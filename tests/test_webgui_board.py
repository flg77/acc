"""`20260903-work-board-webgui` -- the Board's API.

Same projection as the TUI (``acc.work_board``) over the hub's latest
snapshot; interventions are bus signals with the principal as ``actor``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("authlib")
pytest.importorskip("bcrypt")

from fastapi.testclient import TestClient  # noqa: E402


class _FakeObserver:
    def __init__(self, nats_url, collective_id, update_queue, nkey_seed_path=None):
        self.collective_id = collective_id
        self._queue = update_queue
        self.published: list[tuple] = []

    async def connect(self):
        return None

    async def subscribe(self):
        return None

    async def close(self):
        return None

    async def publish(self, subject, payload):
        self.published.append((subject, payload))


@pytest.fixture()
def client(monkeypatch):
    import acc.tui.client as tui_client  # noqa: PLC0415
    monkeypatch.setattr(tui_client, "NATSObserver", _FakeObserver)
    monkeypatch.setenv("ACC_COLLECTIVE_IDS", "sol-01")
    monkeypatch.delenv("ACC_WEBGUI_AUTH_MODE", raising=False)
    from acc.webgui.app import create_app  # noqa: PLC0415
    app = create_app()
    with TestClient(app) as c:
        yield c


def _hub(client):
    return client.app.state.hub


def _seed(client, snapshot: dict) -> None:
    _hub(client)._latest["sol-01"] = snapshot


_SNAP = {
    "collective_id": "sol-01",
    "active_plans": {"p-1": {
        "plan_id": "p-1", "received_ts": 100.0,
        "steps": [
            {"step_id": "s1", "role": "analyst", "depends_on": [], "task_description": "gather"},
            {"step_id": "s2", "role": "coder", "depends_on": ["s1"], "task_description": "build"},
        ],
        "step_progress": {"s1": "RUNNING", "s2": "PENDING"},
        "step_tasks": {"s1": "t1"},
        "step_meta": {"s1": {"iteration_n": 1, "max_iterations": 3, "critique": "more tests"}},
    }},
    "oversight_pending_items": [{"oversight_id": "ov-1", "task_id": "t1", "status": "PENDING",
                                 "summary": "SYSTEM-ACCESS skill shell_exec: Run a process"}],
    "signal_flow_log": [{"ts": 5.0, "signal_type": "TASK_ASSIGN", "agent_id": "", "key_field": "",
                         "task_id": "t-solo", "target_role": "assistant"}],
}


class TestBoardRead:
    def test_unknown_collective_404(self, client):
        assert client.get("/api/board/nope").status_code == 404

    def test_empty_board_has_all_columns(self, client):
        r = client.get("/api/board/sol-01")
        assert r.status_code == 200
        assert [c["status"] for c in r.json()["columns"]] == ["QUEUED", "RUNNING", "BLOCKED", "DONE", "FAILED"]
        assert all(c["items"] == [] for c in r.json()["columns"])

    def test_board_projects_the_snapshot_with_the_blocked_join(self, client):
        _seed(client, _SNAP)
        cols = {c["status"]: c["items"] for c in client.get("/api/board/sol-01").json()["columns"]}
        assert [i["step_id"] for i in cols["QUEUED"]] == ["s2"]
        (blocked,) = cols["BLOCKED"]
        assert blocked["step_id"] == "s1" and blocked["blocked_on"] == "ov-1"
        assert blocked["iteration"] == "1/3" and blocked["critique"] == "more tests"
        assert blocked["can_cancel"] is True and blocked["can_retry"] is False
        (solo,) = cols["RUNNING"]
        assert solo["kind"] == "task" and solo["task_id"] == "t-solo" and solo["role"] == "assistant"


class TestBoardControl:
    def test_plan_step_control_publishes_with_the_principal(self, client):
        r = client.post("/api/board/control", json={
            "collective_id": "sol-01", "kind": "plan_step", "action": "cancel",
            "plan_id": "p-1", "step_id": "s1", "reason": "wrong approach",
        })
        assert r.status_code == 200, r.text
        assert r.json()["signal"] == "PLAN_STEP_CONTROL"
        obs = _hub(client).observer("sol-01")
        (subject, payload) = obs.published[-1]
        assert subject == "acc.sol-01.plan.control"
        assert payload["action"] == "cancel" and payload["step_id"] == "s1"
        assert payload["actor"].startswith("webgui:") and payload["reason"] == "wrong approach"

    def test_reassign_carries_the_role(self, client):
        r = client.post("/api/board/control", json={
            "collective_id": "sol-01", "kind": "plan_step", "action": "reassign",
            "plan_id": "p-1", "step_id": "s1", "role": "senior_analyst",
        })
        assert r.status_code == 200
        assert _hub(client).observer("sol-01").published[-1][1]["role"] == "senior_analyst"

    def test_task_cancel(self, client):
        r = client.post("/api/board/control", json={
            "collective_id": "sol-01", "kind": "task", "action": "cancel", "task_id": "t-solo",
        })
        assert r.status_code == 200 and r.json()["signal"] == "TASK_CANCEL"
        (subject, payload) = _hub(client).observer("sol-01").published[-1]
        assert subject.endswith(".task.cancel") and payload["task_id"] == "t-solo"

    @pytest.mark.parametrize("body,code", [
        ({"kind": "plan_step", "action": "cancel"}, 400),                                   # no ids
        ({"kind": "plan_step", "action": "reassign", "plan_id": "p", "step_id": "s"}, 400),   # no role
        ({"kind": "task", "action": "retry", "task_id": "t"}, 400),                          # tasks only cancel
        ({"kind": "task", "action": "cancel"}, 400),                                        # no task id
        ({"kind": "plan_step", "action": "explode", "plan_id": "p", "step_id": "s"}, 422),   # pattern
    ])
    def test_validation(self, client, body, code):
        r = client.post("/api/board/control", json={"collective_id": "sol-01", **body})
        assert r.status_code == code, r.text
