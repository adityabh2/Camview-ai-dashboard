"""
Browser scenarios D–P of the production-hardening checklist (A, B, C and M are in test_review_e2e.py).

D  Camview VALID, no operator VALID → client does not see it
E  several exams → each alert reaches its own exam's client
F  several clients → no cross-client visibility (portal + direct URLs)
G  camera online + recording unknown          H  camera online + NOT recording        I  camera offline
J  video unavailable, image works             K  image unavailable, video works       L  both unavailable
N  server restart during delivery → delivered exactly once after the restart
O  double VALID → one ticket, one delivery    P  two operators decide at the same time → one decision, no overwrite
"""

import requests

from conftest import (ADMIN, CLIENT, OPERATOR2, OTHER_CLIENT, PROJECT, SUPERVISOR, api_session, push_health, seed)


def text(page, sel):
    return page.inner_text(sel).casefold()


def open_alert(page, base, aid, project=PROJECT):
    page.goto(f"{base}/#/alerts/{aid}?projectId={project}")
    page.wait_for_selector("#rv-title h2")


def client_ids(base, who):
    return {a["alarmId"] for a in api_session(base, who).get(f"{base}/api/client/alerts").json()["items"]}


def tickets_for(base, aid):
    s = api_session(base, ADMIN)
    return [t for t in s.get(f"{base}/api/tickets", params={"search": aid, "size": 50}).json()["items"] if t["alarmId"] == aid]


def test_D_camview_valid_alone_never_reaches_the_client(login, server):
    page = login(SUPERVISOR)
    open_alert(page, server, "E2E-CVVALID")
    assert "valid · camview" in text(page, "#rv-title")                 # Camview's own status is shown as such…
    assert "E2E-CVVALID" not in client_ids(server, CLIENT)             # …but it publishes nothing
    client = login(CLIENT)
    client.goto(f"{server}/#/client/alerts/E2E-CVVALID")
    client.wait_for_selector("text=/not available/i")


def test_E_F_each_exam_reaches_only_its_own_client(login, server):
    page = login(SUPERVISOR)
    open_alert(page, server, "E2E-B1", project="4200")
    head = text(page, ".rv-head")
    assert "other board" in head and "ob recruitment 2026" in head      # resolved from project 4200's exam
    page.uncheck("#rv-auto")
    page.click('[data-decide="valid"]')
    page.wait_for_selector(".rv-result.valid")
    assert "delivered to other board" in text(page, ".rv-result.valid")
    assert "E2E-B1" in client_ids(server, OTHER_CLIENT)
    assert "E2E-B1" not in client_ids(server, CLIENT)
    ssc = login(CLIENT)                                                 # direct URL from another client: nothing
    ssc.goto(f"{server}/#/client/alerts/E2E-B1")
    ssc.wait_for_selector("text=/not available/i")
    s = api_session(server, CLIENT)
    assert s.get(f"{server}/api/client/alerts/E2E-B1").status_code == 404
    assert s.get(f"{server}/api/client/evidence/E2E-B1/image/0").status_code == 404
    assert s.get(f"{server}/api/queue/E2E-B1").status_code == 404        # internal API: behind the wall


def test_G_H_I_camera_and_recording_are_separate_truths(login, server):
    push_health(server)                                                 # fresh heartbeats
    page = login(SUPERVISOR)
    cases = (("E2E-NOVIDEO", "● online", "recording unknown"),         # G camera 1004
             ("E2E-2", "● online", "not recording"),                   # H camera 1002
             ("E2E-NOMEDIA", "● offline", "recording unknown"))        # I camera 1005
    for aid, cam, rec in cases:
        open_alert(page, server, aid)
        aside = text(page, ".rv-grid aside")
        assert cam in aside and rec in aside, (aid, aside)
        assert "● recording" not in aside, f"{aid}: recording must never be claimed without a source"


def _media_state(page):
    page.wait_for_timeout(300)
    return page.evaluate("""() => ({
        videoUnavailable: !!document.querySelector('#rv-video .pl-unavail'),
        videoReady: (document.querySelector('#rv-video video')?.readyState || 0) >= 1,
        imageUnavailable: !!document.querySelector('#rv-images .pl-unavail'),
        imageLoaded: (document.querySelector('#rv-images img')?.naturalWidth || 0) > 0 })""")


def test_J_K_L_one_broken_medium_never_breaks_the_page(login, server):
    page = login(SUPERVISOR)
    open_alert(page, server, "E2E-NOVIDEO")                            # J
    page.wait_for_selector("#rv-video .pl-unavail")
    page.wait_for_function("() => (document.querySelector('#rv-images img')?.naturalWidth || 0) > 0")
    assert "video unavailable" in text(page, "#rv-video")
    assert page.query_selector("#rv-video [data-retry-video]")          # a retry, not a dead end
    open_alert(page, server, "E2E-NOIMAGE")                            # K
    page.wait_for_selector("#rv-images .pl-unavail")
    page.wait_for_function("() => (document.querySelector('#rv-video video')?.readyState || 0) >= 1")
    open_alert(page, server, "E2E-NOMEDIA")                            # L
    page.wait_for_selector("#rv-video .pl-unavail")
    page.wait_for_selector("#rv-images .pl-unavail")
    assert page.query_selector('[data-decide="valid"]').is_enabled()   # the review itself still works
    page.click("#rv-images [data-retry-image]")                        # retry re-asks the server, fails cleanly again
    page.wait_for_selector("#rv-images .pl-unavail")


def test_O_double_valid_creates_one_ticket(login, server):
    page = login(SUPERVISOR)
    open_alert(page, server, "E2E-DBL")
    page.uncheck("#rv-auto")
    page.dblclick('[data-decide="valid"]')
    page.keyboard.press("v")                                           # and the keyboard shortcut on top
    page.wait_for_selector(".rv-result.valid")
    page.wait_for_timeout(800)
    t = tickets_for(server, "E2E-DBL")
    assert len(t) == 1 and t[0]["deliveryStatus"] == "delivered"
    st = api_session(server, ADMIN).get(f"{server}/api/queue/E2E-DBL?projectId={PROJECT}").json()["alert"]
    assert st["revision"] == 1 and st["operatorDecision"] == "valid"
    assert list(client_ids(server, CLIENT)).count("E2E-DBL") == 1


def test_P_two_operators_at_the_same_time(login, server):
    a, b = login(SUPERVISOR), login(OPERATOR2)
    for p in (a, b):
        p.route("**/api/events*", lambda r: r.abort())                 # both look at the same, soon outdated, state
        open_alert(p, server, "E2E-TWO")
        p.uncheck("#rv-auto")
    a.click('[data-decide="valid"]', no_wait_after=True)
    b.click('[data-decide="invalid"]', no_wait_after=True)
    a.wait_for_timeout(1500)
    st = api_session(server, ADMIN).get(f"{server}/api/queue/E2E-TWO?projectId={PROJECT}").json()["alert"]
    assert st["revision"] == 1, "exactly one decision is saved; the other is a conflict, never an overwrite"
    loser = b if st["operatorDecision"] == "valid" else a
    assert loser.query_selector(".toast >> text=/changed this alert/i"), "the second operator is told, not overwritten"
    assert len(tickets_for(server, "E2E-TWO")) == (1 if st["operatorDecision"] == "valid" else 0)


def test_N_restart_during_delivery_delivers_exactly_once(fresh_server, login):
    srv = fresh_server
    srv.extra_env["CAMVIEW_FAULTS"] = "delivery_fail"                  # the first process cannot deliver
    srv.start()
    seed(srv.base)
    page = login(SUPERVISOR, base=srv.base)
    open_alert(page, srv.base, "E2E-RESTART")
    page.uncheck("#rv-auto")
    page.click('[data-decide="valid"]')
    page.wait_for_selector(".rv-result.valid")
    strip = text(page, ".rv-result.valid")
    assert "sending to staff selection commission" in strip and "delivered" not in strip.replace("retried", "")
    t = tickets_for(srv.base, "E2E-RESTART")
    assert len(t) == 1 and t[0]["deliveryStatus"] == "queued"            # no false "sent to client"
    assert "E2E-RESTART" not in client_ids(srv.base, CLIENT)
    srv.restart(CAMVIEW_FAULTS="")                                      # process restart, same database
    admin = api_session(srv.base, ADMIN)
    for _ in range(60):                                                 # the worker resumes the durable job
        t = tickets_for(srv.base, "E2E-RESTART")
        if t and t[0]["deliveryStatus"] == "delivered":
            break
        page.wait_for_timeout(500)
    assert t[0]["deliveryStatus"] == "delivered", t
    assert list(client_ids(srv.base, CLIENT)).count("E2E-RESTART") == 1
    m = admin.get(f"{srv.base}/api/admin/metrics").json()
    assert m["jobs"]["counts"]["DONE"] >= 1 and m["jobs"]["counts"]["RUNNING"] == 0
    assert requests.get(f"{srv.base}/readyz").json()["status"] == "ready"
