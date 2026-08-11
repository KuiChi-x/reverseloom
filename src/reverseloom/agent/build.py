import os
from enum import Enum
from typing import Any, Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from graphloom import build_agent_graph

from reverseloom.agent.chat_model import ChatOpenAIWithReasoning
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
    OPENAI_CHAT = "openai/chat"
    OPENAI_RESPONSES = "openai/responses"
    ANTHROPIC = "anthropic"


class ReasoningEffort(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


def build_llm() -> BaseChatModel:
    """按 MODEL_PROTOCOL 构造模型客户端。三条线的取舍见 runtime/settings.py 的文案。

    缓存命中率：``/chat/completions`` 实测可复用前缀（qwen3.7-plus 55~90%），
    而同一网关的 ``/responses`` 只在整包请求逐字节相同时才算命中，Agent 循环
    永远碰不上，实测恒为 0%。

    但协议不是随便选的 —— 部分模型（如 gpt-5.6-sol）在 chat completions 上
    拒绝 "function tools + reasoning_effort" 组合，只能走 responses。所以三条
    线都得留着，由配置按模型选。
    """
    protocol = ModelProtocol(os.environ.get("MODEL_PROTOCOL", ModelProtocol.OPENAI_CHAT))
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

    if protocol == ModelProtocol.OPENAI_RESPONSES:
        # responses/v1 自带 reasoning 内容块，不需要 ChatOpenAIWithReasoning。
        kwargs["use_responses_api"] = True
        kwargs["output_version"] = "responses/v1"
        if effort:
            kwargs["reasoning"] = {"effort": effort, "summary": "detailed"}
        return ChatOpenAI(**kwargs)

    kwargs["stream_usage"] = True
    if effort:
        # Chat completions caps at xhigh; max only exists on the Anthropic side.
        kwargs["reasoning_effort"] = "xhigh" if effort == ReasoningEffort.MAX else effort
    return ChatOpenAIWithReasoning(**kwargs)


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
