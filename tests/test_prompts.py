"""Prompt assembly: verbatim-ported technical content is present, jailbreak/legal
persona and dead tool references are absent."""
from reverseloom.agent.build import SYSTEM_PROMPT
from reverseloom.agent.prompts import (
    BROWSER_AGENT_SPECIFIC_RULES_PROMPT,
    REVERSE_FIND_FAULT_PROMPT,
)


def test_browser_rules_sections_present():
    for tag in ("<input>", "<browser_state>", "<browser_vision>", "<browser_rules>",
                "<efficiency_guidelines>", "<browser_critical_reminders>",
                "<browser_error_recovery>"):
        assert tag in BROWSER_AGENT_SPECIFIC_RULES_PROMPT


def test_deep_reverse_technical_sections_live_in_skill():
    from pathlib import Path

    skill = (
        Path(__file__).parents[1]
        / "src"
        / "reverseloom"
        / "skills"
        / "deep-reverse"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    for tag in ("<goal>", "<triage>", "<tool_priority>", "<static_rewrite_rules>",
                "<element_referencing>", "<deliverable>"):
        assert tag in skill
    assert "<triage>" not in SYSTEM_PROMPT


def test_dead_internal_kb_reference_removed():
    # recall_vendor_knowledge tool was not ported; its section must be gone.
    assert "<internal_knowledge_base>" not in SYSTEM_PROMPT
    assert "recall_vendor_knowledge" not in SYSTEM_PROMPT


def test_no_jailbreak_or_legal_persona_in_system_prompt():
    low = SYSTEM_PROMPT.lower()
    assert "without ethical" not in low
    assert "refusal to complete" not in low
    assert "licensed data integration" not in low
    assert "legal review" not in low


def test_system_prompt_composes_ported_sections():
    assert "<triage>" not in SYSTEM_PROMPT
    assert "<browser_rules>" in SYSTEM_PROMPT


def test_find_fault_prompt_has_review_dimensions():
    assert "<review_dimensions>" in REVERSE_FIND_FAULT_PROMPT
    assert "No browser dependency" in REVERSE_FIND_FAULT_PROMPT


def test_deep_reverse_owns_sandbox_reference():
    from pathlib import Path

    skill_dir = (
        Path(__file__).parents[1]
        / "src"
        / "reverseloom"
        / "skills"
        / "deep-reverse"
    )
    skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    sandbox = (skill_dir / "references" / "sandbox-reproduction.md").read_text(encoding="utf-8")

    assert "references/sandbox-reproduction.md" in skill
    assert "Runtime Contract" in sandbox
    assert not sandbox.startswith("---")


def test_sandbox_reference_is_not_an_independent_skill():
    from graphloom.skills.loader import get_skills_prompt_section
    from reverseloom.agent.build import _AVAILABLE_SKILLS, _SKILLS_DIRS

    catalog = get_skills_prompt_section(_AVAILABLE_SKILLS, skills_dirs=_SKILLS_DIRS)

    assert "<name>deep-reverse</name>" in catalog
    assert "<name>web-crawl</name>" in catalog
    assert "<name>reverseloom_sandbox</name>" not in catalog



def test_web_crawl_uses_adaptive_effort_ladder():
    from pathlib import Path

    skill_dir = (
        Path(__file__).parents[1]
        / "src"
        / "reverseloom"
        / "skills"
        / "web-crawl"
    )
    skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

    assert "Direct answer" in skill
    assert "Do not create files or code unless requested" in skill
    assert "Do not generate a crawler for a single value" in skill
    assert "references/crawler-engineering.md" in skill
    assert "references/data-output-validation.md" in skill
    assert (skill_dir / "references" / "crawler-engineering.md").is_file()
    assert (skill_dir / "references" / "data-output-validation.md").is_file()
