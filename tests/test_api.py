from .conftest import add_factor


def rec(client, scope, key, qty, day, **extra):
    return client.post(f"/api/records/scope{scope}", json={"activity_key": key, "quantity": qty, "activity_date": day, **extra})


def test_factor_in_force_on_the_activity_date_is_used(factors):
    old = rec(factors, 1, "diesel", 100, "2023-06-15").json()
    new = rec(factors, 1, "diesel", 100, "2024-06-15").json()
    assert old["factor_value"] == 2.0 and old["calculated_kgco2e"] == 200.0
    assert new["factor_value"] == 3.0 and new["calculated_kgco2e"] == 300.0
    assert old["factor_version"] == 1 and new["factor_version"] == 2


def test_validity_boundaries_are_inclusive(factors):
    assert rec(factors, 1, "diesel", 1, "2023-12-31").json()["factor_value"] == 2.0
    assert rec(factors, 1, "diesel", 1, "2024-01-01").json()["factor_value"] == 3.0


def test_no_factor_for_date_is_rejected(factors):
    r = rec(factors, 1, "diesel", 1, "2021-12-31")
    assert r.status_code == 422 and "valid on 2021-12-31" in r.json()["detail"]


def test_scope_mismatch_unknown_activity_and_unit(factors):
    assert rec(factors, 2, "diesel", 1, "2024-02-01").status_code == 422
    assert rec(factors, 1, "grid_electricity", 1, "2024-02-01").status_code == 422
    assert rec(factors, 1, "unobtainium", 1, "2024-02-01").status_code == 422
    assert rec(factors, 1, "diesel", 1, "2024-02-01", unit="kg").status_code == 422


def test_invalid_quantity_and_future_date(factors):
    assert rec(factors, 1, "diesel", 0, "2024-02-01").status_code == 422
    assert rec(factors, 1, "diesel", -5, "2024-02-01").status_code == 422
    assert rec(factors, 1, "diesel", 1, "2999-01-01").status_code == 422


def test_new_factor_version_closes_the_open_one(client):
    add_factor(client, valid_to=None)
    assert add_factor(client, co2e_per_unit=2.5, valid_from="2025-01-01", valid_to=None).status_code == 201
    versions = client.get("/api/factors?activity_key=diesel").json()
    assert [(v["version"], v["valid_to"]) for v in versions] == [(1, "2024-12-31"), (2, None)]


def test_overlapping_factor_is_rejected(client):
    add_factor(client)
    r = add_factor(client, valid_from="2023-06-01", valid_to="2024-06-01", co2e_per_unit=9)
    assert r.status_code == 409
    assert add_factor(client, scope=2).status_code == 409


def test_override_writes_audit_trail_and_changes_totals(factors):
    r = rec(factors, 1, "diesel", 100, "2024-03-10").json()
    out = factors.patch(f"/api/records/{r['id']}/override",
                        json={"override_kgco2e": 250, "reason": "metered value", "changed_by": "auditor"}).json()
    assert out["is_overridden"] and out["effective_kgco2e"] == 250 and out["calculated_kgco2e"] == 300
    trail = factors.get(f"/api/records/{r['id']}/audit").json()
    assert len(trail) == 1 and trail[0]["old_value"] == 300 and trail[0]["new_value"] == 250
    assert trail[0]["changed_by"] == "auditor" and trail[0]["reason"] == "metered value"
    assert factors.get("/api/analytics/yoy?year=2024").json()["current"]["scope1"] == 250
    factors.patch(f"/api/records/{r['id']}/override", json={"override_kgco2e": 260, "reason": "second check", "changed_by": "auditor"})
    assert [a["old_value"] for a in factors.get(f"/api/records/{r['id']}/audit").json()] == [300, 250]


def test_revert_override_is_audited(factors):
    r = rec(factors, 1, "diesel", 100, "2024-03-10").json()
    assert factors.post(f"/api/records/{r['id']}/override/revert", json={"reason": "nothing to undo", "changed_by": "me"}).status_code == 409
    factors.patch(f"/api/records/{r['id']}/override", json={"override_kgco2e": 1, "reason": "temporary value", "changed_by": "me"})
    back = factors.post(f"/api/records/{r['id']}/override/revert", json={"reason": "invoice arrived", "changed_by": "me"}).json()
    assert not back["is_overridden"] and back["effective_kgco2e"] == 300
    assert [a["action"] for a in factors.get(f"/api/records/{r['id']}/audit").json()] == ["override", "override_reverted"]


def test_override_requires_reason_and_user(factors):
    r = rec(factors, 1, "diesel", 100, "2024-03-10").json()
    assert factors.patch(f"/api/records/{r['id']}/override", json={"override_kgco2e": 1}).status_code == 422
    assert factors.patch("/api/records/9999/override", json={"override_kgco2e": 1, "reason": "valid reason", "changed_by": "me"}).status_code == 404


def test_yoy_totals_by_scope_and_change(factors):
    rec(factors, 1, "diesel", 100, "2023-05-01")
    rec(factors, 2, "grid_electricity", 1000, "2023-05-01")
    rec(factors, 1, "diesel", 100, "2024-05-01")
    rec(factors, 2, "grid_electricity", 500, "2024-05-01")
    d = factors.get("/api/analytics/yoy?year=2024").json()
    assert d["previous"] == {"scope1": 200.0, "scope2": 800.0, "total": 1000.0}
    assert d["current"] == {"scope1": 300.0, "scope2": 400.0, "total": 700.0}
    assert d["change_pct"]["scope1"] == 50.0 and d["change_pct"]["total"] == -30.0


def test_yoy_compares_like_for_like_months_for_a_partial_year(factors):
    rec(factors, 1, "diesel", 100, "2023-02-01")
    rec(factors, 1, "diesel", 100, "2023-11-01")
    rec(factors, 1, "diesel", 100, "2024-02-01")
    d = factors.get("/api/analytics/yoy?year=2024").json()
    assert d["like_for_like"] and d["previous"]["scope1"] == 200.0
    full = factors.get("/api/analytics/yoy?year=2024&like_for_like=false").json()
    assert full["previous"]["scope1"] == 400.0


def test_hotspots_rank_and_shares_sum_to_100(factors):
    rec(factors, 1, "diesel", 100, "2024-05-01")
    rec(factors, 2, "grid_electricity", 1000, "2024-05-01")
    d = factors.get("/api/analytics/hotspots?start=2024-01-01&end=2024-12-31").json()
    assert d["largest"] == "Grid electricity" and d["sources"][0]["kgco2e"] == 800.0
    assert round(sum(s["share_pct"] for s in d["sources"]), 1) == 100.0


def test_intensity_for_sum_and_average_metrics(factors):
    rec(factors, 1, "diesel", 100, "2024-02-10")
    rec(factors, 1, "diesel", 100, "2024-03-10")
    for day, v in (("2024-02-29", 40), ("2024-03-31", 60)):
        factors.post("/api/metrics", json={"date": day, "metric_name": "Tons of Steel Produced", "value": v, "unit": "tonne"})
        factors.post("/api/metrics", json={"date": day, "metric_name": "Employees", "value": v, "unit": "employee", "aggregation": "avg"})
    q = "start=2024-01-01&end=2024-12-31"
    steel = factors.get(f"/api/analytics/intensity?{q}").json()
    assert steel["total_kgco2e"] == 600 and steel["metric_value"] == 100 and steel["intensity"] == 6.0
    staff = factors.get(f"/api/analytics/intensity?{q}&metric_name=Employees").json()
    assert staff["aggregation"] == "avg" and staff["intensity"] == 600 / 50
    assert factors.get(f"/api/analytics/intensity?{q}&metric_name=Nothing").status_code == 404


def test_metric_upsert_and_monthly_series(factors):
    body = {"date": "2024-01-31", "metric_name": "Employees", "value": 10, "unit": "employee"}
    assert factors.post("/api/metrics", json=body).status_code == 201
    assert factors.post("/api/metrics", json={**body, "value": 12}).status_code == 200
    assert len(factors.get("/api/metrics").json()) == 1 and factors.get("/api/metrics").json()[0]["value"] == 12
    rec(factors, 1, "diesel", 100, "2024-03-10")
    months = factors.get("/api/analytics/monthly?year=2024").json()["months"]
    assert len(months) == 12 and months[2]["total"] == 300 and months[0]["total"] == 0
