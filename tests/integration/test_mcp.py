"""The MCP server.

Two things are tested here, and the second matters more than the first:

1. the tools work and return parseable data;
2. the tools that would let an agent **grant its own approval** are absent.

An agent's reflex on a red test is to make it green. If it could approve a
recording it eventually would, and the approval gate - the thing that stops a
bug being laundered into a specification - would be worthless.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp")

import driftcatch as drift  # noqa: E402
from driftcatch.mcp_server import INSTRUCTIONS, build_server  # noqa: E402
from tests.integration.fake_provider import FakeProvider, chat_body, post_chat  # noqa: E402


@pytest.fixture
def project(tmp_path: Path) -> Iterator[Path]:
    directory = tmp_path / "recordings"
    drift.reset_config()
    drift.config(recordings_dir=directory)

    @drift.tool
    def lookup_order(order_id: int) -> dict[str, Any]:
        return {"id": order_id, "total": 40 + order_id}

    @drift.tool
    def issue_refund(order_id: int, amount: int) -> dict[str, Any]:
        return {"ok": True}

    for index in range(6):
        provider = FakeProvider([chat_body(content="ok")])
        with drift.take(f"case_{index}"):
            with provider.client() as http:
                post_chat(http)
            order = lookup_order(index)
            issue_refund(index, order["total"])
        drift.approve(f"case_{index}", by="test")

    # One left unapproved on purpose.
    provider = FakeProvider([chat_body(content="ok")])
    with drift.take("case_pending"):
        with provider.client() as http:
            post_chat(http)
        lookup_order(99)

    yield directory
    drift.reset_config()


async def call(server: Any, tool: str, /, **kwargs: Any) -> Any:
    """Invoke a tool the way a client would.

    ``tool`` is positional-only so it cannot collide with a tool argument that
    happens to be called ``name``.
    """
    result = await server.call_tool(tool, kwargs)
    return result.structured_content


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --------------------------------------------------- the safety boundary


@pytest.mark.anyio
async def test_mutating_tools_are_not_exposed(project: Path) -> None:
    """The whole safety model rests on a human holding the approval gate."""
    server = build_server(str(project))
    names = {tool.name for tool in await server.list_tools()}

    forbidden = {"approve", "reject", "record", "restore", "check", "review", "prune"}
    assert not (names & forbidden), (
        f"an agent must not be able to call {names & forbidden}: approving a "
        "recording means a human confirmed the behaviour is correct"
    )


@pytest.mark.anyio
async def test_read_only_tools_are_annotated_as_such(project: Path) -> None:
    server = build_server(str(project))
    tools = {t.name: t for t in await server.list_tools()}
    for name in ("status", "coverage", "show", "bisect", "invariants", "scan", "audit"):
        assert tools[name].annotations is not None
        assert tools[name].annotations.read_only_hint is True, name


@pytest.mark.anyio
async def test_learn_proposes_but_never_enforces(project: Path) -> None:
    server = build_server(str(project))
    result = await call(server, "learn")
    assert result["mined"] >= 1
    assert result["enforced"] == 0
    assert "approve" in result["note"]
    assert all(not i["approved"] for i in result["invariants"])


@pytest.mark.anyio
async def test_pending_approvals_hands_the_decision_back(project: Path) -> None:
    server = build_server(str(project))
    result = await call(server, "pending_approvals")
    assert "case_pending" in result["pending_recordings"]
    assert result["actions_for_the_human"]
    assert "drift review" in result["actions_for_the_human"][0]["human_runs"]
    assert "cannot perform" in result["note"]


def test_instructions_warn_against_the_reflex_fix() -> None:
    assert "Do NOT try to" in INSTRUCTIONS
    assert "does not run" in INSTRUCTIONS
    assert "never a production cache" in INSTRUCTIONS


# ------------------------------------------------------------- the tools


@pytest.mark.anyio
async def test_explain(project: Path) -> None:
    server = build_server(str(project))
    result = await call(server, "explain")
    assert "purpose" in result
    assert "exit_codes" in result


@pytest.mark.anyio
async def test_status(project: Path) -> None:
    server = build_server(str(project))
    result = await call(server, "status")
    assert result["total"] == 7
    assert result["counts"]["approved"] == 6
    assert result["counts"]["pending"] == 1


@pytest.mark.anyio
async def test_coverage(project: Path) -> None:
    server = build_server(str(project))
    result = await call(server, "coverage")
    assert "percent" in result
    assert "lookup_order" in result["tools"]


@pytest.mark.anyio
async def test_show(project: Path) -> None:
    server = build_server(str(project))
    result = await call(server, "show", name="case_0")
    assert result["name"] == "case_0"
    assert result["trajectory"] == ["lookup_order", "issue_refund"]
    assert any(e["type"] == "tool" for e in result["events_detail"])


@pytest.mark.anyio
async def test_show_missing_returns_an_error_not_an_exception(project: Path) -> None:
    server = build_server(str(project))
    result = await call(server, "show", name="nope")
    assert "error" in result


@pytest.mark.anyio
async def test_bisect_identical_recordings(project: Path) -> None:
    server = build_server(str(project))
    result = await call(server, "bisect", golden="case_0", candidate="case_1")
    assert "attribution" in result
    assert result["matched"] is True


@pytest.mark.anyio
async def test_bisect_without_a_target_explains_itself(project: Path) -> None:
    server = build_server(str(project))
    result = await call(server, "bisect", golden="case_0")
    assert "error" in result


@pytest.mark.anyio
async def test_invariants_and_scan(project: Path) -> None:
    server = build_server(str(project))
    await call(server, "learn")
    listed = await call(server, "invariants")
    assert listed["total"] >= 1
    assert listed["enforced"] == 0

    scanned = await call(server, "scan")
    assert scanned["clean"] is True


@pytest.mark.anyio
async def test_audit_reports_match_keys(project: Path) -> None:
    server = build_server(str(project))
    result = await call(server, "audit", name="case_0")
    assert result["matcher"] == "field"
    assert len(result["events"]) == 3


@pytest.mark.anyio
async def test_server_advertises_the_expected_toolset(project: Path) -> None:
    """The exposed surface is pinned on purpose.

    Equality rather than a subset check, so adding a tool to the MCP server is
    always a deliberate edit here. The risk being guarded is specific: an agent
    looking at a red test will reach for the fastest way to make it green, and
    `approve` is that way. Every name below is read-only; `approve`, `reject`,
    `record`, `restore` and `check --live` stay out of an agent's hands.
    """
    server = build_server(str(project))
    names = {tool.name for tool in await server.list_tools()}
    assert names == {
        "explain",
        "status",
        "coverage",
        "show",
        "bisect",
        "learn",
        "invariants",
        "contracts",
        "scan",
        "audit",
        "pending_approvals",
    }


@pytest.mark.anyio
async def test_contracts_tool_returns_a_report(project: Path) -> None:
    server = build_server(str(project))
    result = await call(server, "contracts")
    assert "tools" in result
    assert "corpus" in result
    assert "min_corpus_for_removal" in result


@pytest.mark.anyio
async def test_no_exposed_tool_is_destructive(project: Path) -> None:
    """The annotation is what an MCP client shows a user before allowing a call.

    Every tool here must be non-destructive. Most are outright read-only; the
    sole exception is `learn`, which writes mined rules as *pending* and cannot
    enforce them, so an agent cannot turn its own guesses into a gate.
    """
    server = build_server(str(project))
    writers: set[str] = set()
    for tool in await server.list_tools():
        annotations = tool.annotations
        assert annotations is not None, f"{tool.name} carries no annotations"
        assert annotations.destructive_hint is False, f"{tool.name} is marked destructive"
        if annotations.read_only_hint is not True:
            writers.add(tool.name)

    assert writers == {"learn"}, (
        f"only 'learn' may write, but these also do: {sorted(writers - {'learn'})}. "
        f"An agent facing a red test reaches for the fastest way to make it green."
    )


@pytest.mark.anyio
async def test_learn_proposes_rules_without_enforcing_them(project: Path) -> None:
    """The reason `learn` is safe to expose despite writing to disk."""
    result = await call(build_server(str(project)), "learn")
    assert result["enforced"] == 0, "an agent must not be able to enforce a rule it mined"
