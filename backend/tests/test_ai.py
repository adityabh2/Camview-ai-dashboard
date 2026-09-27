"""
V2 AI assistant + smart search. The real Claude API is NEVER called: ai._make_client is replaced by a fake.
"""

import json
from types import SimpleNamespace as NS

import pytest

import ai
import db


@pytest.fixture(autouse=True)
def _ai_env(monkeypatch):
    monkeypatch.delenv("CAMVIEW_AI_API_KEY", raising=False)
    monkeypatch.delenv("CAMVIEW_AI_MODEL", raising=False)
    ai.reset_rate_limits()
    yield
    ai.reset_rate_limits()


class FakeClaude:
    """Asks for search_alerts first, then answers citing the first alert the tool returned."""

    def __init__(self, tool_input=None):
        self.calls = []
        self.tool_input = tool_input or {"status": "pending", "size": 3}
        self.messages = self

    def create(self, **params):
        self.calls.append(params)
        msgs = params["messages"]
        last = msgs[-1]["content"]
        if isinstance(last, str):
            return NS(stop_reason="tool_use", usage=NS(input_tokens=100, output_tokens=20), content=[
                NS(type="thinking", thinking="", signature="sig"),
                NS(type="tool_use", id="tu_1", name="search_alerts", input=self.tool_input)])
        result = json.loads(last[0]["content"])
        self.last_result = result
        first = result["items"][0]["alarmId"] if result.get("items") else "none"
        return NS(stop_reason="end_turn", usage=NS(input_tokens=300, output_tokens=60), content=[
            NS(type="text", text=f"**Summary**: {result['total']} pending.\n- {first} needs attention")])


def _configure(monkeypatch, fake):
    monkeypatch.setenv("CAMVIEW_AI_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(ai, "installed", lambda: True)
    monkeypatch.setattr(ai, "_make_client", lambda: fake)


def test_vocabulary_is_scope_filtered(supervisor, app):
    from conftest import as_user
    s, full = supervisor.get("/api/search/vocabulary")
    assert s == 200 and full["types"] and full["centres"] and full["projects"]
    s, p12 = as_user(app, "supervisor.p12@demo.camview").get("/api/search/vocabulary")
    assert s == 200
    assert [p["id"] for p in p12["projects"]] == ["12"]
    assert p12["centres"] and all(c["code"].startswith("CTR-12") for c in p12["centres"])
    assert len(p12["centres"]) < len(full["centres"])
    assert p12["ai"] is False


def test_vocabulary_and_ai_are_internal_only(client_a):
    for path in ("/api/search/vocabulary", "/api/ai/status", "/api/ai/conversations"):
        assert client_a.get(path)[0] == 404
    assert client_a.post("/api/ai/ask", {"message": "hi"})[0] == 404
    assert client_a.post("/api/search/interpret", {"text": "hi"})[0] == 404


def test_status_not_configured_without_key(supervisor):
    s, st = supervisor.get("/api/ai/status")
    assert s == 200 and st["configured"] is False and st["model"] == "claude-opus-5-5"
    assert "sk-" not in json.dumps(st)


def test_ask_returns_503_when_not_configured(supervisor):
    s, r = supervisor.post("/api/ai/ask", {"message": "What needs attention?"})
    assert s == 503 and "CAMVIEW_AI_API_KEY" in r["message"]
    s, r = supervisor.post("/api/search/interpret", {"text": "critical pending"})
    assert s == 503


def test_ask_runs_tools_with_user_scope_and_stores(monkeypatch, app):
    from conftest import as_user
    fake = FakeClaude()
    _configure(monkeypatch, fake)
    u = as_user(app, "supervisor.p12@demo.camview")
    s, r = u.post("/api/ai/ask", {"message": "Kya pending hai?", "tzOffset": 330})
    assert s == 200, r
    items = fake.last_result["items"]
    assert items and all(str(i["projectId"]) == "12" for i in items)          # scope applied inside the tool
    assert all("imageUrl" not in i and "videoUrl" not in i for i in items)     # no media links to the model
    first = items[0]["alarmId"]
    assert first in r["answer"]
    assert r["facts"] == [{"alarmId": first, "projectId": items[0]["projectId"], "label": r["facts"][0]["label"]}]
    assert r["usage"]["input"] == 400 and r["usage"]["output"] == 80 and r["usage"]["tools"] == ["search_alerts"]
    # the tool loop replays the assistant turn verbatim and returns results in one user turn
    second = fake.calls[1]["messages"]
    assert second[-2]["role"] == "assistant" and second[-1]["content"][0]["tool_use_id"] == "tu_1"
    assert fake.calls[0]["system"] == ai.SYSTEM_PROMPT and fake.calls[0]["model"] == "claude-opus-5-5"
    # stored conversation, owner only
    s, conv = u.get(f"/api/ai/conversations/{r['conversationId']}")
    assert s == 200 and [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    assert conv["messages"][1]["facts"][0]["alarmId"] == first
    assert u.get("/api/ai/conversations")[1]["items"][0]["id"] == r["conversationId"]
    other = as_user(app, "supervisor@demo.camview")
    assert other.get(f"/api/ai/conversations/{r['conversationId']}")[0] == 404
    assert other.post("/api/ai/ask", {"message": "x", "conversationId": r["conversationId"]})[0] == 404
    # follow-up question sends earlier turns as plain text
    s, r2 = u.post("/api/ai/ask", {"message": "aur?", "conversationId": r["conversationId"]})
    assert s == 200 and r2["conversationId"] == r["conversationId"]
    hist = fake.calls[2]["messages"]
    assert hist[0] == {"role": "user", "content": "Kya pending hai?"} and hist[1]["role"] == "assistant"


def test_ask_audits_metadata_only(monkeypatch, supervisor):
    _configure(monkeypatch, FakeClaude())
    s, r = supervisor.post("/api/ai/ask", {"message": "secret question text"})
    assert s == 200
    rows = db.rows("SELECT * FROM audit_events WHERE action='ai.ask'")
    assert len(rows) == 1 and rows[0]["resource_id"] == r["conversationId"]
    assert "secret question text" not in json.dumps(rows[0])
    d = json.loads(rows[0]["details"])
    assert d["ok"] is True and d["tools"] == ["search_alerts"] and d["tokensIn"] == 400


def test_ask_rate_limited(monkeypatch, supervisor):
    _configure(monkeypatch, FakeClaude())
    monkeypatch.setattr(ai, "RATE_LIMIT", 2)
    assert supervisor.post("/api/ai/ask", {"message": "a"})[0] == 200
    assert supervisor.post("/api/ai/ask", {"message": "b"})[0] == 200
    s, r = supervisor.post("/api/ai/ask", {"message": "c"})
    assert s == 429 and "questions" in r["message"]


def test_api_errors_are_friendly(monkeypatch, supervisor):
    class Boom:
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **_):
            raise RuntimeError("upstream stack trace details")
    _configure(monkeypatch, Boom())
    s, r = supervisor.post("/api/ai/ask", {"message": "hello"})
    assert s == 502 and "stack" not in r["message"] and "Traceback" not in json.dumps(r)


def test_get_alert_tool_respects_scope(app):
    from conftest import as_user
    import rbac
    full = as_user(app, "supervisor@demo.camview")
    items = full.get("/api/queue?status=all&range=all&size=200")[1]["items"]
    p7 = next(a for a in items if str(a["projectId"]) == "7")
    with app.test_request_context():
        u12 = rbac.load_user("u-super12")
        out = ai.tool_get_alert(u12, {"alarmId": p7["alarmId"]}, ai._tz(0))
        assert out["available"] is False
        u = rbac.load_user("u-super")
        out = ai.tool_get_alert(u, {"alarmId": p7["alarmId"], "projectId": "7"}, ai._tz(0))
        assert out["available"] is True and out["alarmId"] == p7["alarmId"]
        assert "videoUrl" not in out and "imageUrls" not in out and "notes" not in out
        stats = ai.tool_summary_stats(u12, {}, ai._tz(0))
        assert all(c["centre"].startswith("CTR-12") for c in stats["topCentres"] if c["centre"] != "Unmapped")
        centres = ai.tool_list_centres(u12, {}, ai._tz(0))
        assert centres["items"] and all(c["centre"].startswith("CTR-12") for c in centres["items"] if c["centre"] != "Unmapped")


def test_interpret_validates_against_vocabulary(monkeypatch, supervisor):
    class Interp:
        def __init__(self):
            self.messages = self

        def create(self, **params):
            assert params["output_config"]["format"]["type"] == "json_schema"
            return NS(stop_reason="end_turn", usage=NS(input_tokens=10, output_tokens=5), content=[NS(type="text", text=json.dumps({
                "status": "pending", "priority": "critical", "type": "1", "centre": "CTR-0701", "camera": "NOPE-1",
                "client": None, "exam": None, "projectId": None, "kind": None, "from": "2026-09-27", "to": "bad",
                "search": None, "city": None}))])
    _configure(monkeypatch, Interp())
    s, r = supervisor.post("/api/search/interpret", {"text": "critical pending mobile at ctr-0701 today"})
    assert s == 200, r
    assert r["filters"] == {"status": "pending", "priority": "critical", "type": "1", "centre": "CTR-0701", "from": "2026-09-27"}
    assert sorted(r["dropped"]) == ["camera", "to"]
