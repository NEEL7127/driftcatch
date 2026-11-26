"""The local UI.

Covers the two things that matter most: every route is a thin wrapper over the
same engine the CLI uses, and the server refuses to expose recordings on a
network interface.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import driftcatch as drift  # noqa: E402
from driftcatch.errors import DriftCatchError  # noqa: E402
from driftcatch.ui.server import EDITABLE_SETTINGS, build_app, serve  # noqa: E402
from tests.integration.fake_provider import FakeProvider, chat_body, post_chat  # noqa: E402


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    drift.reset_config()
    cfg = drift.config(recordings_dir=tmp_path / "recordings")

    provider = FakeProvider(
        [
            chat_body(tool_name="lookup_order", tool_args={"id": 1182}),
            chat_body(content="Refunded $40"),
        ]
    )

    @drift.tool
    def lookup_order(order_id: int) -> dict[str, Any]:
        return {"id": order_id, "total": 40}

    def agent() -> None:
        with provider.client() as http:
            post_chat(http)
        lookup_order(1182)

    with drift.take("refund"):
        agent()

    yield TestClient(build_app(cfg))
    drift.reset_config()


def test_index_serves_the_app(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "DriftCatch" in response.text


def test_summary_reports_counts_and_coverage(client: TestClient) -> None:
    data = client.get("/api/summary").json()
    assert data["total"] == 1
    assert data["counts"]["pending"] == 1
    assert "percent" in data["coverage"]


def test_recordings_list_and_detail(client: TestClient) -> None:
    listing = client.get("/api/recordings").json()
    assert [r["name"] for r in listing] == ["refund"]

    detail = client.get("/api/recordings/refund").json()
    assert detail["status"] == "pending"
    assert detail["trajectory"] == ["lookup_order"]
    assert len(detail["events_detail"]) == 2
    assert detail["fingerprint"]["prompt_hash"].startswith("sha256:")
    assert detail["secrets"] == []


def test_missing_recording_is_a_404(client: TestClient) -> None:
    assert client.get("/api/recordings/nope").status_code == 404


def test_approve_then_reject_is_refused(client: TestClient) -> None:
    """The UI enforces the same state machine as the CLI, not a looser one."""
    approved = client.post("/api/recordings/refund/approve", json={"by": "ui"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    rejected = client.post("/api/recordings/refund/reject")
    assert rejected.status_code == 400
    assert "can only move to" in rejected.json()["detail"]


def test_coverage_route_flags_untested_tools(client: TestClient) -> None:
    data = client.get("/api/coverage").json()
    assert data["tools"]["lookup_order"]["covered"] is True
    assert data["tools"]["lookup_order"]["error_path_covered"] is False


def test_compare_route_attributes_a_change(client: TestClient) -> None:
    data = client.get("/api/compare", params={"golden": "refund", "candidate": "refund"}).json()
    assert data["matched"] is True
    assert data["attribution"]["changed_inputs"] == []


def test_config_route_lists_editable_settings(client: TestClient) -> None:
    data = client.get("/api/config").json()
    assert set(data["editable"]) == set(EDITABLE_SETTINGS)
    assert "matcher" in data["values"]


def test_config_refuses_to_edit_safety_settings(client: TestClient) -> None:
    """Redaction and approval gating must not be weakenable from a web page."""
    response = client.post("/api/config", json={"redactors": []})
    assert response.status_code == 400
    assert "not editable from the UI" in response.json()["detail"]


def test_config_write_persists_editable_settings(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    response = client.post("/api/config", json={"matcher": "positional"})
    assert response.status_code == 200
    written = Path(response.json()["written"])
    assert written.name == "driftcatch.toml"
    assert 'matcher = "positional"' in written.read_text(encoding="utf-8")


def test_serve_refuses_a_non_loopback_bind() -> None:
    with pytest.raises(DriftCatchError, match="loopback-only"):
        serve(host="0.0.0.0", port=7777)
