import json
from typing import Any, Dict, List, Literal

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from graphloom import StandardThoughtInput


class InteractionOption(BaseModel):
    label: str = Field(description="User-facing option text.")
    value: str = Field(description="Stable value returned to the agent.")
    description: str = Field(default="", description="Optional helper text shown under the option.")
    requires_input: bool = Field(
        default=False,
        description="Whether the user must provide additional text after selecting this option.",
    )
    input_placeholder: str = Field(
        default="",
        description="Placeholder shown when additional text is required.",
    )


class RequestUserInteractionInput(StandardThoughtInput):
    question: str = Field(description="The concrete question or action request shown to the user.")
    interaction_type: Literal[
        "missing_info",
        "ambiguous_requirement",
        "approach_choice",
        "risk_confirmation",
        "suggestion",
        "human_action",
    ] = Field(
        description=(
            "Classify why input is needed. Use human_action only when the user must operate the browser, "
            "log in, solve a captcha, or complete another action outside the agent."
        )
    )
    context: str = Field(default="", description="Why the interaction is needed and what will happen next.")
    options: List[InteractionOption] = Field(
        default_factory=list,
        max_length=6,
        description=(
            "Optional structured choices. Provide choices whenever multiple reasonable paths exist. "
            "For human_action, phrase each option as an action the user completes before selecting it."
        ),
    )
    allow_free_text: bool = Field(
        default=True,
        description="Whether the user may reply with text instead of selecting an option.",
    )
    input_placeholder: str = Field(default="", description="Placeholder for a free-text response.")


def _normalise_options(options: List[Any] | None) -> List[Dict[str, Any]]:
    parsed = []
    for option in options or []:
        if hasattr(option, "model_dump"):
            parsed.append(option.model_dump(exclude_none=True))
        elif isinstance(option, dict):
            parsed.append(dict(option))
        else:
            parsed.append(dict(option))
    return parsed


@tool("request_user_interaction", args_schema=RequestUserInteractionInput)
async def request_user_interaction(
    question: str,
    interaction_type: str,
    context: str = "",
    options: List[Any] | None = None,
    allow_free_text: bool = True,
    input_placeholder: str = "",
    **kwargs,
) -> Dict[str, Any]:
    """Pause and ask the user for a choice, clarification, confirmation, or manual action.

    Use one interaction instead of guessing. Include structured options when the user
    can choose between approaches. For login, captcha, or other browser takeover,
    use human_action and let the user complete the selected action before resuming.
    """
    payload = {
        "type": "user_interaction",
        "interaction_type": interaction_type,
        "question": question,
        "context": context,
        "options": _normalise_options(options),
        "allow_free_text": allow_free_text,
        "input_placeholder": input_placeholder,
        "message": question,
    }

    try:
        resume_value = interrupt(payload)
    except GraphInterrupt:
        raise
    except Exception:
        return {
            "interaction_error": (
                "request_user_interaction requires a checkpointer-backed run to pause. "
                "Ask the user directly and wait for a new request instead."
            )
        }

    result_text = json.dumps(resume_value, ensure_ascii=False) if isinstance(resume_value, (dict, list)) else str(resume_value)
    verification = (
        " Verify the browser state before continuing."
        if interaction_type == "human_action"
        else ""
    )
    return {
        "interaction_result": resume_value,
        "message": f"The user responded.{verification}",
        "messages": [
            HumanMessage(
                content=(
                    "[User Interaction Result]\n"
                    f"Type: {interaction_type}\n"
                    f"Question: {question}\n"
                    f"User Response: {result_text}\n"
                    f"Action Required:{verification or ' Continue using the user response.'}"
                )
            )
        ],
    }
