"""Local HTTP proxy tunnel.

A per-session local TCP forwarder that acts as an HTTP proxy for the browser,
relaying either to a user-configured upstream proxy (with Basic auth injected)
or directly to the destination. This exists because Chromium's `--proxy-server`
cannot carry credentials in the URL — the tunnel injects `Proxy-Authorization`
so authenticated upstream proxies work transparently.

Fully generic: give it an upstream like `http://user:pass@host:port` (or none).
"""
import asyncio
import logging
from typing import Optional, Dict, Tuple
from urllib.parse import unquote, urlsplit


class ProxyTunnel:
    """
    A local TCP forwarder that acts as an HTTP proxy for the browser,
    relaying traffic either to a remote upstream proxy or directly to the
    destination server when no upstream proxy is configured.
    """
    def __init__(self, local_port: int, upstream_proxy: Optional[str] = None):
        self.local_port = local_port
        self.upstream_host: Optional[str] = None
        self.upstream_port: Optional[int] = None
        self.upstream_auth: Optional[str] = None  # Base64 encoded 'user:pass'
        self.set_upstream(upstream_proxy)
        self.server: Optional[asyncio.Server] = None
        self.serve_task: Optional[asyncio.Task] = None
        self.is_running = False

    def set_upstream(self, upstream_proxy: Optional[str]):
        """
        Dynamically update the remote upstream proxy.
        Existing connections will persist with the old upstream;
        new connections will use the new one.
        """
        if not upstream_proxy:
            self.upstream_host = None
            self.upstream_port = None
            self.upstream_auth = None
            return

        try:
            from urllib.parse import urlparse
            import base64
            # Ensure we have a scheme for urlparse to work correctly
            proxy_url = upstream_proxy
            if "://" not in proxy_url:
                proxy_url = f"http://{proxy_url}"

            parsed = urlparse(proxy_url)
            self.upstream_host = parsed.hostname
            self.upstream_port = parsed.port or 80

            if parsed.username is not None or parsed.password is not None:
                auth_str = f"{unquote(parsed.username or '')}:{unquote(parsed.password or '')}"
                self.upstream_auth = base64.b64encode(auth_str.encode()).decode()
            else:
                self.upstream_auth = None

            if not self.upstream_host:
                logging.error(f"Could not parse hostname from upstream proxy '{upstream_proxy}'")
                return

            logging.info(f"Tunnel {self.local_port} set upstream to {self.upstream_host}:{self.upstream_port} (Auth: {bool(self.upstream_auth)})")
        except Exception as e:
            logging.error(f"Error parsing upstream proxy '{upstream_proxy}': {e}")
            self.upstream_host = None
            self.upstream_port = None

    async def _pipe(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while not reader.at_eof():
                data = await reader.read(4096 * 8)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _read_initial_request(self, reader: asyncio.StreamReader) -> bytes:
        try:
            return await reader.readuntil(b"\r\n\r\n")
        except asyncio.IncompleteReadError as exc:
            return exc.partial
        except asyncio.LimitOverrunError:
            logging.error(f"Proxy tunnel error (local port {self.local_port}): request headers exceed limit.")
            return b""

    @staticmethod
    def _parse_authority(authority: str, default_port: int) -> Tuple[Optional[str], Optional[int]]:
        if not authority:
            return None, None

        parsed = urlsplit(authority if "://" in authority else f"//{authority}")
        try:
            port = parsed.port or default_port
        except ValueError:
            return None, None
        return parsed.hostname, port

    @staticmethod
    def _extract_host_header(header_block: bytes) -> Optional[str]:
        lines = header_block.split(b"\r\n")
        for line in lines[1:]:
            if line.lower().startswith(b"host:"):
                return line.split(b":", 1)[1].strip().decode(errors="ignore")
        return None

    def _build_direct_http_request(self, header_block: bytes) -> Tuple[Optional[str], Optional[int], Optional[bytes]]:
        lines = header_block.split(b"\r\n")
        if not lines or not lines[0]:
            return None, None, None

        try:
            method, target, version = lines[0].decode(errors="ignore").split(" ", 2)
        except ValueError:
            return None, None, None

        host = None
        port = None
        request_target = target

        if "://" in target:
            parsed = urlsplit(target)
            host = parsed.hostname
            if parsed.scheme == "https":
                port = parsed.port or 443
            else:
                port = parsed.port or 80

            request_target = parsed.path or "/"
            if parsed.query:
                request_target = f"{request_target}?{parsed.query}"
        else:
            host_header = self._extract_host_header(header_block)
            host, port = self._parse_authority(host_header or "", 80)

        if not host or not port:
            return None, None, None

        rebuilt_lines = [f"{method} {request_target} {version}".encode()]
        rebuilt_lines.extend(lines[1:])
        return host, port, b"\r\n".join(rebuilt_lines)

    async def _open_direct_connection(self, method: bytes, first_line: bytes, initial_data: bytes):
        if method == b"CONNECT":
            try:
                _, authority, _ = first_line.decode(errors="ignore").split(" ", 2)
            except ValueError:
                return None, None, None, None

            target_host, target_port = self._parse_authority(authority, 443)
            if not target_host or not target_port:
                return None, None, None, None

            upstream_reader, upstream_writer = await asyncio.open_connection(target_host, target_port)
            return upstream_reader, upstream_writer, target_host, target_port

        target_host, target_port, request_data = self._build_direct_http_request(initial_data)
        if not target_host or not target_port or request_data is None:
            return None, None, None, None

        upstream_reader, upstream_writer = await asyncio.open_connection(target_host, target_port)
        upstream_writer.write(request_data)
        await upstream_writer.drain()
        return upstream_reader, upstream_writer, target_host, target_port

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        upstream_reader = None
        upstream_writer = None
        try:
            # 1. Read the initial request from the browser to determine the method
            initial_data = await self._read_initial_request(reader)
            if not initial_data:
                writer.close()
                return

            first_line = initial_data.split(b"\r\n", 1)[0]
            method = first_line.split(b" ", 1)[0].upper()

            # 2. Connect to the upstream proxy
            direct_mode = not self.upstream_host
            if direct_mode:
                upstream_reader, upstream_writer, target_host, target_port = await self._open_direct_connection(
                    method, first_line, initial_data
                )
                if not upstream_reader or not upstream_writer:
                    logging.error(
                        f"Tunnel {self.local_port} could not determine direct target for request: "
                        f"{first_line.decode(errors='ignore')}"
                    )
                    writer.close()
                    return
            else:
                upstream_reader, upstream_writer = await asyncio.open_connection(
                    self.upstream_host, self.upstream_port
                )

            # 3. Handle Authentication according to method
            if direct_mode and method == b"CONNECT":
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()

            elif method == b"CONNECT" and self.upstream_auth:
                # HTTPS Tunneling Handshake: send our own CONNECT to upstream with Auth
                auth_header = f"Proxy-Authorization: Basic {self.upstream_auth}\r\n"
                connect_request = first_line + b"\r\n" + auth_header.encode() + b"\r\n"

                upstream_writer.write(connect_request)
                await upstream_writer.drain()

                resp = await upstream_reader.read(4096)
                if b" 200 " not in resp.split(b"\r\n", 1)[0]:
                    logging.error(f"Upstream proxy rejected CONNECT: {resp.decode(errors='ignore')}")
                    writer.write(resp)  # Pass error back to browser
                    await writer.drain()
                    writer.close()
                    return

                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()

            elif self.upstream_auth:
                # Standard HTTP Request Injection: add Proxy-Authorization after the first line
                auth_header = f"Proxy-Authorization: Basic {self.upstream_auth}\r\n"
                parts = initial_data.split(b"\r\n", 1)
                if len(parts) > 1:
                    modified_data = parts[0] + b"\r\n" + auth_header.encode() + parts[1]
                else:
                    modified_data = initial_data + auth_header.encode()

                upstream_writer.write(modified_data)
                await upstream_writer.drain()

                await asyncio.gather(
                    self._pipe(reader, upstream_writer),
                    self._pipe(upstream_reader, writer)
                )
                return

            else:
                # No auth needed, just send the initial chunk and pipe
                if not direct_mode:
                    upstream_writer.write(initial_data)
                    await upstream_writer.drain()

            # Relay the rest of the traffic bi-directionally
            await asyncio.gather(
                self._pipe(reader, upstream_writer),
                self._pipe(upstream_reader, writer)
            )

        except Exception as e:
            logging.error(f"Proxy tunnel error (local port {self.local_port}): {e}")
            if writer:
                try:
                    writer.close()
                except Exception:
                    pass
            if upstream_writer:
                try:
                    upstream_writer.close()
                except Exception:
                    pass

    async def _serve(self):
        try:
            await self.server.serve_forever()
        except asyncio.CancelledError:
            pass
        except RuntimeError as exc:
            if "is closed" not in str(exc):
                raise

    async def start(self):
        if self.is_running:
            return

        try:
            self.server = await asyncio.start_server(self._handle_client, '127.0.0.1', self.local_port)
            self.is_running = True
            self.serve_task = asyncio.create_task(self._serve())
            logging.info(f"Local tunnel started on 127.0.0.1:{self.local_port}")
        except Exception as e:
            logging.error(f"Failed to start local tunnel on port {self.local_port}: {e}")
            raise

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            if self.serve_task:
                self.serve_task.cancel()
                try:
                    await self.serve_task
                except asyncio.CancelledError:
                    pass
                self.serve_task = None
            self.is_running = False
            logging.info(f"Local tunnel on port {self.local_port} stopped.")


class ProxyManager:
    """
    Manages multiple proxy tunnels, assigning a unique local port to each session.
    """
    def __init__(self, start_port: int = 20000):
        self.next_port = start_port
        self.tunnels: Dict[str, ProxyTunnel] = {}
        self._lock = asyncio.Lock()

    async def get_or_create_tunnel(self, session_id: str, upstream_proxy: Optional[str] = None) -> ProxyTunnel:
        async with self._lock:
            if session_id in self.tunnels:
                tunnel = self.tunnels[session_id]
                tunnel.set_upstream(upstream_proxy)
                return tunnel

            for _ in range(100):
                port = self.next_port
                self.next_port += 1

                tunnel = ProxyTunnel(port, upstream_proxy)
                try:
                    await tunnel.start()
                except OSError:
                    logging.warning(
                        "Local tunnel port %d is unavailable; trying next port",
                        port,
                    )
                    continue

                self.tunnels[session_id] = tunnel
                return tunnel

            raise RuntimeError("No available local proxy tunnel port in allocation window")

    async def stop_tunnel(self, session_id: str):
        if session_id in self.tunnels:
            await self.tunnels[session_id].stop()
            self.tunnels.pop(session_id, None)

    async def stop_all(self):
        for session_id in list(self.tunnels.keys()):
            await self.stop_tunnel(session_id)
