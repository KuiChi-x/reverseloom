import os
from typing import Optional

import litellm
from langchain_litellm import ChatLiteLLM

from graphloom import build_agent_graph

from reverseloom.browser import create_browser_observer_node
from reverseloom.agent.prompts import (
    BROWSER_AGENT_SPECIFIC_RULES_PROMPT,
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

litellm.suppress_debug_info = True
litellm.drop_params = True

SYSTEM_PROMPT = SAFE_AUTHORIZATION_PROMPT + BROWSER_AGENT_SPECIFIC_RULES_PROMPT
ALL_TOOLS = REVERSE_TOOLS + FILESYSTEM_TOOLS + AUTOMATION_TOOLS


def build_llm() -> ChatLiteLLM:
    """Construct the configured LiteLLM chat model."""
    protocol = os.environ.get("MODEL_PROTOCOL", "openai").strip()
    model = f"{protocol}/{os.environ.get('MODEL', 'gpt-4o').strip()}"
    kwargs = {
        "model": model,
        "api_base": os.environ.get("BASE_URL") or None,
        "api_key": os.environ.get("OPENAI_API_KEY") or None,
        "streaming": True,
    }
    reasoning_effort = os.environ.get("MODEL_REASONING_EFFORT", "").strip().lower()
    if reasoning_effort:
        kwargs["model_kwargs"] = {"reasoning_effort": reasoning_effort}
    return ChatLiteLLM(**kwargs)


def build_agent(llm: Optional[ChatLiteLLM] = None, checkpointer=None):
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
