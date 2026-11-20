"""
N. browser end-to-end

LOGIN → DASHBOARD → ALERT LIST → OPEN ALERT → DETAIL → VIDEO → IMAGE → CONTEXT → CAMERA HEALTH → RECORDING HEALTH
→ VALID → TICKET → CLIENT ROUTING → LOGIN AS CLIENT → CLIENT ALERT; INVALID; EXCEPTION; a conflicting decision;
and a live refresh while a video plays (the alert is not replaced, playback continues).
"""

import requests

from conftest import CLIENT, OTHER_CLIENT, SUPERVISOR, api_session


def text(page, sel):
    """Visible text, case-folded (the UI uppercases some labels with CSS)."""
    return page.inner_text(sel).casefold()


def open_alert(page, server, aid):
    page.goto(f"{server}/#/alerts?status=all")
    page.wait_for_selector('[data-view="needs_review"]')                  # operational views on the one queue
    page.wait_for_selector(f'.qrow[data-id="{aid}"]')
    page.click(f'.qrow[data-id="{aid}"]')
    page.wait_for_selector("#rv-title h2")
    assert f"#/alerts/{aid}" in page.url


def wait_playing(page, seconds=0.6):
    page.wait_for_function("() => { const v = document.querySelector('#rv-video video'); return v && v.readyState >= 2; }")
    page.evaluate("() => { const v = document.querySelector('#rv-video video'); v.muted = true; return v.play(); }")
    page.wait_for_function(f"() => document.querySelector('#rv-video video').currentTime > {seconds}")


def test_valid_flow_from_login_to_the_client_portal(login, server):
    page = login(SUPERVISOR)
    page.wait_for_selector("text=ALERTS TODAY", timeout=20000)             # DASHBOARD
    open_alert(page, server, "E2E-1")                                     # ALERT LIST → OPEN ALERT
    head = text(page, ".rv-head")
    assert "e2e-1" in head and "mobile phone detected" in head            # DETAIL (nomenclature from the Dictionary)
    assert "staff selection commission" in head and "ssc cgl 2026" in head
    ctx = text(page, ".rv-grid")                                          # CONTEXT: resolved, nothing typed
    for expected in ("4100", "ssc-2026-034", "tc-18", "c018", "room204", "1001_1"):   # projectId + code, TC…
        assert expected in ctx, expected
    src = page.get_attribute("#rv-video video", "src")                    # VIDEO through the resolver, not a stored link
    assert src.startswith("/api/media/E2E-1/video/0")
    wait_playing(page)
    page.wait_for_function("() => { const i = document.querySelector('#rv-images img'); return i && i.naturalWidth > 0; }")
    assert page.get_attribute("#rv-images img", "src").startswith("/api/media/E2E-1/image/0")    # IMAGE
    health = text(page, ".rv-grid aside")
    assert "● online" in health                                           # CAMERA HEALTH
    assert "● recording" in health                                        # RECORDING HEALTH (separate source)
    gate = text(page, "#rv-gate")                                         # READINESS GATE, before the decision
    assert "not ready" in gate and "operator decision is pending" in gate
    assert "data quality complete" in gate and "evidence complete" in gate
    page.wait_for_selector("#rv-story")                                   # EVENT STORY (secondary, collapsible)
    story = text(page, "#rv-story")
    assert "why this alert" in story and "video available" in story
    page.uncheck("#rv-auto")                                              # stay on this alert after deciding
    page.click('[data-decide="valid"]')                                   # VALID
    page.wait_for_selector(".rv-result.valid")
    result = text(page, ".rv-result.valid")
    assert "marked valid" in result and "tkt-" in result                  # TICKET
    assert "delivered to staff selection commission" in result            # CLIENT ROUTING
    page.wait_for_selector("#rv-ticket >> text=Delivered")
    page.wait_for_selector("#rv-gate >> text=VISIBLE TO CLIENT")         # the gate follows the delivery
    ref = page.inner_text("#rv-title .mono")
    assert ref.startswith("TKT-")

    client = login(CLIENT)                                                # LOGIN AS CLIENT
    client.goto(f"{server}/#/client/alerts")
    client.wait_for_selector("text=E2E-1")
    client.click("text=E2E-1")
    client.wait_for_selector("text=SSC CGL 2026")                         # CLIENT ALERT with its exam
    body = text(client, "body")
    assert "e2e-1" in body and "e2e supervisor" not in body               # no operator names reach the client
    client.wait_for_function("() => [...document.images].some((i) => i.src.includes('/api/client/evidence/E2E-1/') && i.naturalWidth > 0)")

    other = api_session(server, OTHER_CLIENT)                             # another client: nothing
    assert other.get(f"{server}/api/client/alerts/E2E-1").status_code == 404
    assert all(a["alarmId"] != "E2E-1" for a in other.get(f"{server}/api/client/alerts").json()["items"])


def test_invalid_and_exception_never_reach_the_client(login, server):
    page = login(SUPERVISOR)
    for aid, result, label in (("E2E-2", "invalid", "Marked INVALID"), ("E2E-3", "exception", "Marked EXCEPTION")):
        open_alert(page, server, aid)
        page.uncheck("#rv-auto")
        page.click(f'[data-decide="{result}"]')
        page.wait_for_selector(f".rv-result.{result}")
        assert label.casefold() in text(page, f".rv-result.{result}")
    open_alert(page, server, "E2E-2")                                     # recording health: online but NOT recording
    aside = text(page, ".rv-grid aside")
    assert "not recording" in aside and "● online" in aside
    client = api_session(server, CLIENT)
    ids = {a["alarmId"] for a in client.get(f"{server}/api/client/alerts").json()["items"]}
    assert not ids & {"E2E-2", "E2E-3"}


def test_a_conflicting_decision_is_shown_not_overwritten(login, server):
    page = login(SUPERVISOR)
    page.route("**/api/events*", lambda r: r.abort())                     # no live refresh: the screen is stale on purpose
    open_alert(page, server, "E2E-5")
    page.uncheck("#rv-auto")
    admin = api_session(server, ("admin@e2e.test", "e2e-admin-pass"))
    r = admin.post(f"{server}/api/queue/E2E-5/decide", json={"result": "valid", "projectId": 4100, "expectedRevision": 0})
    assert r.status_code == 200, r.text
    page.click('[data-decide="invalid"]')
    page.wait_for_selector(".toast >> text=changed this alert to VALID")
    page.wait_for_selector('#rv-title .dec.v-valid')                     # the badge shows the other reviewer's VALID
    st = admin.get(f"{server}/api/queue/E2E-5?projectId=4100").json()["alert"]
    assert st["operatorDecision"] == "valid" and st["revision"] == 1      # the stale decision was NOT saved


def test_live_refresh_while_watching_video_keeps_the_alert_and_position(login, server, camview):
    page = login(SUPERVISOR)
    open_alert(page, server, "E2E-4")
    wait_playing(page, 1.0)
    page.evaluate("() => { document.querySelector('#rv-video video').dataset.mark = 'same-element'; }")
    before = page.evaluate("() => document.querySelector('#rv-video video').currentTime")
    camview.add("E2E-NEW", 1003, minutes_ago=0)                         # a new alert arrives in Camview…
    admin = api_session(server, ("admin@e2e.test", "e2e-admin-pass"))
    admin.post(f"{server}/api/queue/E2E-6/decide", json={"result": "invalid", "projectId": 4100})   # …and data changes
    page.wait_for_selector("#rv-new .new-alerts", timeout=30000)          # the refresh reached this screen
    assert "#/alerts/E2E-4" in page.url, "the alert being reviewed must never be replaced"
    assert page.query_selector('#rv-video video[data-mark="same-element"]'), "the player was re-mounted"
    after = page.evaluate("() => document.querySelector('#rv-video video').currentTime")
    paused = page.evaluate("() => document.querySelector('#rv-video video').paused")
    assert after >= before and not paused, (before, after, paused)
    assert "e2e-4" in text(page, ".rv-head")


def test_health_endpoints_and_request_ids(server):
    assert requests.get(f"{server}/healthz").json() == {"status": "ok"}
    r = requests.get(f"{server}/readyz")
    assert r.status_code == 200 and r.json()["checks"]["jobWorker"] == "ok"
    assert r.headers.get("X-Request-ID")
