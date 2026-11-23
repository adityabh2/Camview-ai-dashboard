"""HMS board: alerts per classification (CamviewAI feature list), and the alert list's type counts."""

import alarms


def test_hms_lists_every_feature_and_counts_alerts(supervisor):
    s, d = supervisor.get("/api/hms?tzOffset=330")
    assert s == 200
    codes = {t["type"] for t in d["types"]}
    assert set(alarms.FEATURES) <= codes                                 # every type of the list, even with 0 alerts
    assert d["totals"]["total"] == sum(t["total"] for t in d["types"])
    for t in d["types"]:
        assert t["live"] <= t["total"] and t["today"] <= t["total"] and t["pending"] <= t["total"]
        if t["type"] in alarms.FEATURES:
            assert t["short"] == alarms.FEATURES[t["type"]][0] and t["name"] == alarms.FEATURES[t["type"]][1]
    assert {"seen", "online", "offline", "syncFailed"} <= set(d["cameras"])


def test_hms_total_matches_the_alert_list(supervisor):
    s, d = supervisor.get("/api/hms?tzOffset=330")
    s, q = supervisor.get("/api/queue?status=all&range=all&kind=alert&size=1")
    assert d["totals"]["total"] == q["totalElements"]


def test_queue_type_counts_follow_the_feature_list(supervisor):
    s, q = supervisor.get("/api/queue?status=all&range=all&size=5")
    tc = q["facets"]["typeCounts"]
    assert tc and tc == sorted(tc, key=lambda t: -t["count"])
    first = tc[0]
    s, f = supervisor.get(f"/api/queue?status=all&range=all&size=500&type={first['type']}")
    assert f["totalElements"] == first["count"]
    assert all(str(a["alarmType"]) == str(first["type"]) for a in f["items"])
