"""Every call_agent_json("name", …) / call_agent("name", …) literal must have a
matching prompts/<name>.md. A rename that updates the prompt file but leaves a
stale call site (e.g. "competitor" → "spec-extract", commit 85c9864) silently
breaks that stage at runtime — the agent fails to load, the call is caught, and
the pipeline degrades (broken Rds_on extraction = "sim runs hot" in the
CR-vs-Proteus benchmark). This static check catches it without an LLM call.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2] / "heaviside"
_PROMPTS = _ROOT / "agents" / "prompts"
_CALL = re.compile(r"""call_agent(?:_json)?\(\s*["']([a-zA-Z0-9_\-]+)["']""")


def test_all_agent_references_have_prompts():
    available = {p.stem for p in _PROMPTS.glob("*.md")}
    dangling: list[str] = []
    for f in _ROOT.rglob("*.py"):
        for m in _CALL.finditer(f.read_text(encoding="utf-8")):
            name = m.group(1)
            if name not in available:
                dangling.append(f"{f.relative_to(_ROOT)}: '{name}'")
    assert not dangling, (
        "call_agent referencing a non-existent prompt (rename left a stale call "
        "site):\n  " + "\n  ".join(dangling)
    )
