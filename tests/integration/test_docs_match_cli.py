"""Documentation that promises a command must promise a real one.

This project has already shipped a README documenting two commands that did not
exist, and a later docs pass invented `drift approve` before it was built. Both
are the same failure: prose and code drifting apart with nothing watching.

A testing tool that cannot keep its own documentation honest is a poor
advertisement for itself, so the check is automated rather than remembered.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from driftcatch._name import CLI_NAME
from driftcatch.cli import app

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"

#: Fenced code blocks and inline code spans. Only these are scanned. Prose says
#: things like "a drift report" and "catch model drift from a cache", and
#: flagging those would make the check cry wolf until somebody deleted it.
_FENCED = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)
_INLINE = re.compile(r"`([^`\n]+)`")

#: A shell invocation: `drift <command>` at the start of a line. Spaces and tabs
#: only, never ``\s``, which would span a newline and make
#: ``import driftcatch as drift`` followed by ``from x import y`` look like a
#: command called "from".
_INVOCATION = re.compile(
    rf"^[ \t]*(?:\$[ \t]*)?{re.escape(CLI_NAME)}[ \t]+([a-z][a-z-]*)",
    re.MULTILINE,
)


def invoked_commands(text: str) -> set[str]:
    """Command names invoked in code blocks or inline code spans."""
    snippets = _FENCED.findall(text) + _INLINE.findall(text)
    return {match for snippet in snippets for match in _INVOCATION.findall(snippet)}


def real_commands() -> set[str]:
    """Every command Typer actually registered."""
    names: set[str] = set()
    for command in app.registered_commands:
        name = command.name or (command.callback.__name__ if command.callback else None)
        if name:
            names.add(name.replace("_", "-"))
    return names


def documentation_files() -> list[Path]:
    files = sorted(DOCS.glob("*.md"))
    files.extend([ROOT / "README.md", ROOT / "CONTRIBUTING.md"])
    return [f for f in files if f.exists()]


# ------------------------------------------------------ guarding the guard


def test_there_are_commands_to_check() -> None:
    """A parser that returned nothing would pass every document silently."""
    found = real_commands()
    assert len(found) > 10, found
    assert {"status", "approve", "check"} <= found


def test_the_parser_finds_commands_and_ignores_prose() -> None:
    """Pin both halves: what it must catch, and what it must not."""
    catches = invoked_commands("```bash\ndrift nonexistent --flag\n```")
    assert catches == {"nonexistent"}

    assert invoked_commands("A drift report explains the change.") == set()
    assert invoked_commands("```python\nimport driftcatch as drift\nfrom x import y\n```") == set()
    assert invoked_commands("`drift status`") == {"status"}


# --------------------------------------------------------- the real checks


@pytest.mark.parametrize("doc", documentation_files(), ids=lambda p: p.name)
def test_every_documented_command_exists(doc: Path) -> None:
    known = real_commands()
    unknown = sorted(
        word for word in invoked_commands(doc.read_text(encoding="utf-8")) if word not in known
    )
    assert not unknown, (
        f"{doc.relative_to(ROOT)} documents commands that do not exist: {unknown}. "
        f"Real commands: {sorted(known)}"
    )


def test_the_cli_reference_covers_every_command() -> None:
    """The other direction: a command nobody documented is a command nobody uses."""
    reference = (DOCS / "cli.md").read_text(encoding="utf-8")
    missing = sorted(name for name in real_commands() if f"`{CLI_NAME} {name}" not in reference)
    assert not missing, f"docs/cli.md does not mention: {missing}"


def test_documented_exit_codes_match_the_enum() -> None:
    from driftcatch.agentio import ExitCode

    reference = (DOCS / "cli.md").read_text(encoding="utf-8")
    for code in ExitCode:
        assert f"| {int(code)} | `{code.name}` |" in reference, (
            f"docs/cli.md does not document {code.name} = {int(code)}"
        )


# ------------------------------------------------------------- navigation


def _index_links() -> set[str]:
    index = (DOCS / "index.md").read_text(encoding="utf-8")
    return set(re.findall(r"\]\(([a-zA-Z0-9_.-]+\.md)[^)]*\)", index))


def test_every_doc_linked_from_the_index_exists() -> None:
    broken = sorted(target for target in _index_links() if not (DOCS / target).exists())
    assert not broken, f"docs/index.md links to missing files: {broken}"


def test_every_doc_is_reachable_from_the_index() -> None:
    """A page nobody can navigate to may as well not be written."""
    linked = _index_links()
    orphans = sorted(
        path.name
        for path in DOCS.glob("*.md")
        if path.name != "index.md" and path.name not in linked
    )
    assert not orphans, f"not linked from docs/index.md: {orphans}"


# ------------------------------------------- the AGENTS.md section


def test_the_agents_section_only_invokes_real_commands() -> None:
    """`drift init` writes this into the user's project.

    It told agents to run `drift record --update`, which pytest rejects. A
    template that instructs an agent to run a failing command is worse than no
    template: the agent believes the workflow is broken, not the instructions.
    """
    from driftcatch.agentio import AGENTS_SECTION

    known = real_commands()
    unknown = sorted(word for word in invoked_commands(AGENTS_SECTION) if word not in known)
    assert not unknown, f"AGENTS.md section invokes unknown commands: {unknown}"


def test_the_agents_section_uses_flags_the_cli_accepts() -> None:
    """Catch the specific class of bug that `--update` was."""
    from driftcatch.agentio import AGENTS_SECTION

    assert "drift record --update" not in AGENTS_SECTION, (
        "pytest rejects --update; the flag is --drift-update"
    )
    assert "--drift-update" in AGENTS_SECTION


# ------------------------------------------------------- the demo tapes


def tape_files() -> list[Path]:
    return sorted((ROOT / "demo" / "tapes").glob("*.tape"))


@pytest.mark.parametrize("tape", tape_files(), ids=lambda p: p.name)
def test_every_tape_command_exists(tape: Path) -> None:
    """A VHS tape films a real terminal, so a bad command is filmed failing.

    The tape's `Type "..."` lines are the commands that will be run, so they
    are checked the same way documentation is.
    """
    known = real_commands()
    typed = re.findall(r'Type\s+"([^"]*)"', tape.read_text(encoding="utf-8"))
    invoked = {match for line in typed for match in _INVOCATION.findall(line)}
    unknown = sorted(word for word in invoked if word not in known)
    assert not unknown, f"{tape.name} types commands that do not exist: {unknown}"


def test_there_are_tapes_to_check() -> None:
    assert tape_files(), "no tapes found; the parametrised check above would vacuously pass"
