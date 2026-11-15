"""Provider failure tests: retries, timeouts, deduplication, cache expiry, circuit breaker,
unit validation, missing-data tracking and provenance. No network: httpx.MockTransport."""
import threading
from dataclasses import replace

import httpx
import pytest

from backend import nasa_client, net, providers, quality, sources, terrain, water
from tests.test_backend import client, fake_data  # noqa: F401  (client is a fixture)


@pytest.fixture(autouse=True)
def clean_net(monkeypatch):
    net.reset()
    monkeypatch.setattr(net, "_clients", {})
    waits = []
    monkeypatch.setattr(net, "_sleep", waits.append)
    yield waits
    net.reset()


def mock(name, handler):
    net._clients[name] = httpx.Client(transport=httpx.MockTransport(handler))


def sequence(*answers):
    """Handler that returns the answers in order; an exception instance is raised."""
    calls = []

    def handler(request):
        a = answers[min(len(calls), len(answers) - 1)]
        calls.append(request)
        if isinstance(a, Exception):
            raise a
        return a
    return handler, calls


# ---------- retries and timeouts ----------

def test_retries_a_server_error_then_succeeds(clean_net):
    handler, calls = sequence(httpx.Response(503), httpx.Response(200, json={"ok": 1}))
    mock("open_meteo", handler)
    r = net.get("open_meteo", "https://x.test/a")
    assert r.json() == {"ok": 1} and r.attempts == 2 and len(calls) == 2
    assert len(clean_net) == 1                                     # one backoff wait
    assert net.status()["open_meteo"]["stats"]["retries"] == 1


def test_client_errors_are_not_retried():
    handler, calls = sequence(httpx.Response(404))
    mock("open_meteo", handler)
    with pytest.raises(net.Unavailable) as exc:
        net.get("open_meteo", "https://x.test/a")
    assert exc.value.status == 404 and len(calls) == 1


def test_read_timeout_is_retried_only_where_the_policy_allows():
    handler, calls = sequence(httpx.ReadTimeout("slow"), httpx.Response(200, json={}))
    mock("open_meteo", handler)
    with pytest.raises(net.Unavailable, match="ReadTimeout"):
        net.get("open_meteo", "https://x.test/a")
    assert len(calls) == 1
    handler, calls = sequence(httpx.ReadTimeout("slow"), httpx.Response(200, json={}))
    mock("earthdata_data", handler)
    assert net.get("earthdata_data", "https://x.test/b").attempts == 2


def test_connect_errors_are_retried_up_to_the_limit():
    handler, calls = sequence(httpx.ConnectError("down"))
    mock("open_meteo", handler)
    with pytest.raises(net.Unavailable, match="ConnectError"):
        net.get("open_meteo", "https://x.test/a")
    assert len(calls) == 1 + net.POLICIES["open_meteo"].retries


def test_retry_after_is_honoured(clean_net):
    handler, _ = sequence(httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(200, json={}))
    mock("open_meteo", handler)
    net.get("open_meteo", "https://x.test/a")
    assert clean_net[0] >= 3


def test_time_budget_stops_retries(monkeypatch):
    monkeypatch.setitem(net.POLICIES, "open_meteo", replace(net.POLICIES["open_meteo"], budget_s=0.01, backoff_s=1))
    handler, calls = sequence(httpx.Response(503))
    mock("open_meteo", handler)
    with pytest.raises(net.Unavailable, match="time budget"):
        net.get("open_meteo", "https://x.test/a")
    assert len(calls) == 1


# ---------- deduplication and cache ----------

def test_identical_requests_in_flight_share_one_call():
    release, started = threading.Event(), threading.Event()
    calls = []

    def handler(request):
        calls.append(request)
        started.set()
        release.wait(5)
        return httpx.Response(200, json={"n": len(calls)})
    mock("open_meteo", handler)
    results = []
    threads = [threading.Thread(target=lambda: results.append(net.get("open_meteo", "https://x.test/a", {"q": 1})))
               for _ in range(4)]
    threads[0].start()
    started.wait(5)
    for t in threads[1:]:
        t.start()
    while net.status()["open_meteo"]["stats"]["dedup_hits"] < 3:
        pass
    release.set()
    for t in threads:
        t.join(5)
    assert len(calls) == 1 and len(results) == 4 and all(r.json() == {"n": 1} for r in results)


def test_cache_serves_until_it_expires(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(net.time, "monotonic", lambda: clock[0])
    handler, calls = sequence(httpx.Response(200, json={"v": 1}))
    mock("open_meteo", handler)
    net.get("open_meteo", "https://x.test/a", {"d": 1})
    assert net.get("open_meteo", "https://x.test/a", {"d": 1}).from_cache
    assert len(calls) == 1
    net.get("open_meteo", "https://x.test/a", {"d": 2})                # other parameters: not the same request
    assert len(calls) == 2
    clock[0] += net.POLICIES["open_meteo"].ttl_s + 1
    assert not net.get("open_meteo", "https://x.test/a", {"d": 1}).from_cache
    assert len(calls) == 3


# ---------- circuit breaker ----------

def test_breaker_opens_after_repeated_failures_and_closes_later(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(net.time, "monotonic", lambda: clock[0])
    handler, calls = sequence(httpx.Response(404))
    mock("nasa_gibs", handler)
    for _ in range(net.POLICIES["nasa_gibs"].breaker_after):
        with pytest.raises(net.Unavailable):
            net.get("nasa_gibs", "https://x.test/tile")
    n = len(calls)
    with pytest.raises(net.Unavailable, match="skipped"):
        net.get("nasa_gibs", "https://x.test/tile")
    assert len(calls) == n and net.status()["nasa_gibs"]["state"] == "down"
    clock[0] += net.POLICIES["nasa_gibs"].breaker_open_s + 1
    mock("nasa_gibs", sequence(httpx.Response(200, content=b"png"))[0])
    assert net.get("nasa_gibs", "https://x.test/tile").status == 200
    assert net.status()["nasa_gibs"]["state"] == "ok"


# ---------- NASA POWER: units, ranges, missing data ----------

def _power_body(units=None, t2m=None):
    params = {name: {"20240101": 1.0, "20240102": 2.0} for name in nasa_client.PARAMS}
    params["T2M"] = t2m or {"20240101": 5.0, "20240102": 6.0}
    params["RH2M"] = {"20240101": 50.0, "20240102": 140.0}           # 140 % is impossible
    return {"header": {"fill_value": -999.0, "sources": ["MERRA2"], "api": {"version": "v2.10.3"}},
            "geometry": {"coordinates": [69.2, 41.3, 450.0]},
            "parameters": {k: {"units": (units or {}).get(k, quality.POWER_LIMITS[k][0])} for k in nasa_client.PARAMS},
            "properties": {"parameter": params}}


def test_power_rejects_impossible_values_and_reports_them(monkeypatch):
    mock("nasa_power", lambda request: httpx.Response(200, json=_power_body()))
    from datetime import date
    data = nasa_client.fetch_daily(41.3, 69.2, today=date(2024, 1, 3))
    rh = data["params"]["RH2M"]
    assert rh[-2] == 50.0 and rh[-1] is None                           # rejected, not clipped to 100
    report = data["quality"]["parameters"]["RH2M"]
    assert report["rejected"] == 1 and data["quality"]["rejected_units"] == []
    assert data["fetched_at"] and data["units"]["ALLSKY_SFC_SW_DWN"] == "MJ/m^2/day"


def test_power_parameter_in_an_unexpected_unit_is_dropped():
    from datetime import date
    mock("nasa_power", lambda request: httpx.Response(200, json=_power_body(units={"WS2M": "km/h"})))
    data = nasa_client.fetch_daily(41.3, 69.2, today=date(2024, 1, 3))
    assert data["quality"]["rejected_units"] == ["WS2M"]
    assert all(v is None for v in data["params"]["WS2M"])             # not converted on a guess


def test_power_down_becomes_power_error():
    from datetime import date
    mock("nasa_power", lambda request: httpx.Response(503))
    with pytest.raises(nasa_client.PowerError, match="HTTP 503"):
        nasa_client.fetch_daily(41.3, 69.2, today=date(2024, 1, 3))


def test_missing_and_pending_days_are_counted_separately():
    params = {"T2M": [1.0, None, 3.0, 4.0, None, None], "PRECTOTCORR": [0.0] * 6}
    report = quality.check_power(params, "2024-01-01")
    t = report["parameters"]["T2M"]
    assert t["missing"] == 1 and t["pending_recent_days"] == 2 and t["last"] == "2024-01-04"
    assert report["complete_share"] == pytest.approx(0.75)


# ---------- SoilGrids, Open-Meteo, SRTM ----------

def test_implausible_soil_is_refused():
    body = {"properties": {"layers": [
        {"name": "clay", "unit_measure": {"d_factor": 10}, "depths": [{"values": {"mean": 800}}]},
        {"name": "sand", "unit_measure": {"d_factor": 10}, "depths": [{"values": {"mean": 700}}]},
    ]}}
    with pytest.raises(providers.ProviderDown, match="implausible"):
        providers._soil_parse(body)


def test_forecast_drops_wrong_units_and_impossible_values():
    body = {"daily": {"time": ["2026-11-15", "2026-11-16"], "precipitation_sum": [-5.0, 2.0],
                      "et0_fao_evapotranspiration": [0.03, 0.04], "temperature_2m_max": [10, 11],
                      "temperature_2m_min": [1, 2], "precipitation_probability_max": [10, 20]},
            "daily_units": {"precipitation_sum": "mm", "et0_fao_evapotranspiration": "inch",
                            "temperature_2m_max": "°C", "temperature_2m_min": "°C"}}
    out = providers._forecast_days({"name": "Open-Meteo"}, body)
    assert out["quality"]["rejected_units"] == ["et0_fao_evapotranspiration"]
    assert all(d["et0_mm"] is None for d in out["days"]) and out["et0_7d_mm"] is None
    assert out["days"][0]["rain_mm"] is None and out["quality"]["rejected_values"] == 1


def test_srtm_failure_and_implausible_height(monkeypatch):
    monkeypatch.setattr(terrain, "_cache", {})
    monkeypatch.setattr(terrain, "MIN_INTERVAL_S", 0)
    monkeypatch.setattr(terrain, "CACHE_FILE", terrain.CACHE_FILE.parent / "_test_terrain.json")
    mock("srtm", lambda request: httpx.Response(502))
    with pytest.raises(terrain.TerrainDown, match="HTTP 502"):
        terrain.sample(10.0, 10.0)
    net.reset()
    mock("srtm", lambda request: httpx.Response(200, json={"results": [{"elevation": 99999}] * 9}))
    with pytest.raises(terrain.TerrainDown, match="implausible"):
        terrain.sample(10.1, 10.1)


def test_open_meteo_down_is_weather_down(monkeypatch):
    monkeypatch.setattr(water, "_cache", {})
    mock("open_meteo", lambda request: httpx.Response(500))
    with pytest.raises(water.WeatherDown, match="HTTP 500"):
        water.fetch(1.0, 2.0, 1)
    mock("open_meteo_ensemble", lambda request: httpx.Response(500))
    assert water.fetch_ensemble(1.0, 2.0) == []                       # no ensemble: [] not invented members


# ---------- provenance and the catalogue ----------

def test_analysis_carries_provenance(client):
    body = client.get("/api/analyze?lat=41.3&lon=69.2").json()
    prov = {p["id"]: p for p in body["provenance"]}
    assert set(prov) == {s["id"] for s in sources.CATALOGUE}
    assert prov["nasa_power"]["status"] == "cache" and prov["nasa_power"]["completeness"] == 1.0
    assert prov["nasa_power"]["period"]["start"] == "1991-01-01"
    assert prov["soilgrids"]["status"] == "default" and "class" in prov["soilgrids"]["detail"]
    assert prov["open_meteo"]["status"] == "unavailable"
    assert prov["nasa_srtm"]["status"] == "unavailable"
    assert prov["faostat"]["status"] == "offline_dataset" and len(prov["faostat"]["dataset"]["sha256"]) == 12
    chosen = client.get("/api/analyze?lat=41.3&lon=69.2&soil=heavy").json()
    assert next(p for p in chosen["provenance"] if p["id"] == "soilgrids")["status"] == "chosen"


def test_fallback_point_is_marked_with_distance(client, monkeypatch):
    from backend import store
    monkeypatch.setattr(store, "_memory", {"41.3_69.2": fake_data()})
    body = client.get("/api/analyze?lat=41.6&lon=69.5").json()
    power = next(p for p in body["provenance"] if p["id"] == "nasa_power")
    assert power["status"] == "fallback" and power["fallback"]["distance_km"] > 0


def test_data_sources_endpoint(client):
    body = client.get("/api/data-sources").json()
    by = {s["id"]: s for s in body["sources"]}
    assert set(by) == {"nasa_power", "nasa_gibs", "nasa_earthdata", "nasa_srtm", "soilgrids", "open_meteo",
                       "faostat", "mapspam"}
    assert by["mapspam"]["state"] == "offline_dataset" and by["mapspam"]["dataset"]["bytes"] > 0
    assert by["open_meteo"]["channels"]["open_meteo"]["policy"]["retries"] == 2
    assert by["nasa_power"]["state"] in {"ok", "unknown", "degraded", "down"}
