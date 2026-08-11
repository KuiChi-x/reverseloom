"""ChatOpenAI subclass that keeps provider reasoning text on /chat/completions.

Stock ``ChatOpenAI`` targets the official OpenAI schema and drops non-standard
fields such as ``reasoning_content`` (its own docstring points you at a
provider-specific subclass). We re-attach it as a standard v1 ``reasoning``
content block, so graphloom reads thinking text through
``message.content_blocks`` exactly as it does on the Responses API.
"""
from __future__ import annotations

from typing import Optional

from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI


class ChatOpenAIWithReasoning(ChatOpenAI):
    """``ChatOpenAI`` that surfaces ``reasoning_content`` as a reasoning block."""

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: Optional[dict],
    ) -> Optional[ChatGenerationChunk]:
        generation = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation is None or not isinstance(generation.message, AIMessageChunk):
            return generation

        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices") or []
        reasoning = choices[0].get("delta", {}).get("reasoning_content") if choices else None

        # Every chunk has to carry list content: merge_content appends a bare
        # string next to the block dicts when a str chunk merges into a list
        # one, and consumers iterating content_blocks would hit a str. The
        # stable indexes let deltas merge in place instead of piling up.
        message = generation.message
        blocks: list[dict] = []
        if isinstance(message.content, str) and message.content:
            blocks.append({"type": "text", "text": message.content, "index": 0})
        if isinstance(reasoning, str) and reasoning:
            blocks.append({"type": "reasoning", "reasoning": reasoning, "index": 1})
        message.content = blocks
        # content_blocks only returns the list verbatim for v1-tagged messages;
        # otherwise the OpenAI translator rebuilds it and drops the reasoning.
        message.response_metadata["output_version"] = "v1"
        return generation
