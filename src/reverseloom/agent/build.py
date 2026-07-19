import os
from typing import Any, Dict, Optional

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

# Skill library shipped with reverseloom (progressive-disclosure via graphloom).
_SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills")
_AVAILABLE_SKILLS = ["*"]
_SKILLS_DIRS = [_SKILLS_DIR, str(default_skills_dir())]

SYSTEM_PROMPT = SAFE_AUTHORIZATION_PROMPT + DELIVERY_STRATEGY_PROMPT + BROWSER_AGENT_SPECIFIC_RULES_PROMPT
ALL_TOOLS = REVERSE_TOOLS + FILESYSTEM_TOOLS + AUTOMATION_TOOLS

_REASONING_EFFORTS = {"low", "medium", "high", "xhigh", "max"}


def build_llm() -> BaseChatModel:
    """Construct the configured OpenAI-compatible or Anthropic chat model."""
    protocol = os.environ.get("MODEL_PROTOCOL", "openai").strip()
    if protocol not in {"openai", "openai/responses", "anthropic"}:
        raise ValueError(f"Unsupported MODEL_PROTOCOL: {protocol}")
    model_name = os.environ.get("MODEL", "gpt-4o").strip()
    kwargs: Dict[str, Any] = {
        "model": model_name,
        "base_url": os.environ.get("BASE_URL") or None,
        "api_key": os.environ.get("OPENAI_API_KEY") or None,
        "streaming": True,
    }
    reasoning_effort = os.environ.get("MODEL_REASONING_EFFORT", "").strip().lower()
    reasoning_enabled = reasoning_effort in _REASONING_EFFORTS

    if protocol == "anthropic":
        kwargs["model_kwargs"] = {"cache_control": {"type": "ephemeral"}}
        if reasoning_enabled:
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
            kwargs["output_config"] = {"effort": reasoning_effort}
        return ChatAnthropic(**kwargs)

    kwargs["model_kwargs"] = {
        "prompt_cache_key": (
            os.environ.get("PROMPT_CACHE_KEY", "").strip()
            or f"reverseloom:{protocol}/{model_name}"
        ),
        "prompt_cache_retention": "24h",
    }
    if protocol == "openai/responses":
        kwargs["use_responses_api"] = True
        kwargs["output_version"] = "responses/v1"
        if reasoning_enabled:
            kwargs["reasoning"] = {
                "effort": reasoning_effort,
                "summary": "detailed",
            }
    elif reasoning_enabled:
        kwargs["reasoning_effort"] = reasoning_effort
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
