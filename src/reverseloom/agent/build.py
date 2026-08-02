import os
from enum import Enum
from typing import Any, Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from graphloom import build_agent_graph

from reverseloom.browser import create_browser_observer_node
from reverseloom.agent.prompts import (
    BROWSER_AGENT_SPECIFIC_RULES_PROMPT,
    DELIVERY_STRATEGY_PROMPT,
    REVERSE_FIND_FAULT_PROMPT, SAFE_AUTHORIZATION_PROMPT,
)
from reverseloom.runtime.paths import default_skills_dir
from reverseloom.tools.browser.automation import AUTOMATION_TOOLS
from reverseloom.tools.browser.investigation import REVERSE_TOOLS
from reverseloom.tools.filesystem import FILESYSTEM_TOOLS

_SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills")
_AVAILABLE_SKILLS = ["*"]
_SKILLS_DIRS = [_SKILLS_DIR, str(default_skills_dir())]

SYSTEM_PROMPT = SAFE_AUTHORIZATION_PROMPT + DELIVERY_STRATEGY_PROMPT + BROWSER_AGENT_SPECIFIC_RULES_PROMPT
ALL_TOOLS = REVERSE_TOOLS + FILESYSTEM_TOOLS + AUTOMATION_TOOLS


class ModelProtocol(str, Enum):
    OPENAI_RESPONSES = "openai/responses"
    ANTHROPIC = "anthropic"


class ReasoningEffort(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


def build_llm() -> BaseChatModel:
    """Construct the configured OpenAI Responses or Anthropic model."""
    protocol = ModelProtocol(os.environ.get("MODEL_PROTOCOL", ModelProtocol.OPENAI_RESPONSES))
    raw_effort = os.environ.get("MODEL_REASONING_EFFORT", "").strip().lower()
    effort = ReasoningEffort(raw_effort).value if raw_effort else None
    kwargs: dict[str, Any] = {
        "model": os.environ.get("MODEL", "gpt-4o").strip(),
        "base_url": os.environ.get("BASE_URL") or None,
        "api_key": os.environ.get("OPENAI_API_KEY") or None,
        "streaming": True,
    }

    if protocol == ModelProtocol.ANTHROPIC:
        kwargs["betas"] = ["context-management-2025-06-27"]
        if effort:
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
            kwargs["output_config"] = {"effort": effort}
        return ChatAnthropic(**kwargs)

    kwargs["use_responses_api"] = True
    kwargs["output_version"] = "responses/v1"
    if effort:
        kwargs["reasoning"] = {"effort": effort, "summary": "detailed"}
    return ChatOpenAI(**kwargs)


def build_agent(llm: Optional[BaseChatModel] = None, checkpointer=None):
    """Compile the reverseloom agent graph: browser+reverse primary, general tools auxiliary.

    Tools drive the browser_manager singleton (keyed by session_id); the observer
    injects a fresh browser snapshot each turn.
    """
    tools = list(ALL_TOOLS)
    return build_agent_graph(
        custom_system_prompt=SYSTEM_PROMPT,
        tools=tools,
        llm=llm or build_llm(),
        observer=create_browser_observer_node(tools=tools),
        checkpointer=checkpointer,
        find_fault=REVERSE_FIND_FAULT_PROMPT,
        allow_direct_reply=True,
        available_skills=_AVAILABLE_SKILLS,
        skills_dirs=_SKILLS_DIRS,
    )
