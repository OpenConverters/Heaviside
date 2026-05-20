"""Unit tests for :mod:`heaviside.knowledge`.

Covers the ``read_knowledge`` helper: existence, ambiguity rejection,
input validation, and the six schema files ported from Proteus.
"""

from __future__ import annotations

import pytest

from heaviside.knowledge import KNOWLEDGE_ROOT, available_topics, read_knowledge


REQUIRED_SCHEMA_FILES = (
    "peas-schema",
    "sas-schema",
    "cas-schema",
    "ras-schema",
    "mas-schema-summary",
    "tas-structure",
)


@pytest.mark.parametrize("name", REQUIRED_SCHEMA_FILES)
def test_schema_knowledge_files_present_and_non_empty(name: str) -> None:
    text = read_knowledge(name)
    assert text.strip(), f"{name}.md exists but is empty"
    # Markdown front character — every Proteus knowledge file opens with #.
    assert text.lstrip().startswith("#"), (
        f"{name}.md does not look like Markdown (no leading #)"
    )


def test_available_topics_lists_components_dir() -> None:
    topics = available_topics()
    assert "components" in topics
    for required in REQUIRED_SCHEMA_FILES:
        assert required in topics["components"], (
            f"{required}.md missing from components/ topic listing"
        )


def test_missing_knowledge_raises_filenotfound() -> None:
    with pytest.raises(FileNotFoundError, match="no such knowledge file"):
        read_knowledge("definitely-not-a-real-schema-name")


def test_empty_name_raises_valueerror() -> None:
    with pytest.raises(ValueError, match="invalid name"):
        read_knowledge("")


@pytest.mark.parametrize("bad", ["foo/bar", "..\\peas-schema"])
def test_pathlike_name_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="invalid name"):
        read_knowledge(bad)


def test_ambiguous_name_raises_lookuperror(tmp_path, monkeypatch) -> None:
    """Two files with the same stem in different subdirs is ambiguous."""
    # Stage a fake tree under the real root by writing into a temporary
    # subdir that is collected by the glob.  We monkeypatch KNOWLEDGE_ROOT
    # to the temp path so the production tree is untouched.
    root = tmp_path
    (root / "a").mkdir()
    (root / "b").mkdir()
    (root / "a" / "dup.md").write_text("# A\n")
    (root / "b" / "dup.md").write_text("# B\n")

    import heaviside.knowledge as kn
    monkeypatch.setattr(kn, "KNOWLEDGE_ROOT", root)

    with pytest.raises(LookupError, match="ambiguous"):
        read_knowledge("dup")


def test_knowledge_root_is_a_directory() -> None:
    assert KNOWLEDGE_ROOT.is_dir()
