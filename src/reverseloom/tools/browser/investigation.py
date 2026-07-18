import base64
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import List, Any, Dict, Optional, Tuple, Literal
from urllib.parse import urlparse

from langchain_core.tools import tool
import tldextract
from pydantic import Field



from reverseloom.browser.browser_manager import browser_manager
from graphloom import StandardThoughtInput
from graphloom.util.session_store import session_store

_TLD_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())



def _raw_lines(source: str) -> List[str]:
    return source.splitlines() or [source]


def _line_col_to_offset(source: str, line_number: int, column_number: int) -> int:
    if not source:
        return 0
    parts = source.splitlines(keepends=True)
    if not parts:
        return min(max(0, column_number), len(source))

    safe_line = max(0, min(line_number, len(parts) - 1))
    offset = sum(len(part) for part in parts[:safe_line])
    line_text = parts[safe_line]
    safe_col = max(0, min(column_number, len(line_text)))
    return min(offset + safe_col, len(source))


def _offset_to_line_col(source: str, offset: int) -> Tuple[int, int]:
    if not source:
        return 0, 0
    safe_offset = max(0, min(offset, len(source)))
    line_number = source.count("\n", 0, safe_offset)
    last_newline = source.rfind("\n", 0, safe_offset)
    column_number = safe_offset - (last_newline + 1)
    return line_number, column_number


def _render_raw_offset_excerpt(
    source: str,
    *,
    offset: Optional[int] = None,
    line_number: Optional[int] = None,
    column_number: Optional[int] = None,
    radius: int = 2400,
    marker: str = "\n<=== BREAKPOINT HERE ===>\n"
) -> str:
    if offset is None:
        if line_number is None or column_number is None:
            return ""
        offset = _line_col_to_offset(source, line_number, column_number)

    start = max(0, offset - radius)
    end = min(len(source), offset + radius)
    excerpt = source[start:offset] + marker + source[offset:end]

    if line_number is not None and column_number is not None:
        return (
            f"raw_offset[{offset}] chars[{start}:{end}] around raw[{line_number}:{column_number}]:\n"
            f"{excerpt}"
        )
    return f"raw_offset[{offset}] chars[{start}:{end}]:\n{excerpt}"


def _render_keyword_context_with_breakpoints(
    source: str,
    match: re.Match,
    breakpoint_locations: List[Dict[str, Any]],
    *,
    radius: int = 2400,
    max_pretty_lines: int = 180,
) -> Tuple[str, List[Dict[str, int]]]:
    match_offset = match.start()
    match_line, match_col = _offset_to_line_col(source, match_offset)
    start = max(0, match_offset - radius)
    end = min(len(source), match_offset + radius)

    candidates: List[Tuple[int, int, int, int]] = []
    for loc in breakpoint_locations:
        line_number = int(loc.get("lineNumber", 0) or 0)
        column_number = int(loc.get("columnNumber", 0) or 0)
        offset = _line_col_to_offset(source, line_number, column_number)
        if start <= offset <= end:
            candidates.append((offset, abs(offset - match_offset), line_number, column_number))

    candidates.sort(key=lambda item: item[0])
    breakpoints = [
        {"lineNumber": line_number, "columnNumber": column_number}
        for _, _, line_number, column_number in candidates
    ]

    segments = _attach_breakpoints_to_segments(
        _beautified_js_segments(source, start, end, breakpoints),
        source,
        start,
        end,
        breakpoints,
    )
    if len(segments) > max_pretty_lines:
        keyword_idx = next(
            (
                i for i, segment in enumerate(segments)
                if segment.get("start", start) <= match_offset <= segment.get("end", end)
            ),
            len(segments) // 2,
        )
        half = max_pretty_lines // 2
        first = max(0, keyword_idx - half)
        last = min(len(segments), first + max_pretty_lines)
        first = max(0, last - max_pretty_lines)
        segments = segments[first:last]

    rendered_lines = [
        "Pretty breakable context (keyword +/-2400 chars)",
        "Left prefix = raw CDP line,column. Use only rows marked '*' with set_line_breakpoint; '--------' rows are context only.",
        f"Keyword raw location: {match_line},{match_col}",
        "",
    ]
    rendered_bp_offsets = set()
    for segment in segments:
        segment_start = int(segment.get("start", start))
        segment_end = int(segment.get("end", end))
        breakpoints_on_line = list(segment.get("breakpoints") or [])
        code = str(segment.get("text") or "")
        if segment_start <= match_offset <= segment_end:
            code += "  // KEYWORD MATCH"

        if breakpoints_on_line:
            # Use only the first (lowest-column) breakpoint position for display.
            # Remaining positions on the same pretty line are collapsed into a suffix.
            primary_bp = breakpoints_on_line[0]
            line_number = int(primary_bp["lineNumber"])
            column_number = int(primary_bp["columnNumber"])
            prefix = f"{line_number},{column_number}".ljust(12)
            if len(breakpoints_on_line) > 1:
                extra_cols = ",".join(
                    str(int(bp["columnNumber"])) for bp in breakpoints_on_line[1:]
                )
                suffix = f"{code}  // +{len(breakpoints_on_line) - 1} alt col: {extra_cols}"
            else:
                suffix = code
            rendered_lines.append(f"{prefix}* {suffix}")
            for bp in breakpoints_on_line:
                rendered_bp_offsets.add(
                    _line_col_to_offset(source, int(bp["lineNumber"]), int(bp["columnNumber"]))
                )
        else:
            rendered_lines.append(f"{'--------'.ljust(12)}  {code}")

    hidden_breakpoints = len(breakpoints) - len(rendered_bp_offsets)
    if hidden_breakpoints > 0:
        rendered_lines.append(f"... {hidden_breakpoints} V8-confirmed breakpoint(s) omitted by pretty context line cap ...")

    return (
        "\n".join(rendered_lines),
        breakpoints,
    )


_BP_MARKER_RE = re.compile(r"/\*__REVERSELOOM_BP_(\d+)_(\d+)__\*/")


def _beautified_js_segments(
    source: str,
    start: int,
    end: int,
    breakpoints: List[Dict[str, int]],
) -> List[Dict[str, Any]]:
    """Beautify a JS excerpt and carry raw V8 breakpoint coordinates via temporary comments."""
    excerpt = source[start:end]
    inserts: List[Tuple[int, str]] = []
    for bp in breakpoints:
        line_number = int(bp["lineNumber"])
        column_number = int(bp["columnNumber"])
        offset = _line_col_to_offset(source, line_number, column_number)
        if start <= offset <= end:
            inserts.append((offset - start, f"/*__REVERSELOOM_BP_{line_number}_{column_number}__*/"))

    marked = excerpt
    for relative_offset, marker in sorted(inserts, key=lambda item: item[0], reverse=True):
        safe_offset = max(0, min(relative_offset, len(marked)))
        marked = marked[:safe_offset] + marker + marked[safe_offset:]

    try:
        import jsbeautifier

        options = jsbeautifier.default_options()
        options.indent_size = 2
        options.preserve_newlines = True
        options.max_preserve_newlines = 2
        beautified = jsbeautifier.beautify(marked, options)
    except Exception as e:
        logging.debug("jsbeautifier failed, falling back to lightweight pretty printer: %s", e)
        return _pretty_js_segments(source, start, end)

    segments: List[Dict[str, Any]] = []
    search_from = start
    for raw_line in beautified.splitlines():
        matches = list(_BP_MARKER_RE.finditer(raw_line))
        clean_line = _BP_MARKER_RE.sub("", raw_line).rstrip()
        if not clean_line.strip():
            continue

        stripped = clean_line.strip()
        raw_pos = source.find(stripped, search_from, end)
        if raw_pos == -1:
            raw_pos = search_from
        else:
            search_from = min(end, raw_pos + len(stripped))

        segments.append({
            "start": raw_pos,
            "end": min(end, raw_pos + len(stripped)),
            "text": clean_line,
            "breakpoints": [
                {"lineNumber": int(match.group(1)), "columnNumber": int(match.group(2))}
                for match in matches
            ],
        })

    return segments or _pretty_js_segments(source, start, end)


def _attach_breakpoints_to_segments(
    segments: List[Dict[str, Any]],
    source: str,
    start: int,
    end: int,
    breakpoints: List[Dict[str, int]],
) -> List[Dict[str, Any]]:
    attached = {
        (int(bp.get("lineNumber", 0)), int(bp.get("columnNumber", 0)))
        for segment in segments
        for bp in (segment.get("breakpoints") or [])
    }

    for bp in breakpoints:
        line_number = int(bp["lineNumber"])
        column_number = int(bp["columnNumber"])
        key = (line_number, column_number)
        if key in attached:
            continue

        offset = _line_col_to_offset(source, line_number, column_number)
        target = next(
            (
                segment for segment in segments
                if int(segment.get("start", start)) <= offset <= int(segment.get("end", end))
            ),
            None,
        )
        if target is None:
            target = _raw_breakpoint_segment(source, start, end, offset)
            segments.append(target)

        target_breakpoints = target.setdefault("breakpoints", [])
        target_breakpoints.append({"lineNumber": line_number, "columnNumber": column_number})
        attached.add(key)

    return sorted(segments, key=lambda segment: int(segment.get("start", start)))


def _raw_breakpoint_segment(source: str, start: int, end: int, offset: int) -> Dict[str, Any]:
    left_bound = max(start, source.rfind(";", start, offset), source.rfind("{", start, offset), source.rfind("}", start, offset))
    if left_bound < offset:
        left_bound += 1
    else:
        left_bound = max(start, offset - 120)

    right_candidates = [
        pos for pos in (
            source.find(";", offset, end),
            source.find("{", offset, end),
            source.find("}", offset, end),
        )
        if pos != -1
    ]
    right_bound = min(right_candidates) + 1 if right_candidates else min(end, offset + 240)
    text = source[left_bound:right_bound].strip() or source[offset:min(end, offset + 120)].strip()

    return {
        "start": left_bound,
        "end": right_bound,
        "text": text,
        "breakpoints": [],
    }


def _append_pretty_segment(
    segments: List[Dict[str, Any]],
    raw_text: str,
    raw_start: Optional[int],
    indent: int,
) -> None:
    if raw_start is None:
        return
    stripped = raw_text.strip()
    if not stripped:
        return
    leading = len(raw_text) - len(raw_text.lstrip())
    trailing = len(raw_text.rstrip())
    segments.append({
        "start": raw_start + leading,
        "end": raw_start + trailing,
        "indent": max(0, indent),
        "text": stripped,
    })


def _pretty_js_segments(source: str, start: int, end: int) -> List[Dict[str, Any]]:
    """Lightweight JS pretty printer that preserves raw offsets for display."""
    segments: List[Dict[str, Any]] = []
    buf: List[str] = []
    buf_start: Optional[int] = None
    indent = 0
    quote = ""
    escaped = False

    def append_char(idx: int, ch: str) -> None:
        nonlocal buf_start
        if buf_start is None:
            buf_start = idx
        buf.append(ch)

    def flush(current_indent: Optional[int] = None) -> None:
        nonlocal buf, buf_start
        _append_pretty_segment(
            segments,
            "".join(buf),
            buf_start,
            indent if current_indent is None else current_indent,
        )
        buf = []
        buf_start = None

    i = start
    while i < end:
        ch = source[i]

        if quote:
            append_char(i, ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            i += 1
            continue

        if ch in ("'", '"', "`"):
            quote = ch
            append_char(i, ch)
        elif ch == "{":
            append_char(i, ch)
            flush(indent)
            indent += 1
        elif ch == "}":
            flush(indent)
            indent = max(0, indent - 1)
            append_char(i, ch)
            flush(indent)
        elif ch == ";":
            append_char(i, ch)
            flush(indent)
        elif ch == ":":
            append_char(i, ch)
            if "".join(buf).strip().startswith(("case ", "default")):
                flush(indent)
        else:
            append_char(i, ch)
        i += 1

    flush(indent)
    return segments


async def _extract_source_matches(
    pattern: re.Pattern,
    source_text: str,
    url_display: str,
    script_id_display: str,
    *,
    cdp: Any = None,
    max_matches: int = 8,
) -> List[str]:
    results = []
    if not source_text:
        return results
    seen_locations = set()
    for match in pattern.finditer(source_text):
        offset = match.start()
        line_number = source_text.count('\n', 0, offset)
        last_newline = source_text.rfind('\n', 0, offset)
        column_number = offset - (last_newline + 1)
        location_key = (line_number, column_number, match.group(0))
        if location_key in seen_locations:
            continue
        seen_locations.add(location_key)

        possible_breakpoints = await _get_possible_breakpoints_for_match(
            cdp,
            source_text,
            script_id_display,
            match,
        )
        snippet, nearest_breakpoints = _render_keyword_context_with_breakpoints(
            source_text,
            match,
            possible_breakpoints,
        )
        if nearest_breakpoints:
            breakpoint_hint = (
                "Rows marked '*' are V8-confirmed breakpoints. "
                "Call set_line_breakpoint with the raw line,column shown on the left."
            )
        else:
            breakpoint_hint = (
                "No V8-confirmed breakpoint was returned in the keyword +/-2400 character window. "
                "The script may be stale/inactive, or the keyword is not near executable code."
            )
        results.append(
            f"--- Script ID: {script_id_display} | URL: {url_display} ---\n"
            f"Keyword location (0-indexed): line={line_number}, column={column_number}\n"
            f"{breakpoint_hint}\n"
            f"Note: rows with '--------' are pretty context only and must not be used for set_line_breakpoint.\n"
            f"{snippet}\n"
        )
        if len(results) >= max_matches:
            break
    return results


async def _resolve_script_source(
    session: Any,
    cdp: Any,
    script_id: str,
    url: str = "",
) -> str:
    meta = getattr(session, "script_registry", {}).get(script_id, {}) if session else {}
    return str(meta.get("source") or "")


async def _get_possible_breakpoints_for_match(
    cdp: Any,
    source: str,
    script_id: str,
    match: re.Match,
    *,
    radius: int = 2400,
) -> List[Dict[str, Any]]:
    if not cdp or not script_id or not source:
        return []

    match_offset = match.start()
    start_offset = max(0, match_offset - radius)
    end_offset = min(len(source), match_offset + radius)
    start_line, start_col = _offset_to_line_col(source, start_offset)
    end_line, end_col = _offset_to_line_col(source, end_offset)

    try:
        res = await cdp.send("Debugger.getPossibleBreakpoints", {
            "start": {
                "scriptId": script_id,
                "lineNumber": start_line,
                "columnNumber": start_col,
            },
            "end": {
                "scriptId": script_id,
                "lineNumber": end_line,
                "columnNumber": end_col,
            },
            "restrictToFunction": False,
        })
        return list(res.get("locations") or [])
    except Exception as e:
        logging.debug("Debugger.getPossibleBreakpoints failed for script %s: %s", script_id, e)
        return []


async def _clear_xhr_breakpoints(handler: Any, cdp: Any) -> int:
    """Clear XHR breakpoints previously armed on this tab's CDP session.
    `handler` must be the CdpHandler for the SAME target `cdp` talks to —
    the patterns list lives on that handler, not globally on the session."""
    patterns = list(getattr(handler, "xhr_breakpoint_patterns", []) or [])
    cleared = 0
    for pattern in patterns:
        try:
            await cdp.send("DOMDebugger.removeXHRBreakpoint", {"url": pattern})
            cleared += 1
        except Exception as e:
            logging.warning("Failed to remove XHR breakpoint pattern=%s: %s", pattern, e)
    if handler is not None:
        handler.xhr_breakpoint_patterns = []
    return cleared


def _response_body_bytes(entry: Dict[str, Any]) -> bytes:
    body = entry.get("responseBody", "")
    if entry.get("responseBodyIsBase64"):
        try:
            return base64.b64decode(body)
        except Exception:
            return b""
    if isinstance(body, bytes):
        return body
    return str(body or "").encode("utf-8", errors="ignore")


def _looks_like_wasm(entry: Dict[str, Any]) -> bool:
    mime_type = str(entry.get("mimeType") or "").lower()
    url = str(entry.get("url") or "").lower()
    body_bytes = _response_body_bytes(entry)
    return (
        "application/wasm" in mime_type
        or url.split("?", 1)[0].endswith(".wasm")
        or body_bytes.startswith(b"\x00asm")
    )


def _safe_json_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _decoded_response_text(entry: Dict[str, Any]) -> str:
    body = entry.get("responseBody", "")
    if not body:
        return ""
    if entry.get("responseBodyIsBase64"):
        if _looks_like_wasm(entry):
            return ""
        try:
            return _response_body_bytes(entry).decode("utf-8", errors="ignore")
        except Exception:
            return ""
    return str(body)


def _network_search_fields(entry: Dict[str, Any]) -> List[Tuple[str, str]]:
    url = str(entry.get("url") or "")
    parsed_url = urlparse(url)
    headers = entry.get("headers") or {}
    response_headers = entry.get("response_headers") or {}
    cookie_header = ""
    set_cookie_header = ""
    if isinstance(headers, dict):
        cookie_header = "\n".join(str(v) for k, v in headers.items() if k.lower() == "cookie")
    if isinstance(response_headers, dict):
        set_cookie_header = "\n".join(str(v) for k, v in response_headers.items() if k.lower() == "set-cookie")

    fields = [
        ("Request URL", url),
        ("Request Metadata", _safe_json_text({
            "method": entry.get("method"),
            "type": entry.get("type"),
            "status": entry.get("status"),
            "mimeType": entry.get("mimeType"),
            "requestId": entry.get("requestId"),
        })),
        ("Query String", parsed_url.query),
        ("Request Headers", _safe_json_text(headers)),
        ("Request Cookies", cookie_header),
        ("Response Headers", _safe_json_text(response_headers)),
        ("Response Cookies", set_cookie_header),
        ("Request Payload", str(entry.get("postData") or "")),
        ("Response Body", _decoded_response_text(entry)),
        ("Initiator Stack", _safe_json_text(entry.get("initiator_stack") or [])),
    ]

    # Full record is a fallback so future metadata fields are still searchable.
    fields.append(("Full Network Record", _safe_json_text(entry)))
    return [(name, text) for name, text in fields if text]


def _response_set_cookie_values(response_headers: Any) -> List[str]:
    if not isinstance(response_headers, dict):
        return []

    values: List[str] = []
    for key, value in response_headers.items():
        if str(key).lower() != "set-cookie":
            continue
        if isinstance(value, list):
            raw_values = value
        else:
            raw_values = str(value or "").splitlines()
        values.extend(str(item).strip() for item in raw_values if str(item).strip())
    return values


def _format_set_cookie_preview(response_headers: Any, *, max_items: int = 5, value_prefix: int = 24) -> str:
    previews: List[str] = []
    for raw_cookie in _response_set_cookie_values(response_headers):
        cookie_pair = raw_cookie.split(";", 1)[0].strip()
        if not cookie_pair or "=" not in cookie_pair:
            continue
        name, value = cookie_pair.split("=", 1)
        value_preview = value[:value_prefix]
        if len(value) > value_prefix:
            value_preview += "..."
        previews.append(f"{name}={value_preview}")
        if len(previews) >= max_items:
            break
    return "; ".join(previews)


def _network_log_fingerprint(entry: Dict[str, Any]) -> str:
    excluded = {"requestId", "load_time", "navigation_epoch", "loaderId", "frameId"}
    comparable = {
        k: v for k, v in (entry or {}).items()
        if k not in excluded
    }
    return hashlib.sha256(
        json.dumps(comparable, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8", errors="ignore")
    ).hexdigest()


def _format_load_time(value: Any) -> str:
    try:
        ts = float(value)
    except Exception:
        return ""
    if ts <= 0:
        return ""
    dt = datetime.fromtimestamp(ts, timezone(timedelta(hours=8)))
    return dt.strftime("%H:%M:%S.%f")


def _is_same_site(host_a: str, host_b: str) -> bool:
    """Check if two hostnames share the same registered domain (eTLD+1)."""
    a = _TLD_EXTRACT(host_a or "")
    b = _TLD_EXTRACT(host_b or "")
    domain_a = a.top_domain_under_public_suffix
    domain_b = b.top_domain_under_public_suffix
    return bool(domain_a and domain_a == domain_b)


def _iter_tab_scopes(session: Any, tab_index: Optional[int]) -> List[Tuple[int, Any, Any]]:
    """Return the list of (tab_index, page, handler) to search across.

    - tab_index=None  → every tab in the context that has an attached handler
      (i.e. every tab whose CdpHandler has been wired up — which is every
      page tab under the current autoAttach setup).
    - tab_index=<int> → just that tab, or empty list if the index is invalid
      or the tab has no attached handler yet.

    Tabs are yielded in context-page order so the output matches the
    observer's "Open Tabs Summary" indices (`browser_snapshot` uses the same
    enumeration).
    """
    if session is None:
        return []
    context = getattr(session, "context", None)
    pages = list(context.pages) if context else []
    handlers: Dict[Any, Any] = getattr(session, "cdp_handlers", {}) or {}

    scopes: List[Tuple[int, Any, Any]] = []
    for i, page in enumerate(pages):
        handler = handlers.get(page)
        if handler is None:
            continue
        if tab_index is not None and i != tab_index:
            continue
        scopes.append((i, page, handler))
    return scopes


def _tab_header(tab_idx: int, page: Any) -> str:
    url = getattr(page, "url", "") or ""
    return f"[Tab {tab_idx}] {url}"


def _resolve_tab(session: Any, tab_index: Optional[int]) -> Tuple[int, Any, Any, Any]:
    """Pick the tab a single-target tool should operate on.

    Returns (tab_idx, page, handler, cdp_session). Raises ValueError with a
    message suitable for returning to the LLM if the resolution fails.

    - tab_index=None  → the agent's currently-active tab (session.page).
      This keeps backward compatibility with tools that used to read
      session.cdp_handler directly.
    - tab_index=<int> → the tab at that index, as long as it has an attached
      CdpHandler. Indices match the observer's "Open Tabs Summary".
    """
    if session is None:
        raise ValueError("Browser session is not initialised.")

    context = getattr(session, "context", None)
    pages = list(context.pages) if context else []
    handlers: Dict[Any, Any] = getattr(session, "cdp_handlers", {}) or {}

    if tab_index is None:
        active = getattr(session, "page", None)
        if active is None:
            raise ValueError("No active page on this session.")
        try:
            idx = pages.index(active)
        except ValueError:
            idx = -1
        handler = handlers.get(active)
        if handler is None:
            raise ValueError(
                "Active tab has no attached CdpHandler yet — the page may still be initialising."
            )
        return idx, active, handler, handler.cdp_session

    if tab_index < 0 or tab_index >= len(pages):
        raise ValueError(
            f"tab_index={tab_index} is out of range; {len(pages)} tab(s) open "
            f"(valid indices 0-{max(0, len(pages) - 1)})."
        )
    page = pages[tab_index]
    handler = handlers.get(page)
    if handler is None:
        raise ValueError(
            f"tab_index={tab_index} has no attached CdpHandler yet — try again after the tab finishes loading."
        )
    return tab_index, page, handler, handler.cdp_session


# --- Network Recon & Analysis Tools ---

class NetworkSearchInput(StandardThoughtInput):
    filter_keyword: str = Field(default="", description="Optional: deep keyword search across the full request record (URL/Body/Headers). If empty, the most recent network requests will be displayed.")
    resource_types: List[Literal["All", "Fetch", "XHR", "Document", "Script", "Image", "Media", "WebSocket", "Other"]] = Field(
        default=["Fetch", "XHR"],
        description=(
            "Filter by resource type. "
            "Valid values are enforced by the enum constraint."
        )
    )
    status_filter: List[int] = Field(
        default=[],
        description="Optional HTTP status filter. Empty means all statuses.",
    )
    same_origin_only: bool = Field(
        default=True,
        description="Only show requests from the same registered domain as the tab they belong to.",
    )
    search_historical: bool = Field(
        default=False,
        description="If False, only search requests from each tab's current navigation epoch.",
    )
    tab_index: Optional[int] = Field(
        default=None,
        description=(
            "0-based tab index to restrict the search to a single tab (matches the Open Tabs Summary in the observer). "
            "Omit / null → search across ALL tabs in the session (default). "
            "Results always label the owning tab; `inspect_network_request` only inspects the active tab, so switch_tab "
            "to the labelled tab before looking up a returned requestId."
        ),
    )


@tool("search_in_network_payloads", args_schema=NetworkSearchInput)
async def search_in_network_payloads(
    filter_keyword: str = "",
    resource_types: List[str] = None,
    status_filter: Optional[List[int]] = None,
    same_origin_only: bool = True,
    search_historical: bool = False,
    tab_index: Optional[int] = None,
    **kwargs,
) -> str:
    """
    Network reconnaissance tool: List captured data API requests from the browser.
    Returns recent matching requests, deduplicating only fully identical captured records.
    After finding a target requestId, t` to view full details
    (switch_tab first if the result came from another tab — requestIds are per-target).

    WHEN TO USE:
    - To find ANY text data returned by the server (JSON APIs, HTML DOM structures, or raw Text payloads).

    Tab scoping: pass `tab_index` to pin the search to a specific tab. When omitted,
    every tab's network log is merged in reverse chronological order and each returned
    request is labelled with its owning tab.
    """
    if resource_types is None:
        resource_types = ["Fetch", "XHR"]
    status_filter = list(status_filter or [])
    status_set = {int(s) for s in status_filter}
    display_limit = 30

    session_id = kwargs.get("session_id")
    session = browser_manager.get_session(session_id)
    from urllib.parse import urlparse

    scopes = _iter_tab_scopes(session, tab_index)
    if not scopes:
        if tab_index is not None:
            return (
                f"tab_index={tab_index} is invalid or has no attached CDP handler yet. "
                "Check the Open Tabs Summary in the observer."
            )
        return "No tabs with attached CDP handlers are available to search."

    is_all_types = "all" in (t.lower() for t in resource_types) if resource_types else False
    lower_filters = [t.lower() for t in (resource_types or [])]

    # Gather candidate requests across scopes, tagged with their tab of origin.
    # Each entry: (load_time, tab_idx, page, handler, req)
    pending: List[Tuple[float, int, Any, Any, Dict[str, Any]]] = []
    for tab_idx, page, handler in scopes:
        tab_epoch = getattr(handler, "navigation_epoch", None)
        logs = getattr(handler, "network_logs", []) or []
        page_host = urlparse(getattr(page, "url", "") or "").hostname or ""

        for req in logs:
            req_type = str(req.get("type") or "")

            if not search_historical and tab_epoch is not None:
                if req.get("navigation_epoch") != tab_epoch:
                    continue

            # Type filtering
            if not is_all_types and resource_types:
                if req_type == "navigation_event":
                    if "Document" not in resource_types and "all" not in lower_filters:
                        continue
                else:
                    if req_type.lower() not in lower_filters:
                        continue

            url = req.get("url", "")
            req_host = urlparse(url).hostname or ""
            if same_origin_only and page_host and not _is_same_site(req_host, page_host):
                continue

            if status_set:
                try:
                    req_status = int(req.get("status"))
                except Exception:
                    continue
                if req_status not in status_set:
                    continue

            # Keyword filtering across the entire captured request record.
            if filter_keyword:
                pattern = re.compile(re.escape(filter_keyword), re.IGNORECASE)
                if not any(pattern.search(field_text) for _, field_text in _network_search_fields(req)):
                    continue

            try:
                load_time = float(req.get("load_time") or 0.0)
            except Exception:
                load_time = 0.0
            pending.append((load_time, tab_idx, page, handler, req))

    # Sort globally by load_time desc so the most recent across all tabs comes first.
    pending.sort(key=lambda t: t[0], reverse=True)

    selected: List[Tuple[int, Any, Any, Dict[str, Any]]] = []
    seen_fingerprints: set = set()
    for _, tab_idx, page, handler, req in pending:
        fingerprint = _network_log_fingerprint(req)
        if fingerprint in seen_fingerprints:
            continue
        seen_fingerprints.add(fingerprint)
        selected.append((tab_idx, page, handler, req))
        if len(selected) >= display_limit:
            break

    # --- Flatten Extractor ---
    def _extract_flat_samples(obj, prefix="", current_depth=1, max_depth=8, result_map=None) -> dict:
        if result_map is None:
            result_map = {}
        if current_depth > max_depth or len(result_map) > 50:
            return result_map

        if isinstance(obj, dict):
            for k, v in obj.items():
                full_k = f"{prefix}{k}" if prefix else str(k)
                _extract_flat_samples(v, f"{full_k}.", current_depth + 1, max_depth, result_map)
        elif isinstance(obj, list) and obj:
            # Check if list of primitives
            if all(not isinstance(i, (dict, list)) for i in obj[:20]):
                val_str = str(obj)
                full_k = prefix.rstrip('.') if prefix else "array"
                if full_k not in result_map:
                    result_map[full_k] = val_str[:500] + "..." if len(val_str) > 500 else val_str
            else:
                for item in obj[:2]:  # Sample first 2 items
                    _extract_flat_samples(item, f"{prefix}[].", current_depth + 1, max_depth, result_map)
        else:
            # Leaf node
            full_k = prefix.rstrip('.') if prefix else "value"
            if full_k not in result_map:
                val_str = str(obj)
                result_map[full_k] = val_str[:500] + "..." if len(val_str) > 500 else val_str
        return result_map

    formatted_results: List[str] = []
    for tab_idx, page, handler, req in selected:
        url = req.get("url", "")
        method = req.get("method", "")

        # Extract payload samples (Keep Query and Body separate like DevTools)
        query_samples = {}
        payload_samples = {}
        post_data = req.get("postData", "")
        parsed_url = urlparse(url)

        if parsed_url.query:
            from urllib.parse import parse_qs
            q_dict = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed_url.query).items()}
            query_samples = _extract_flat_samples(q_dict, max_depth=2)

        if post_data:
            try:
                parsed_post = json.loads(post_data) if isinstance(post_data, str) else post_data
                payload_samples = _extract_flat_samples(parsed_post, max_depth=5)
            except Exception:
                payload_samples = {"raw_payload": str(post_data)[:80] + "..." if len(str(post_data)) > 80 else str(post_data)}

        # Extract response sample
        response_samples = {}
        response_text = _decoded_response_text(req)
        if response_text:
            try:
                parsed_res = json.loads(response_text) if isinstance(response_text, str) else response_text
                response_samples = _extract_flat_samples(parsed_res)
            except Exception:
                response_samples = {"raw_response": str(response_text)[:80] + "..." if len(str(response_text)) > 80 else str(response_text)}

        # Format URL cleanly
        display_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"
        if parsed_url.query:
            display_url += "?..."

        if req.get("type") == "navigation_event":
            method = "[NAV]"
            display_url = url[:150]

        load_time = _format_load_time(req.get("load_time"))
        time_prefix = f"[{load_time}] " if load_time else ""
        tab_tag = f"[Tab {tab_idx}] "
        summary_block = (
            f"[{len(formatted_results) + 1}] {tab_tag}{time_prefix}RequestId: {req.get('requestId')}\n"
            f"    Request: {method} {display_url}\n"
            f"    Response Status: {req.get('status')} | Type: {req.get('type')}\n"
        )
        set_cookie_preview = _format_set_cookie_preview(req.get("response_headers") or {})
        if set_cookie_preview:
            summary_block += f"    Response Set-Cookie: {set_cookie_preview}\n"
        if query_samples:
            summary_block += f"    Query String: {json.dumps(query_samples, ensure_ascii=False)}\n"
        if payload_samples:
            summary_block += f"    Request Payload: {json.dumps(payload_samples, ensure_ascii=False)}\n"
        if response_samples:
            summary_block += f"    Response Preview: {json.dumps(response_samples, ensure_ascii=False)}\n"

        # --- Keyword Context Extraction ---
        if filter_keyword:
            pattern = re.compile(re.escape(filter_keyword), re.IGNORECASE)
            contexts_added = 0
            for field_name, field_text in _network_search_fields(req):
                if field_name == "Full Network Record" and contexts_added:
                    continue
                match = pattern.search(field_text)
                if not match:
                    continue
                marker = f"\n<=== KEYWORD MATCH {match.group(0)!r} ===>\n"
                snippet = _render_raw_offset_excerpt(field_text, offset=match.start(), marker=marker)
                summary_block += f"\n    [Keyword Context in {field_name}]:\n    {snippet.replace(chr(10), chr(10) + '    ')}\n"
                contexts_added += 1
                if contexts_added >= 6:
                    break

        # --- Code Search Bridge: Auto-resolve V8 coordinates if it's JS ---
        # scriptIds are scoped per target, so look them up in THIS request's
        # owning tab handler — never in session.cdp_handler (which is the
        # agent's currently-active tab and may be a different target).
        mime = str(req.get("mimeType", "")).lower()
        is_js = "javascript" in mime or "ecmascript" in mime or req.get("type") == "Script" or url.lower().endswith(".js")
        if is_js and filter_keyword and response_text and handler is not None:
            script_ids = handler.url_to_script_ids.get(url, [])
            if script_ids:
                script_id = script_ids[-1]  # Get the latest scriptId for this URL
                tab_cdp = getattr(handler, "cdp_session", None)
                try:
                    pattern = re.compile(re.escape(filter_keyword), re.MULTILINE | re.IGNORECASE)
                    matches = await _extract_source_matches(
                        pattern,
                        response_text,
                        url,
                        script_id,
                        cdp=tab_cdp,
                        max_matches=1,
                    )
                    if matches:
                        summary_block += f"\n    [V8 Breakpoint Coordinates]\n    {matches[0].replace(chr(10), chr(10)+'    ')}\n"
                except Exception:
                    pass

        formatted_results.append(summary_block)

    if not formatted_results:
        scope_desc = (
            f"tab_index={tab_index}" if tab_index is not None else f"{len(scopes)} tab(s)"
        )
        return f"No matching network requests found across {scope_desc}."

    scope_desc = (
        f"single tab (tab_index={tab_index})"
        if tab_index is not None
        else f"all {len(scopes)} attached tab(s); each row tagged with its owning [Tab N]"
    )
    intro = (
        f"Captured latest {len(formatted_results)} matching requests from {scope_desc} "
        f"(resource_types={resource_types}, status_filter={status_filter or 'ALL'}, "
        f"same_origin_only={same_origin_only}, search_historical={search_historical}, "
        f"full-record dedupe enabled):\n"
        f"{'-'*80}\n"
    )
    return intro + "\n".join(formatted_results)


class InspectNetworkInput(StandardThoughtInput):
    """Input for viewing specific request details."""
    request_id: str = Field(
        description="The requestIds of the API endpoints"
    )
    tab_index: Optional[int] = Field(
        default=None,
        description=(
            "0-based tab index to look up the request in. RequestIds are scoped per target, "
            "so pass the [Tab N] from search_in_network_payloads. Omit / null → current active tab."
        ),
    )


@tool("inspect_network_request", args_schema=InspectNetworkInput)
async def inspect_network_request(
    request_id: str,
    tab_index: Optional[int] = None,
    **kwargs,
) -> str:
    """
    Deep inspection tool: View complete details of requests by requestId, including Headers, Payload, Initiator Call Stack, and Response.
    Use this to deeply analyze the request structure before setting breakpoints or writing reproduction code.

    Tab scoping: pass `tab_index` when the requestId came from `search_in_network_payloads`
    with a `[Tab N]` tag other than the active one; requestIds are not global.
    """
    session_id = kwargs.get("session_id")
    session = browser_manager.get_session(session_id)
    try:
        tab_idx, page, handler, _cdp = _resolve_tab(session, tab_index)
    except ValueError as exc:
        return f"Failed to inspect: {exc}"
    logs = getattr(handler, "network_logs", []) or []

    results = []
    target = next((req for req in logs if req.get("requestId") == request_id), None)
    if not target:
        results.append(
            f"Request with requestId {request_id} not found in {_tab_header(tab_idx, page)}."
        )
    else:
        results.append(
            f"{_tab_header(tab_idx, page)}\nrequestId: {request_id}\n{_format_network_request_detail(target)}"
        )

    return "\n\n".join(results)


def _format_network_request_detail(target: Dict[str, Any]) -> str:
    url = target.get("url", "")
    method = target.get("method", "")
    mime_type = str(target.get("mimeType") or "")

    # 1. Target Header
    output = f">>> Target: {method} {url}\n"
    load_time = _format_load_time(target.get("load_time"))
    if load_time:
        output += f">>> Load Time: {load_time}\n"
    if mime_type:
        output += f">>> MimeType: {mime_type}\n"
    output += "\n"

    # 2. Query String (Raw)
    from urllib.parse import urlparse
    parsed_url = urlparse(url)
    if parsed_url.query:
        output += "[ Query String (Raw) ]\n"
        output += f"{parsed_url.query}\n\n"

    # 3. Request Payload (Raw)
    post_data = target.get("postData", "")
    if post_data:
        output += "[ Request Payload (Raw) ]\n"
        try:
            parsed_post = json.loads(post_data) if isinstance(post_data, str) else post_data
            output += f"{json.dumps(parsed_post, indent=2, ensure_ascii=False)}\n\n"
        except Exception:
            output += f"{post_data}\n\n"

    # 4. Filtered Request Headers
    headers = target.get("headers", {})
    if headers:
        ignore_prefixes = ("sec-ch-", "sec-fetch-", "accept-encoding", "accept-language", "connection", "content-length")
        filtered_headers = {k: v for k, v in headers.items() if not k.lower().startswith(ignore_prefixes)}
        if filtered_headers:
            output += "[ Filtered Request Headers ]\n"
            for k, v in filtered_headers.items():
                output += f"- {k}: {v}\n"
            output += "\n"

    # 5. Response Headers and Set-Cookie
    response_headers = target.get("response_headers", {}) or {}
    if isinstance(response_headers, dict) and response_headers:
        non_cookie_response_headers = {
            k: v for k, v in response_headers.items()
            if str(k).lower() != "set-cookie"
        }
        if non_cookie_response_headers:
            output += "[ Response Headers ]\n"
            for k, v in non_cookie_response_headers.items():
                output += f"- {k}: {v}\n"
            output += "\n"

    set_cookie_values = _response_set_cookie_values(response_headers)
    if set_cookie_values:
        output += "[ Response Set-Cookie ]\n"
        for cookie in set_cookie_values:
            output += f"- {cookie}\n"
        output += "\n"

    # 6. Initiator Call Stack
    initiator_stack = target.get("initiator_stack", [])
    if initiator_stack:
        output += "[ Initiator Call Stack (Historical Async Stack) ]\n"
        for frame in initiator_stack:
            func_name = frame.get("functionName", "(anonymous)")
            script_id = frame.get("scriptId", "")
            line_number = frame.get("lineNumber", 0)
            col_number = frame.get("columnNumber", 0)

            if func_name.startswith("--- "):
                output += f"{func_name}\n"
            else:
                # {frame_url}
                output += f"#{frame.get('index', 0)} {func_name} at (ScriptID: {script_id} | Line: {line_number} | Col: {col_number})\n"
                # if script_id:
                #     output += f"   -> [Call: set_line_breakpoint(script_id=\"{script_id}\", line_number={line_number}, column_number={col_number})]\n"
        output += "\n"

    # 7. Response Body
    body = target.get("responseBody", "")
    body_is_base64 = bool(target.get("responseBodyIsBase64"))

    output += "[ Response Body ]\n"

    is_binary = False
    if _looks_like_wasm(target):
        is_binary = True
    elif body_is_base64:
        mime = mime_type.lower()
        is_text_mime = "text" in mime or "json" in mime or "xml" in mime or "javascript" in mime or "urlencoded" in mime
        if not is_text_mime and not body.lstrip().startswith(("{", "[")):
            is_binary = True

    if is_binary:
        body_bytes = _response_body_bytes(target)
        file_type = "WASM" if body_bytes.startswith(b"\x00asm") else "Binary"
        output += (
            f"<{file_type} data detected>\n"
            f"mimeType: {mime_type or 'unknown'}\n"
            f"base64Encoded: {body_is_base64}\n"
            f"byte_length: {len(body_bytes)}\n"
            f"magic: {body_bytes[:4].hex() if body_bytes else 'unknown'}\n"
        )
    elif body:
        try:
            parsed_json = json.loads(body)
            output += json.dumps(parsed_json, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            output += str(body)
    else:
        output += "(Empty Response)"

    return output


class SearchJsInput(StandardThoughtInput):
    """Input for searching JS content."""
    keyword: str = Field(description="Search keyword or regex pattern")
    is_regex: bool = Field(default=False, description="Treat keyword as regular expression")
    search_historical: bool = Field(default=False, description="If True, searches ALL scripts from the entire session (including dead/previous pages). Use this for forensic analysis. By default, it only searches active scripts on the current page.")
    tab_index: Optional[int] = Field(
        default=None,
        description=(
            "0-based tab index to restrict the search to a single tab (matches the Open Tabs Summary in the observer). "
            "Omit / null → search across ALL tabs in the session (default). "
            "Results always label the owning tab; if you want to operate further on a returned scriptId "
            "(get_script_source / set_line_breakpoint), first call switch_tab to the labelled tab because "
            "scriptIds are scoped per target."
        ),
    )


@tool("search_in_js_codes", args_schema=SearchJsInput)
async def search_in_js_codes(
    keyword: str,
    is_regex: bool = False,
    search_historical: bool = True,
    tab_index: Optional[int] = None,
    **kwargs,
) -> str:
    """
    Global JS search tool: Mimics DevTools Ctrl+Shift+F (Sources panel).
    Searches captured V8 JavaScript execution code for the keyword.

    WHEN TO USE:
    - To find encryption logic, constants, or variables in JavaScript in order to set breakpoints.
    - It provides the exact `script_id`, `line_number`, and `column_number` required for `set_line_breakpoint`.
    - To find VMP entry/exit points, token assignment code, and generator call sites.
    - When you know a function name or unique string from network analysis and want to
      locate its definition or call site without setting a breakpoint first.

    Tab scoping: pass `tab_index` to pin the search to a specific tab (see Open Tabs Summary).
    When omitted, every tab in the session is searched and results are grouped per tab.
    """
    session_id = str(kwargs.get("session_id", ""))
    session = browser_manager.get_session(session_id)

    scopes = _iter_tab_scopes(session, tab_index)
    if not scopes:
        if tab_index is not None:
            return (
                f"tab_index={tab_index} is invalid or has no attached CDP handler yet. "
                "Check the Open Tabs Summary in the observer."
            )
        return "No tabs with attached CDP handlers are available to search."

    try:
        # Use MULTILINE so ^ and $ match line boundaries just like the old line-by-line behavior
        pattern = re.compile(keyword if is_regex else re.escape(keyword), re.MULTILINE)
    except Exception as e:
        return f"Invalid regex pattern: {str(e)}"

    results: List[str] = []
    max_result_blocks = 30
    # Deduplicate identical scripts across ALL scopes — the same bundle often
    # appears in multiple tabs and we don't want the LLM to wade through dupes.
    seen_hashes: set = set()

    for tab_idx, page, handler in scopes:
        if len(results) >= max_result_blocks:
            break

        active_script_ids = getattr(handler, "active_script_ids", set()) or set()
        script_registry = getattr(handler, "script_registry", {}) or {}
        if search_historical:
            target_script_ids = list(script_registry.keys())
        else:
            target_script_ids = [sid for sid in script_registry.keys() if sid in active_script_ids]

        if not target_script_ids:
            continue

        cdp = getattr(handler, "cdp_session", None)
        tab_header = _tab_header(tab_idx, page)
        tab_scripts_matched: List[str] = []

        for script_id in reversed(target_script_ids):
            if len(results) + len(tab_scripts_matched) >= max_result_blocks:
                break
            meta = script_registry.get(script_id, {})
            url = str(meta.get("url") or "")
            source = str(meta.get("source") or "")
            script_hash = meta.get("hash") or (
                hashlib.sha256(source.encode("utf-8", errors="ignore")).hexdigest() if source else ""
            )

            if script_hash:
                if script_hash in seen_hashes:
                    continue
                seen_hashes.add(script_hash)

            url_match = pattern.search(url) if url else None
            source_match = pattern.search(source) if source else None
            if not url_match and not source_match:
                continue

            if url_match:
                load_time = _format_load_time(meta.get("load_time"))
                time_part = f" | Load Time: {load_time}" if load_time else ""
                tab_scripts_matched.append(
                    f"--- Script ID: {script_id}{time_part} | URL metadata match ---\n"
                    f"URL: {url}\n"
                    f"Keyword location: URL column={url_match.start()}\n"
                )
            if source_match:
                matches = await _extract_source_matches(
                    pattern,
                    source,
                    url or "(inline/eval script)",
                    script_id,
                    cdp=cdp,
                )
                load_time = _format_load_time(meta.get("load_time"))
                if load_time:
                    matches = [
                        match.replace(
                            f"--- Script ID: {script_id} |",
                            f"--- Script ID: {script_id} | Load Time: {load_time} |",
                            1,
                        )
                        for match in matches
                    ]
                tab_scripts_matched.extend(matches)

        if tab_scripts_matched:
            remaining = max_result_blocks - len(results)
            keep = tab_scripts_matched[:remaining]
            results.append(
                f"=== {tab_header} ===\n" + "\n".join(keep)
            )

    if not results:
        scope_desc = (
            f"tab_index={tab_index}" if tab_index is not None else f"{len(scopes)} tab(s)"
        )
        return f"No content matching '{keyword}' found in JS files across {scope_desc}."

    intro_scope = (
        f"single tab (tab_index={tab_index})"
        if tab_index is not None
        else f"all {len(scopes)} attached tab(s); switch_tab before using a returned scriptId"
    )
    return (
        f"Search results ({intro_scope}; latest scripts first, source-hash deduped; "
        "only '*' rows expose raw CDP breakpoints):\n\n"
        + "\n".join(results)
    )


async def _auto_resume_to_new_breakpoint(session_id, session, cdp) -> str:
    import asyncio
    import time

    if not browser_manager.is_paused(session_id):
        return " The page is running freely. Please interact with the browser to trigger the request."

    # Capture old state
    old_paused_state = getattr(session, "paused_state", None)
    old_call_frames_str = str(old_paused_state.get("callFrames", [])) if old_paused_state else ""

    # Auto-resume
    await cdp.send("Debugger.resume")

    start_time = time.time()
    while time.time() - start_time < 5.0:
        if browser_manager.is_paused(session_id):
            new_paused_state = getattr(session, "paused_state", None)
            new_call_frames_str = str(new_paused_state.get("callFrames", [])) if new_paused_state else ""
            if new_call_frames_str != old_call_frames_str:
                return "\n🚀 [Auto-Warp Successful] Resumed execution and HIT the new breakpoint! (See Observer HUD for state)"
        await asyncio.sleep(0.1)

    return "\n⚠️ [Auto-Warp Timeout] Resumed execution, but did NOT hit the new breakpoint within 5 seconds. The page might be waiting for user input, or the code path was not reached."

# --- Professional CDP Debugging Tools ---

class BreakpointInput(StandardThoughtInput):
    """Input for setting breakpoints."""
    url_pattern: str = Field(description="URL match pattern or keyword. When hit, the page will pause execution.", default="")
    tab_index: Optional[int] = Field(
        default=None,
        description=(
            "0-based tab index to arm the XHR breakpoint on. XHR breakpoints are per-target — "
            "only requests from the chosen tab will pause. Omit / null → current active tab."
        ),
    )


@tool("break_on_request", args_schema=BreakpointInput)
async def break_on_request(
    url_pattern: str = "",
    tab_index: Optional[int] = None,
    **kwargs,
) -> str:
    """
    [ ⚠️ FALLBACK TOOL ONLY ] Set XHR/Fetch network breakpoints.
    IMPORTANT: You should PRIORTIZE using `set_line_breakpoint` based on the Initiator Stack from `inspect_network_request`.
    ONLY use `break_on_request` if the Initiator Stack is empty or missing, or if line breakpoints fail.

    When the page sends a request matching the pattern, JS execution pauses immediately.
    Then call `get_paused_state` to inspect the call stack.

    Tab scoping: pass `tab_index` to arm the breakpoint on a specific tab's CDP session.
    """
    session_id = str(kwargs.get("session_id", ""))
    session = browser_manager.get_session(session_id)
    try:
        tab_idx, page, handler, cdp = _resolve_tab(session, tab_index)
    except ValueError as exc:
        return f"Failed to set XHR breakpoint: {exc}"
    active_page = getattr(session, "page", None)
    is_active_tab = page is active_page

    try:
        await cdp.send("Debugger.enable")
        await cdp.send("Debugger.setSkipAllPauses", {"skip": False})

        # Enforce XHR breakpoint only
        await cdp.send("DOMDebugger.setXHRBreakpoint", {"url": url_pattern})
        if handler is not None:
            patterns = getattr(handler, "xhr_breakpoint_patterns", [])
            if url_pattern not in patterns:
                patterns.append(url_pattern)
            handler.xhr_breakpoint_patterns = patterns

        if is_active_tab:
            warp_msg = await _auto_resume_to_new_breakpoint(session_id, session, cdp)
        else:
            warp_msg = (
                f"\n⚠️ XHR breakpoint armed on Tab {tab_idx} which is NOT the active tab. "
                f"Call switch_tab({tab_idx}) before interacting; get_paused_state/step_execution "
                f"observe the active tab only."
            )
        return (
            f"XHR Breakpoint set successfully on [Tab {tab_idx}] (pattern: {url_pattern or 'all XHR'}).{warp_msg}\n"
            f"💡 TIP: If the paused stack is very shallow (< 4 frames), abandon this breakpoint and use `search_in_network_payloads` / `set_line_breakpoint` instead!"
        )
    except Exception as e:
        return f"Failed to set XHR breakpoint: {str(e)}"


class PausedStateInput(StandardThoughtInput):
    """Input for getting paused state."""
    frame_index: int = Field(default=0, description="Call stack frame index (0 = top frame)")


def _format_remote_value(remote_obj: Dict[str, Any], *, max_chars: int = 120) -> str:
    val_type = remote_obj.get("type")
    if "unserializableValue" in remote_obj:
        text = str(remote_obj.get("unserializableValue"))
    elif val_type == "string":
        text = json.dumps(str(remote_obj.get("value", "")), ensure_ascii=False)
    elif "value" in remote_obj:
        text = str(remote_obj.get("value"))
    else:
        text = str(remote_obj.get("description") or val_type or "")
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


async def _preview_remote_object(cdp, object_id: str, *, max_items: int = 8, max_value_chars: int = 80) -> str:
    try:
        props_res = await cdp.send("Runtime.getProperties", {
            "objectId": object_id,
            "ownProperties": True,
            "accessorPropertiesOnly": False,
        })
    except Exception:
        return ""

    items = []
    for prop in props_res.get("result", []):
        name = prop.get("name")
        if not name or name == "__proto__":
            continue
        val_obj = prop.get("value")
        if not isinstance(val_obj, dict):
            continue
        val_type = val_obj.get("type")
        desc = str(val_obj.get("description") or "")
        if val_type == "function":
            continue
        if val_type in ("undefined", "null") and not desc:
            continue

        items.append(f"{name}: {_format_remote_value(val_obj, max_chars=max_value_chars)}")
        if len(items) >= max_items:
            break

    if not items:
        return ""
    suffix = ", ..." if len(props_res.get("result", [])) > len(items) else ""
    return "{ " + ", ".join(items) + suffix + " }"


async def build_paused_state_report(session, cdp, event, frame_index: int = 0) -> str:
    """Helper function to build a comprehensive pause report including variables and source code."""
    call_frames = event.get("callFrames", [])
    if not call_frames:
        return "Pause captured, but no call stack information available."

    if frame_index >= len(call_frames):
        return (
            f"Error: specified frame_index ({frame_index}) exceeds call stack range. "
            f"Valid range: [0-{len(call_frames) - 1}]"
        )

    total_frames = len(call_frames)
    report = [f"=== Page is currently PAUSED | Viewing Frame #{frame_index} ==="]
    reason = event.get('reason', 'unknown')
    report.append(f"Reason: {reason}")

    if reason == "XHR" and total_frames < 4:
        report.append(
            "\n🚨 [SYSTEM WARNING]: You are currently paused at a physical live stack that is very shallow "
            f"({total_frames} frames). If the target encryption logic (e.g., md5, sign) is NOT found in "
            "the local variables below, it implies the site uses deep Async/Promises, and the variables "
            "have already been destroyed in the async closure.\n"
            "SOLUTION: ABANDON this paused state. Go use `inspect_network_request` to view the Historical "
            "Async Stack, find the exact line, and use `set_line_breakpoint`!\n"
        )
    report.append(
        f"Frame summary: valid range [0-{total_frames - 1}] | current={frame_index} | "
        f"top=0 | bottom={total_frames - 1} | sync_total={total_frames} | "
    )
    hit_breakpoints = event.get("hitBreakpoints") or []
    if hit_breakpoints:
        report.append(f"Hit breakpoints: {json.dumps(hit_breakpoints, ensure_ascii=False)}")

    # Extract call stack
    report.append("\n[Call Stack]")
    for i, frame in enumerate(call_frames[:15]):
        func_name = frame.get("functionName") or "(anonymous)"
        loc = frame.get("location", {})

        raw_line = int(loc.get("lineNumber", 0) or 0)
        raw_col = int(loc.get("columnNumber", 0) or 0)

        if i == frame_index:
            marker = " <--- currently viewing"
        else:
            marker = f"  -> 💡 [Action: call get_paused_state(frame_index={i})]"

        report.append(
            f"#{i} Function: {func_name} | Script: {loc.get('scriptId')} "
            f"| Location (0-indexed): {raw_line}:{raw_col}{marker}"
        )

    # ======= [Source Code Snippet - Optimized] =======
    try:
        loc = call_frames[frame_index].get("location", {})
        script_id = loc.get("scriptId")
        line_num = loc.get("lineNumber", 0)
        col_num = loc.get("columnNumber", 0)
        script_meta = getattr(session, "script_registry", {}).get(script_id, {}) or {}
        source_code = await _resolve_script_source(session, cdp, script_id, str(script_meta.get("url") or ""))

        if source_code:
            report.append(
                "\n[Source Code Snippet | raw source view]\n"
                + _render_raw_offset_excerpt(
                    source_code,
                    line_number=line_num,
                    column_number=col_num,
                )
            )
    except Exception as e:
        report.append(f"\n[Failed to retrieve source code]: {str(e)}")

    # Extract variables from multiple scopes
    target_frame = call_frames[frame_index]
    scopes = target_frame.get("scopeChain", [])

    if scopes and cdp:
        report.append(f"\n[Frame #{frame_index} Variables by Scope]")
        for scope in scopes:
            scope_type = scope.get("type", "unknown")
            # Skip global scope to prevent token explosion
            if scope_type == "global":
                continue

            report.append(f"▼ Scope: {scope_type.capitalize()}")
            obj_id = scope.get("object", {}).get("objectId")
            if not obj_id:
                report.append("  (empty or unavailable)")
                continue

            try:
                props_res = await cdp.send("Runtime.getProperties", {"objectId": obj_id, "ownProperties": True})
                properties = props_res.get("result", [])

                if not properties:
                    report.append("  (empty)")
                    continue

                for prop in properties:
                    name = prop.get("name")
                    val_obj = prop.get("value", {})
                    val_type = val_obj.get("type")
                    raw_val = val_obj.get("value")
                    desc = val_obj.get("description", "")

                    if val_type == "function" and "native code" in str(desc):
                        continue
                    if val_type in ("undefined", "null") or (raw_val is None and not desc):
                        continue

                    display_val = raw_val if raw_val is not None else desc
                    if val_type == "object" and val_obj.get("objectId"):
                        preview = await _preview_remote_object(cdp, val_obj["objectId"])
                        if preview:
                            display_val = f"{desc} {preview}".strip()
                    report.append(f"  - {name} ({val_type}): {str(display_val)[:300]}")
            except Exception as e:
                report.append(f"  - Failed to extract {scope_type} variables: {str(e)}")

    return "\n".join(report)


@tool("get_paused_state", args_schema=PausedStateInput)
async def get_paused_state(frame_index: int = 0, **kwargs) -> str:
    """
    Scene investigation tool: Get detailed information about the current paused state,
    including call stack, local variables, and source code snippet.
    Use `frame_index` to switch between different stack frame contexts.
    """
    session_id = str(kwargs.get("session_id", ""))
    session = browser_manager.get_session(session_id)
    event = getattr(session.cdp_handler, "last_paused_event", None)
    if not event:
        return "No pause signal detected on the current page."

    cdp = await browser_manager.get_cdp_client(session_id)
    return await build_paused_state_report(session, cdp, event, frame_index)


class EvaluateInContextInput(StandardThoughtInput):
    """Input for in-context evaluation."""
    frame_index: int = Field(default=0, description="Frame index")
    expression: str = Field(description="JavaScript expression")


@tool("evaluate_in_call_frame", args_schema=EvaluateInContextInput)
async def evaluate_in_call_frame(expression: str, frame_index: int = 0, **kwargs) -> str:
    """
    Dynamic probing tool: Execute an expression in the context of a paused call stack frame.
    - WHEN TO USE: To inspect Global variables (e.g. `window`, `navigator`) or to dynamically test expressions (e.g. `md5(data)`).
    - WHEN NOT TO USE: Do NOT use to guess Local or Block variables, as those are auto-extracted in the HUD.
    """
    session_id = kwargs.get("session_id")
    session = browser_manager.get_session(session_id)
    event = getattr(session.cdp_handler, "last_paused_event", None)
    if not event:
        return "Cannot execute: page is not paused."

    call_frames = event.get("callFrames", [])
    total_frames = len(call_frames)
    if frame_index >= total_frames:
        return (
            f"Error: frame_index {frame_index} out of range. "
            f"The live synchronous stack only has {total_frames} frames.\n"
            "🚨 [SYSTEM WARNING]: You cannot evaluate historical async frames here! "
            "If you got this index from `inspect_network_request`, that was the Historical Async Stack. "
            "To capture variables in a historical frame, you must use `set_line_breakpoint` at the exact "
            "ScriptID, Line, and Column shown in that historical stack, then trigger the request again."
        )

    call_frame_id = call_frames[frame_index].get("callFrameId")
    cdp = await browser_manager.get_cdp_client(session_id)

    try:
        res = await cdp.send("Debugger.evaluateOnCallFrame", {
            "callFrameId": call_frame_id,
            "expression": expression
        })
        if "exceptionDetails" in res:
            ex = res["exceptionDetails"].get("exception", {})
            text = res["exceptionDetails"].get("text", "Exception")
            desc = ex.get("description") or text
            return f"Error: {desc}"

        result_obj = res.get("result", {})
        js_type = result_obj.get("type", "unknown")
        js_class = result_obj.get("className", "")
        type_str = f"{js_type} ({js_class})" if js_class else js_type

        val = result_obj.get("value")
        if val is None:
            val = result_obj.get("description", "undefined")

        val_str = json.dumps(val, ensure_ascii=False) if isinstance(val, (dict, list)) else str(val)
        return f"Type: {type_str}\nValue: {val_str}"
    except Exception as e:
        return f"Exception: {str(e)}"


class GetScriptSourceInput(StandardThoughtInput):
    """Input for getting script source code."""
    script_id: str = Field(description="Script ID")
    line_number: int = Field(description="Optional raw line number anchor (0-indexed)", default=0)
    column_number: int = Field(description="Optional raw column number anchor (0-indexed, useful for minified JS)", default=0)
    tab_index: Optional[int] = Field(
        default=None,
        description=(
            "0-based tab index that owns this scriptId. ScriptIds are scoped per target. "
            "Pass the [Tab N] from search_in_js_codes. Omit / null → current active tab."
        ),
    )


@tool("get_script_source", args_schema=GetScriptSourceInput)
async def get_script_source(
    script_id: str,
    line_number: int = 0,
    column_number: int = 0,
    tab_index: Optional[int] = None,
    **kwargs,
) -> str:
    """
    Get source code of a specified script.
    - `line_number` and `column_number` are raw CDP anchors (0-indexed).
    - NOTE: This tool ONLY returns a 2400-character snippet. It NEVER returns the full file.
    - WHEN TO USE: To inspect distant logic missing from the HUD snippet or to explore Search results.

    Tab scoping: pass `tab_index` matching the [Tab N] label from search_in_js_codes.
    """
    session_id = str(kwargs.get("session_id", ""))
    session = browser_manager.get_session(session_id)
    try:
        tab_idx, page, handler, cdp = _resolve_tab(session, tab_index)
    except ValueError as exc:
        return f"Failed to retrieve: {exc}"
    try:
        script_registry = getattr(handler, "script_registry", {}) or {}
        meta = script_registry.get(script_id, {}) or {}
        raw_source = str(meta.get("source") or "")
        if not raw_source:
            return (
                f"Failed to retrieve: script `{script_id}` has no available source "
                f"in {_tab_header(tab_idx, page)}."
            )

        raw_lines = _raw_lines(raw_source)
        total_raw_lines = len(raw_lines)
        anchor_line = max(0, int(line_number or 0))
        anchor_col = min(max(0, int(column_number or 0)), len(raw_source))

        if anchor_line >= total_raw_lines:
            warning = f"🚨 WARNING: Requested line {line_number} exceeds file length ({total_raw_lines} lines). Snapping to end of file.\n"
            anchor_line = total_raw_lines - 1
        else:
            warning = ""

        if total_raw_lines == 0:
            return f"Script {script_id} source is empty."

        if line_number <= 0 and column_number <= 0:
            note = "No raw anchor provided; showing the beginning of the raw source."
        else:
            note = f"Showing raw source near anchor {anchor_line}:{anchor_col}."

        snippet = _render_raw_offset_excerpt(
            raw_source,
            line_number=anchor_line,
            column_number=anchor_col,
        )

        return (
            f"{_tab_header(tab_idx, page)}\n"
            f"Script {script_id} ({meta.get('url') or 'inline/eval script'})\n"
            "View: raw source\n"
            f"Raw lines: {len(raw_lines)}\n"
            f"Anchor raw location: {anchor_line}:{anchor_col}\n"
            f"{warning}{note}\n\n"
            f"[--- SNIPPET START ---]\n{snippet}\n[--- SNIPPET END ---]"
        )

    except Exception as e:
        return f"Failed to retrieve: {str(e)}"


class LineBreakpointInput(StandardThoughtInput):
    """Input for setting line breakpoints."""
    script_id: str = Field(description="Script ID")
    line_number: int = Field(description="Line number (0-indexed)")
    column_number: int = Field(default=0, description="Column number (critical for minified single-line JS)")
    clear_request_breakpoints: bool = Field(default=True, description="Whether to clear existing XHR request breakpoints before arming this line breakpoint")
    tab_index: Optional[int] = Field(
        default=None,
        description=(
            "0-based tab index that owns this scriptId. The breakpoint is armed on that tab's CDP session; "
            "pauses / call stacks only show up while that tab is the active one. "
            "Pass the [Tab N] from search_in_js_codes. Omit / null → current active tab."
        ),
    )


@tool("set_line_breakpoint", args_schema=LineBreakpointInput)
async def set_line_breakpoint(
    script_id: str,
    line_number: int,
    column_number: int = 0,
    clear_request_breakpoints: bool = True,
    tab_index: Optional[int] = None,
    **kwargs
) -> str:
    """Set a debug breakpoint at the target location. When the page hits this location, JS execution pauses immediately.

    Tab scoping: scriptIds are per-target. Pass `tab_index` matching the [Tab N] label from
    search_in_js_codes, otherwise the breakpoint targets the active tab.
    """
    session_id = str(kwargs.get("session_id", ""))
    session = browser_manager.get_session(session_id)
    try:
        tab_idx, page, handler, cdp = _resolve_tab(session, tab_index)
    except ValueError as exc:
        return f"Failed: {exc}"
    active_page = getattr(session, "page", None)
    is_active_tab = page is active_page
    try:
        # Prefer URL/hash breakpoints: they survive reload because V8 can bind
        # them again when the same script is parsed with a new scriptId.
        active_ids = getattr(handler, "active_script_ids", set()) or set()
        script_registry = getattr(handler, "script_registry", {}) or {}
        meta = script_registry.get(script_id) or {}
        url = str(meta.get("url") or "")
        script_hash = str(meta.get("hash") or "")
        is_active = script_id in active_ids

        if not meta and not is_active:
            return (
                f"Failed: scriptId '{script_id}' does not exist in {_tab_header(tab_idx, page)}. "
                f"Use search_in_js_codes to locate the current script."
            )

        await cdp.send("Debugger.enable")
        await cdp.send("Debugger.setSkipAllPauses", {"skip": False})
        if is_active:
            # Validate the requested location by probing with Debugger.setBreakpoint
            # (by scriptId). V8 returns the actualLocation it snapped to — this is the
            # authoritative source and has no response-size cap (unlike
            # getPossibleBreakpoints which truncates on large minified files).
            try:
                probe_res = await cdp.send("Debugger.setBreakpoint", {
                    "location": {
                        "scriptId": script_id,
                        "lineNumber": line_number,
                        "columnNumber": column_number,
                    }
                })
            except Exception as probe_exc:
                return (
                    f"Failed: V8 rejected breakpoint at {line_number}:{column_number}: {probe_exc}. "
                    f"Use search_in_js_codes to find a V8-confirmed location."
                )
            probe_id = str(probe_res.get("breakpointId") or "")
            probe_actual = probe_res.get("actualLocation") or {}
            probe_line = int(probe_actual.get("lineNumber", -1))
            probe_col = int(probe_actual.get("columnNumber", -1))
            # Always remove the probe — the real breakpoint is set by URL/hash below
            # so that it survives page reloads.
            if probe_id:
                try:
                    await cdp.send("Debugger.removeBreakpoint", {"breakpointId": probe_id})
                except Exception:
                    pass
            if not probe_actual:
                return (
                    f"Failed: V8 could not bind a breakpoint at {line_number}:{column_number}. "
                    f"The position may be inside a string, comment, or non-executable region. "
                    f"Use search_in_js_codes and choose a '*'-marked location."
                )
            if probe_line != line_number or probe_col != column_number:
                return (
                    f"Failed: requested {line_number}:{column_number} is not a V8-confirmed breakpoint location. "
                    f"V8 snapped to {probe_line}:{probe_col} instead. "
                    f"Choose one of the '*'-marked raw line:column locations from the pretty breakable context."
                )

        cleared_xhr = 0
        if clear_request_breakpoints:
            cleared_xhr = await _clear_xhr_breakpoints(handler, cdp)
        breakpoint_mode = "scriptId"
        res = {}
        actual_locations = []

        if url or script_hash:
            breakpoint_mode = "url" if url else "scriptHash"
            params: Dict[str, Any] = {
                "lineNumber": line_number,
                "columnNumber": column_number,
            }
            if url:
                params["url"] = url
            else:
                params["scriptHash"] = script_hash
            res = await cdp.send("Debugger.setBreakpointByUrl", params)
            actual_locations = res.get("locations") or []
        elif is_active:
            res = await cdp.send("Debugger.setBreakpoint", {
                "location": {"scriptId": script_id, "lineNumber": line_number, "columnNumber": column_number}
            })
            actual = res.get("actualLocation") or {}
            if actual:
                actual_locations = [actual]
        else:
            return (
                f"Failed: scriptId '{script_id}' is stale and has no URL/hash metadata, "
                f"so it cannot be rebound after reload. Re-run search_in_js_codes to locate the current script."
            )

        breakpoint_id = str(res.get("breakpointId") or "")

        # If the script is already active, empty locations means V8 could not bind
        # the requested line/column to an executable statement.
        if is_active and not actual_locations:
            if breakpoint_id:
                try:
                    await cdp.send("Debugger.removeBreakpoint", {"breakpointId": breakpoint_id})
                except Exception:
                    pass
            return (
                f"Failed: breakpoint NOT BOUND at {line_number}:{column_number}. "
                f"V8 can only bind breakpoints to executable JavaScript statements. "
                f"Common causes: the anchor is inside a string literal, comment, whitespace, "
                f"or the column is not on a statement in minified code. "
                f"Recovery: use search_in_js_codes/get_script_source to pick the keyword's absolute line/column, "
                f"then move to the nearest executable statement if V8 rejects it."
            )

        actual = actual_locations[0] if actual_locations else {}
        actual_line = actual.get("lineNumber", line_number)
        actual_col = actual.get("columnNumber", column_number)

        # Register the successful breakpoint
        if handler is not None and breakpoint_id:
            ids = getattr(handler, "line_breakpoint_ids", [])
            if breakpoint_id not in ids:
                ids.append(breakpoint_id)
            handler.line_breakpoint_ids = ids

        # Warn if V8 snapped to a different position
        drift_note = ""
        if actual_line != line_number or (actual_col is not None and actual_col != column_number):
            drift_note = (
                f" NOTE: V8 snapped the breakpoint from requested {line_number}:{column_number} "
                f"to actual {actual_line}:{actual_col} (nearest executable statement)."
            )
        persistence_note = ""
        if breakpoint_mode in ("url", "scriptHash"):
            target = url if breakpoint_mode == "url" else f"scriptHash:{script_hash}"
            if actual_locations:
                persistence_note = f" Persistent {breakpoint_mode} breakpoint armed for {target}; it should rebind after reload."
            else:
                persistence_note = f" Persistent {breakpoint_mode} breakpoint armed for future script parse: {target}."

        tab_note = f" [Tab {tab_idx}]"
        # Auto-warp only makes sense for the agent's active tab — the paused
        # state observer reads `session.cdp_handler` (active tab). For a
        # non-active tab we tell the LLM to switch_tab before interacting.
        if is_active_tab:
            warp_msg = await _auto_resume_to_new_breakpoint(session_id, session, cdp)
        else:
            warp_msg = (
                f"\n⚠️ Breakpoint is armed on Tab {tab_idx} which is NOT the active tab. "
                f"Call switch_tab({tab_idx}) before interacting; get_paused_state/step_execution "
                f"observe the active tab only."
            )
        clear_note = f" Cleared {cleared_xhr} XHR breakpoint(s)." if clear_request_breakpoints else ""
        return (
            f"Breakpoint set successfully:{tab_note} {breakpoint_id} "
            f"@ {actual_line}:{actual_col}.{drift_note}{persistence_note}{clear_note}{warp_msg}"
        )
    except Exception as e:
        return f"Failed: {str(e)}"


class StepInput(StandardThoughtInput):
    """Input for step execution control."""
    action: str = Field(
        description="'over' (step over), 'into' (step into function), 'out' (step out of current function), 'resume' (continue to next breakpoint), or 'resume_and_clear' (force resume and ignore all subsequent breakpoints)",
        default="resume")


@tool("step_execution", args_schema=StepInput)
async def step_execution(action: str = "resume", **kwargs) -> str:
    """
    [ ⚠️ DANGEROUS TOOL ] Control JS execution flow. 
    DO NOT use 'over' or 'into' for long distances. It will burn tokens. 
    ALWAYS prioritize setting a `set_line_breakpoint` further down the code and using 'resume'. 
    ONLY use 'over', 'into', or 'out' for escaping library traps or dynamic eval traversal. 
    """
    session_id = kwargs.get("session_id")
    cdp = await browser_manager.get_cdp_client(session_id)
    session = browser_manager.get_session(session_id)

    try:
        import asyncio
        import time

        # Capture current state before stepping to detect changes
        old_paused_state = getattr(session, "paused_state", None) if session else None
        old_call_frames_str = str(old_paused_state.get("callFrames", [])) if old_paused_state else ""

        if action == "over":
            await cdp.send("Debugger.stepOver")
        elif action == "into":
            await cdp.send("Debugger.stepInto")
        elif action == "out":
            await cdp.send("Debugger.stepOut")
        elif action == "resume_and_clear":
            await cdp.send("Debugger.setSkipAllPauses", {"skip": True})
            await cdp.send("Debugger.resume")
        else:
            await cdp.send("Debugger.resume")

        # Fast polling loop: wait until the pause state changes or 5 seconds timeout
        if action in ["over", "into", "out", "resume"]:
            start_time = time.time()
            while time.time() - start_time < 5.0:
                if not browser_manager.is_paused(session_id):
                    # If we resumed and it's not paused, break early but give it a small buffer
                    if action == "resume":
                        await asyncio.sleep(0.5)
                        break
                else:
                    # If it IS paused, verify it's a NEW pause state
                    new_paused_state = getattr(session, "paused_state", None) if session else None
                    new_call_frames_str = str(new_paused_state.get("callFrames", [])) if new_paused_state else ""
                    # For 'resume', any pause is a new pause (even same line if hit again)
                    # For step actions, we want to ensure the call frame state explicitly changed
                    if new_call_frames_str != old_call_frames_str or action == "resume":
                        break

                await asyncio.sleep(0.1)
        else:
            await asyncio.sleep(0.5)

        return f"Action sent successfully: {action}"
    except Exception as e:
        return f"Failed to send action: {str(e)}"


# --- JS Extraction Tools ---

class DumpRuntimeAssetInput(StandardThoughtInput):
    """Input for dumping a full captured runtime asset to a standalone file."""
    request_id: str = Field(
        default="",
        description="Captured requestId (Track A). Optional if script_id is provided.",
    )
    script_id: str = Field(
        default="",
        description="Script ID (Track B). Optional if request_id is provided.",
    )
    filename: str = Field(default="", description="Optional output filename. If omitted, infer from URL/mimeType.")


def _register_dumped_asset(
    session_id: str, filepath: str, summary: str, producer: str = "", kind: str = "dump",
) -> None:
    """Register a runtime asset into delivery_status. `skip_audit` keeps find_fault
    from reviewing a raw JS/WASM dump on content. `kind` controls delivery: a
    plain `dump` is a candidate the agent may inspect but that does NOT auto-ship;
    only `runtime_mount` (assigned when a file is actually mounted for a run) is
    auto-promoted into the delivery. This keeps analysis-only source dumps out of
    the handoff while true runtime dependencies still ride along."""
    filename = os.path.basename(filepath)
    if not filename:
        return
    delivery_status = session_store.get(session_id, "delivery_status", {}) or {}
    delivery_status[filename] = {
        "path": os.path.abspath(filepath),
        "status": "DRAFT",
        "fatal_gaps": [],
        "recommended_rework": [],
        "summary": summary,
        "tags": ["skip_audit", f"kind:{kind}"],
        "producer": producer,
    }
    session_store.set(session_id, "delivery_status", delivery_status)


@tool("dump_runtime_asset", args_schema=DumpRuntimeAssetInput)
async def dump_runtime_asset(
    request_id: str = "",
    script_id: str = "",
    filename: str = "",
    **kwargs,
) -> str:
    """
    Dump a complete captured runtime asset (JS/WASM) to a standalone file for
    sandbox execution or offline analysis.

    IMPORTANT: Before dumping, you should understand the target generator's interface:
    - What function generates the required value (from breakpoint, code search, or AST analysis).
    - What its input parameters are.
    - How to call it in the sandbox (e.g. window.X(args), module.exports.fn(args)).

    If you haven't analyzed the generator yet, use breakpoint analysis
    (set_line_breakpoint + get_paused_state), or code search
    (search_in_js_codes + get_script_source) first.
    """
    session_id = str(kwargs.get("session_id", ""))
    runtime_context = dict(kwargs.get("runtime_context") or {})
    producer = str(
        runtime_context.get("current_agent_name")
        or kwargs.get("current_agent_name")
        or ""
    ).strip()
    from reverseloom.runtime.config import artifact_dir as _artifact_dir
    from urllib.parse import urlsplit

    artifact_dir = _artifact_dir(session_id)
    os.makedirs(artifact_dir, exist_ok=True)

    session = browser_manager.get_session(session_id)

    if not request_id and not script_id:
        return "You must provide either request_id or script_id."

    # --- TRACK B (V8 Script Extraction) ---
    if script_id:
        handler = getattr(session, "cdp_handler", None)
        script_registry = getattr(handler, "script_registry", {}) if handler else {}
        meta = script_registry.get(script_id)
        if not meta:
            return f"Script with scriptId {script_id} not found in V8 registry."

        content = str(meta.get("source", ""))
        if not content:
            return f"Script {script_id} exists but has no extracted source."

        if not filename:
            url_path = urlsplit(str(meta.get("url") or "")).path.rstrip("/")
            inferred_name = os.path.basename(url_path)
            filename = inferred_name if inferred_name else f"script_{script_id}.js"
            if not filename.endswith(".js"):
                filename += ".js"

        filepath = os.path.join(artifact_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        _register_dumped_asset(
            session_id, filepath,
            f"Dumped V8 script {script_id} from {meta.get('url') or '<unknown>'} ({len(content)} chars)",
            producer=producer,
        )
        return f"V8 Script dumped: {filepath} ({len(content)} chars)"

    # --- TRACK A (Network Extraction) ---
    target = next((req for req in session.network_logs if req.get("requestId") == request_id), None)
    if not target:
        return f"Request with requestId {request_id} not found."

    url = str(target.get("url") or "")
    if not filename:
        url_path = urlsplit(url).path.rstrip("/")
        inferred_name = os.path.basename(url_path)
        if inferred_name:
            filename = inferred_name
        elif _looks_like_wasm(target):
            filename = "module.wasm"
        else:
            filename = "runtime_asset.js"

    filepath = os.path.join(artifact_dir, filename)
    if _looks_like_wasm(target):
        body_bytes = _response_body_bytes(target)
        if not body_bytes:
            return f"Request {request_id} has no extractable WASM body."
        if not filepath.lower().endswith(".wasm"):
            filepath += ".wasm"
        with open(filepath, "wb") as f:
            f.write(body_bytes)
        _register_dumped_asset(
            session_id, filepath,
            f"Dumped WASM module from {url} ({len(body_bytes)} bytes)",
            producer=producer,
        )

        # Auto-dump JS glue code based on initiator_stack.
        glue_code_msg = ""
        initiator_stack = target.get("initiator_stack", [])
        if initiator_stack:
            glue_url = str(initiator_stack[0].get("url") or "")
            if glue_url:
                handler = getattr(session, "cdp_handler", None)
                if handler and glue_url in handler.url_to_script_ids:
                    s_ids = handler.url_to_script_ids[glue_url]
                    if s_ids:
                        glue_script_id = s_ids[0]
                        meta = handler.script_registry.get(glue_script_id, {})
                        glue_content = str(meta.get("source", ""))
                        if glue_content:
                            glue_filename = "glue_" + os.path.basename(urlsplit(glue_url).path.rstrip("/"))
                            if not glue_filename.endswith(".js"):
                                glue_filename += ".js"
                            glue_filepath = os.path.join(artifact_dir, glue_filename)
                            with open(glue_filepath, "w", encoding="utf-8") as gf:
                                gf.write(glue_content)
                            glue_summary = (
                                f"JS glue loader (initiator of WASM {os.path.basename(filepath)}) "
                                f"from {glue_url} ({len(glue_content)} chars)"
                            )
                            _register_dumped_asset(
                                session_id, glue_filepath, glue_summary, producer=producer,
                            )
                            glue_code_msg = (
                                f"\nAlso auto-dumped the JS glue loader for this WASM: "
                                f"{glue_filepath} ({len(glue_content)} chars)"
                            )

        return (
            f"WASM runtime asset dumped: {filepath}\n"
            f"byte_length: {len(body_bytes)}\n"
            f"mimeType: {target.get('mimeType', '')}\n"
            f"magic: {body_bytes[:4].hex() if body_bytes else 'unknown'}"
            f"{glue_code_msg}"
        )

    response_body = target.get("responseBody", "")
    if target.get("responseBodyIsBase64"):
        try:
            response_body = base64.b64decode(response_body).decode("utf-8", errors="ignore")
        except Exception:
            response_body = ""
    content = str(response_body or "")

    # Auto-Fallback to Track B if empty
    if not content and url:
        handler = getattr(session, "cdp_handler", None)
        if handler and url in handler.url_to_script_ids:
            s_ids = handler.url_to_script_ids[url]
            if s_ids:
                s_id = s_ids[0]
                meta = handler.script_registry.get(s_id, {})
                content = str(meta.get("source", ""))

    if not content:
        return f"Request {request_id} has no text body to dump (Track A is empty and Track B fallback failed)."

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    _register_dumped_asset(
        session_id, filepath,
        f"Dumped runtime asset from {url} ({len(content)} chars)",
        producer=producer,
    )
    return f"Runtime asset dumped: {filepath} ({len(content)} chars)"




REVERSE_TOOLS = [
    search_in_network_payloads,
    inspect_network_request,
    set_line_breakpoint,
    break_on_request,
    get_paused_state,
    evaluate_in_call_frame,
    step_execution,
    get_script_source,
    search_in_js_codes,
    dump_runtime_asset,
]
