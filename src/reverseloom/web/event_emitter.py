"""graphloom BaseEventEmitter → reverse WebSocket JSON messages.

Reverse's UI protocol uses string ``type`` fields (reasoning/token/step_*),
not octopus ActionTypeEnums codes.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List

from graphloom import BaseEventEmitter

# Host send: async (session_id, payload_dict) -> None
SendFn = Callable[[str, Dict[str, Any]], Awaitable[None]]


class ReverseEventEmitter(BaseEventEmitter):
    """Maps graphloom events onto reverse's WebSocket envelope.

    Also accumulates streamed content into ``streamed_reply_parts`` so the
    host can fall back when ``final_reply`` is empty.
    """

    def __init__(
        self,
        *,
        session_id: str,
        send: SendFn,
        streamed_reply_parts: List[str] | None = None,
    ) -> None:
        self._session_id = session_id
        self._send = send
        self.streamed_reply_parts: List[str] = (
            streamed_reply_parts if streamed_reply_parts is not None else []
        )

    async def on_ai_delta(self, payload: Dict[str, Any]) -> None:
        reasoning = str(payload.get("reasoning") or "")
        content = str(payload.get("content") or "")
        if reasoning:
            await self._send(self._session_id, {"type": "reasoning", "text": reasoning})
        if content:
            self.streamed_reply_parts.append(content)
            await self._send(self._session_id, {"type": "token", "text": content})

    async def on_step_planned(self, payload: Dict[str, Any]) -> None:
        await self._send(self._session_id, {"type": "step_start", **payload})

    async def on_tool_start(self, payload: Dict[str, Any]) -> None:
        await self._send(self._session_id, {"type": "tool_start", **payload})

    async def on_tool_end(self, payload: Dict[str, Any]) -> None:
        await self._send(self._session_id, {"type": "tool_end", **payload})

    async def on_step_done(self, payload: Dict[str, Any]) -> None:
        await self._send(self._session_id, {"type": "step_done", **payload})

    # reverse UI has no multi-agent panel yet; keep hooks for forward-compat.
    async def on_subagent_state(self, payload: Dict[str, Any]) -> None:
        await self._send(self._session_id, {"type": "subagent_state", **payload})

    async def on_subagent_reply(self, payload: Dict[str, Any]) -> None:
        await self._send(self._session_id, {"type": "subagent_reply", **payload})
