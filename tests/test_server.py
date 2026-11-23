"""API contract tests against the offline demo model (no key, no quota)."""

import json
import os

import pytest

os.environ["CC_FAKE_LLM"] = "1"
from fastapi.testclient import TestClient  # noqa: E402

from server.app import app  # noqa: E402

client = TestClient(app)


def events(resp):
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def test_health_and_options():
    assert client.get("/api/health").json()["fake_llm"] is True
    opts = client.get("/api/options").json()
    assert set(opts["tasks"]) == {"service_audit", "incident_trace"} and opts["demo_queries"]["incident_trace"]


@pytest.mark.parametrize("arm", ["compact", "baseline"])
def test_session_ask_streams_events(arm):
    st = client.post("/api/sessions", json={"arm": arm, "task": "service_audit", "size": "medium"}).json()
    assert st["sandbox"]["files"] == 13 and len(st["sandbox"]["turns"]) == 4 and st["transcript"] == []
    with client.stream("POST", f"/api/sessions/{st['id']}/ask", json={"question": st["sandbox"]["prompt"], "kind": "task"}) as r:
        r.read()
        evs = events(r)
    types_ = [e["type"] for e in evs]
    assert types_[-2:] == ["state", "done"] and "model_call" in types_ and "tool_result" in types_
    final_state = evs[-2]["state"]
    tool_refs = [t["ref"] for t in final_state["transcript"] if t["role"] == "tool"]
    assert [e["ref"] for e in evs if e["type"] == "tool_result"] == tool_refs  # streamed refs match
    assert final_state["settings"]["model"] == "demo/offline-scripted"
    if arm == "compact":
        assert "fold" in types_ and final_state["folds"] and final_state["stats"]["folds_model"] >= 1
        final = next(e for e in evs if e["type"] == "final")
        assert final["grade"]["score"] == 1.0
        assert any(t["folded_into"] for t in final_state["transcript"])
        assert final_state["last_view"]["estimate"] <= 6000
    else:  # the plain agent hits the simulated free-tier wall on the medium sandbox
        err = next(e for e in evs if e["type"] == "error")
        assert err["status"] == "request_too_large" and final_state["error"]
    assert client.get(f"/api/sessions/{st['id']}").json()["id"] == st["id"]


def test_results_and_golden_endpoints():
    assert isinstance(client.get("/api/results").json(), list)
    g = client.get("/api/golden").json()
    assert g["dataset"]["version"] == "v1" and len(g["dataset"]["cases"]) == 6
    assert client.get("/api/results/..%2Fetc").status_code == 404
