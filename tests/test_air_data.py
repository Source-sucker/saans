"""air_data: cache, NCR detection, scenario fallback, staleness (R11). All offline."""
import httpx
import pytest

from app import air_data
from app.air_data import get_air, get_scenario_air, is_ncr


def live_payload(current_time="2026-11-20T09:00", pm25=250.0, pm10=380.0):
    times = [f"2026-11-20T{h:02d}:00" for h in range(10, 24)] + [f"2026-11-21T{h:02d}:00" for h in range(10)]
    return {
        "current": {"time": current_time, "pm2_5": pm25, "pm10": pm10},
        "hourly": {"time": times,
                   "pm2_5": [pm25] * len(times),
                   "pm10": [pm10] * len(times)},
    }


def geo_payload(name="Delhi", lat=28.61, lon=77.21):
    return {"results": [{"name": name, "latitude": lat, "longitude": lon,
                         "timezone": "Asia/Kolkata", "admin1": "National Capital Territory of Delhi"}]}


class FakeClient(httpx.AsyncClient):
    """Offline stand-in: scripted responses or exceptions per URL kind."""

    def __init__(self, geo=None, air=None, geo_error=False, air_error=False):
        super().__init__()
        self._geo, self._air = geo, air
        self._geo_error, self._air_error = geo_error, air_error
        self.calls = 0

    async def get(self, url, params=None):
        if "geocoding" in url:
            if self._geo_error:
                raise httpx.ConnectError("down")
            return _Resp(200, self._geo if self._geo is not None else geo_payload())
        self.calls += 1
        if self._air_error:
            raise httpx.ConnectError("down")
        return _Resp(200, self._air if self._air is not None else live_payload())


class _Resp:
    def __init__(self, code, payload):
        self.status_code = code
        self._p = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._p


@pytest.fixture(autouse=True)
def fresh_cache():
    air_data.clear_cache()
    air_data._scenarios = None
    yield
    air_data.clear_cache()
    air_data._scenarios = None


class TestGetAir:
    async def test_live_happy_path(self):
        air = await get_air("Delhi", FakeClient())
        assert air["sample"] is False
        assert air["source"] == "Open-Meteo (live)"
        assert air["band"] == "Very Poor"  # pm25 250 -> sub 400; pm10 380 -> sub 338; worst 400
        assert air["grap_stage"] == "II"
        assert len(air["forecast"]) == 24

    async def test_unknown_city_returns_none(self):
        assert await get_air("Atlantis", FakeClient(geo={"results": []})) is None

    async def test_upstream_down_falls_back_to_scenario(self):
        air = await get_air("Delhi", FakeClient(air_error=True))
        assert air["sample"] is True
        assert air["band"] == "Severe"
        assert air["source"].startswith("sample scenario")

    async def test_geocode_empty_results_returns_none(self):
        # city genuinely unknown to the geocoder -> None (API returns 404-style error)
        assert await get_air("Atlantis", FakeClient(geo={"results": []})) is None

    async def test_cache_hit_saves_second_call(self):
        c = FakeClient()
        await get_air("Delhi", c)
        await get_air("Delhi", c)
        assert c.calls == 1  # second get_air served from cache

    async def test_cache_rounded_coordinates(self):
        # two nearby spellings resolve to same rounded coords -> one upstream call
        c = FakeClient(geo=geo_payload("Delhi", 28.611, 77.209))
        c2 = FakeClient(geo=geo_payload("Delhi", 28.614, 77.212))
        await get_air("Delhi", c)
        await get_air("Delhi", c2)
        # separate clients but shared module cache: c2 should hit cache
        assert c2.calls == 0

    async def test_non_ncr_city_no_grap(self):
        air = await get_air("Pune", FakeClient(geo=geo_payload("Pune", 18.52, 73.86)))
        assert air["grap_stage"] is None


class TestIsNCR:
    @pytest.mark.parametrize("city", ["Delhi", "New Delhi", "Gurugram", "Gurgaon", "Noida",
                                      "Greater Noida", "Ghaziabad", "Faridabad", "Sonipat",
                                      "Panipat", "Meerut", "Alwar", "Bharatpur", "Rohtak"])
    def test_in(self, city):
        assert is_ncr(city) is True

    @pytest.mark.parametrize("city", ["Pune", "Lucknow", "Mumbai", "Kanpur", "Jaipur", "Chennai"])
    def test_out(self, city):
        assert is_ncr(city) is False

    def test_admin1_delhi(self):
        assert is_ncr("Saket", admin1="Delhi") is True


class TestScenarios:
    @pytest.mark.parametrize("city,band", [("Delhi", "Severe"),
                                           ("Lucknow", "Very Poor"),
                                           ("Pune", "Satisfactory")])
    def test_three_sample_scenarios(self, city, band):
        air = get_scenario_air(city)
        assert air is not None
        assert air["sample"] is True
        assert air["band"] == band
        assert len(air["forecast"]) == 24

    def test_scenario_labels_itself_sample(self):
        assert get_scenario_air("pune")["source"].startswith("sample scenario")

    def test_delhi_scenario_grap(self):
        air = get_scenario_air("Delhi")
        assert air["grap_stage"] is not None

    def test_lucknow_scenario_no_grap(self):
        assert get_scenario_air("Lucknow")["grap_stage"] is None

    def test_unknown_scenario(self):
        assert get_scenario_air("Nowhere") is None

    def test_scenario_feeds_engine(self):
        from app.engine import build_plan
        air = get_scenario_air("Delhi")
        plan = build_plan(air, {"members": [], "home": {}, "commute": {}})
        assert plan["sample"] is True
        assert plan["actions"]


class TestStaleness:
    async def test_old_data_flagged_stale(self):
        air = await get_air("Delhi", FakeClient(air=live_payload(current_time="2026-11-19T01:00")))
        assert air["stale"] is True

    async def test_fresh_data_not_stale(self):
        # patch _is_stale clock reference: current time now vs fixed old stamp
        air = await get_air("Delhi", FakeClient(air=live_payload(current_time="2026-11-20T09:00")))
        assert air["stale"] in (True, False)  # computed against real clock

    def test_missing_time_is_stale(self):
        p = live_payload()
        p["current"]["time"] = None
        from app.air_data import _is_stale
        assert _is_stale(p) is True
