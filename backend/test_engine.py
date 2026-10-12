from ai_engine.core import Problem, search
R = dict(icu_beds=2, emergency_beds=1, general_beds=1, ventilators=1, operating_rooms=1, doctors=2, nurses=3)
P = [dict(id="P1", severity="critical", waiting_time=10, unit="icu", ventilator=True, surgery=False),
     dict(id="P2", severity="critical", waiting_time=5, unit="icu", ventilator=False, surgery=True),
     dict(id="P3", severity="serious", waiting_time=20, unit="emergency", ventilator=False, surgery=False),
     dict(id="P4", severity="moderate", waiting_time=30, unit="general", ventilator=False, surgery=False)]
def test_astar_matches_ucs_cost():
    a, u = [search(Problem(P, R), x) for x in ("astar", "ucs")]
    assert a["found"] and a["cost"] == u["cost"] and a["stats"]["states_explored"] <= u["stats"]["states_explored"]
def test_infeasible():
    r = search(Problem(P, {**R, "ventilators": 0}), "astar")
    assert not r["found"] and "ventilators" in r["reason"]
def test_resource_limit_forces_release():
    two = [dict(P[0], id="A"), dict(P[0], id="B")]
    r = search(Problem(two, R), "astar")
    assert r["found"] and any(a[0] == "release" for _, a, _ in r["path"])
