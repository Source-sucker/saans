"""Live air data: geocode + fetch + TTL cache + offline scenario fallback (R11).

One upstream call returns current + hourly PM2.5/PM10 (Open-Meteo, no key).
The cache is keyed by rounded coordinates. Everything here is I/O; all
decisions live in aqi.py / engine.py.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from .aqi import aqi as aqi_of
from .aqi import band as band_of
from .aqi import grap_stage

DATA = Path(__file__).parent / "data"

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
TTL_SECONDS = 15 * 60
STALE_AFTER = 3 * 60 * 60
_TIMEOUT = httpx.Timeout(8.0)
_RETRIES = 2

_cache: dict[tuple[float, float], tuple[float, dict]] = {}
_geo_cache: dict[str, dict] = {}
_scenarios: dict[str, dict] | None = None


def _load_scenarios() -> dict[str, dict]:
    global _scenarios
    if _scenarios is None:
        raw = json.loads((DATA / "scenarios.json").read_text())["scenarios"]
        _scenarios = {s["city"].lower(): s for s in raw} | {s["id"]: s for s in raw}
    return _scenarios


def is_ncr(city: str, admin1: str | None = None) -> bool:
    """NCR membership by city name / district, from the district list in aqi_bands.json."""
    doc = json.loads((DATA / "aqi_bands.json").read_text())["grap"]
    districts = {d.lower() for states in doc["ncr_districts"].values() if isinstance(states, list)
                 for d in states}
    city_l = city.strip().lower()
    if city_l == "delhi" or city_l.startswith("new delhi"):
        return True
    if city_l in districts:
        return True
    if admin1 and admin1.strip().lower() == "delhi":
        return True
    # common city -> district aliases inside NCR
    aliases = {"gurugram": "gurgaon", "noida": "gautam buddh nagar",
               "greater noida": "gautam buddh nagar", "ghaziabad": "ghaziabad",
               "faridabad": "faridabad", "sonipat": "sonipat", "panipat": "panipat",
               "meerut": "meerut", "rohtak": "rohtak", "alwar": "alwar",
               "bharatpur": "bharatpur", "baghpat": "baghpat", "hapur": "hapur",
               "rewari": "rewari", "jhajjar": "jhajjar", "palwal": "palwal",
               "bulandshahr": "bulandshahr", "muzaffarnagar": "muzaffarnagar",
               "shamli": "shamli", "jind": "jind", "karnal": "karnal", "nuh": "nuh"}
    return aliases.get(city_l, city_l) in districts


async def geocode(city: str, client: httpx.AsyncClient) -> dict | None:
    """City -> {name, latitude, longitude, timezone, admin1} or None."""
    r = await client.get(GEO_URL, params={"name": city, "count": 1, "language": "en", "format": "json"})
    r.raise_for_status()
    results = r.json().get("results") or []
    if not results:
        return None
    g = results[0]
    return {"name": g["name"], "latitude": g["latitude"], "longitude": g["longitude"],
            "timezone": g.get("timezone", "Asia/Kolkata"), "admin1": g.get("admin1")}


async def fetch_air(latitude: float, longitude: float, client: httpx.AsyncClient) -> dict:
    """Current + next-48h hourly PM2.5/PM10 from Open-Meteo (single call)."""
    r = await client.get(AIR_URL, params={
        "latitude": latitude, "longitude": longitude,
        "current": "pm2_5,pm10", "hourly": "pm2_5,pm10",
        "forecast_days": 2, "timeformat": "iso8601", "timezone": "auto",
    })
    r.raise_for_status()
    return r.json()


def _round_key(latitude: float, longitude: float) -> tuple[float, float]:
    return (round(latitude, 2), round(longitude, 2))


def _hourly_forecast(payload: dict) -> list[dict]:
    hourly = payload.get("hourly", {})
    times = hourly.get("time", [])
    pm25s, pm10s = hourly.get("pm2_5", []), hourly.get("pm10", [])
    return [{"time": t, "pm25": p, "pm10": q} for t, p, q in zip(times, pm25s, pm10s)]


def _from_current(payload: dict) -> tuple[float | None, float | None]:
    cur = payload.get("current", {})
    return cur.get("pm2_5"), cur.get("pm10")


def _scenario_air(scenario: dict, now_iso: str | None = None) -> dict:
    """Build the air dict from a scenario, with today's hours as the forecast."""
    import datetime as dt

    now = dt.datetime.now(dt.UTC) + dt.timedelta(hours=5, minutes=30)
    hourly = []
    for i, pm25 in enumerate(scenario["hourly_pm25"]):
        t = now.replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(hours=i)
        pm10 = round(pm25 * scenario["current"]["pm10"] / scenario["current"]["pm25"])
        hourly.append({"time": t.strftime("%Y-%m-%dT%H:%M"), "pm25": pm25, "pm10": pm10})
    cur = scenario["current"]
    a = aqi_of(cur["pm25"], cur["pm10"])
    return {
        "city": scenario["city"],
        "aqi": a,
        "band": band_of(a),
        "pm25": cur["pm25"],
        "pm10": cur["pm10"],
        "forecast": hourly,
        "now": now.strftime("%Y-%m-%dT%H:%M"),
        "stale": False,
        "sample": True,
        "observed_at": now.strftime("%Y-%m-%dT%H:%M"),
        "grap_stage": grap_stage(a, scenario["in_ncr"]),
        "source": f"sample scenario {scenario['id']}",
    }


def _live_air(payload: dict, city: str, in_ncr: bool, now_iso: str) -> dict:
    pm25, pm10 = _from_current(payload)
    a = aqi_of(pm25, pm10)
    return {
        "city": city,
        "aqi": a,
        "band": band_of(a),
        "pm25": pm25,
        "pm10": pm10,
        "forecast": _hourly_forecast(payload),
        "now": now_iso,
        "stale": _is_stale(payload),
        "sample": False,
        "observed_at": payload.get("current", {}).get("time"),
        "grap_stage": grap_stage(a, in_ncr),
        "source": "Open-Meteo (live)",
    }


def _is_stale(payload: dict) -> bool:
    import datetime as dt

    t = payload.get("current", {}).get("time")
    if not t:
        return True
    try:
        observed = dt.datetime.fromisoformat(t)
    except ValueError:
        return True
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=dt.UTC)
    return (dt.datetime.now(dt.UTC) - observed).total_seconds() > STALE_AFTER


async def get_air(city: str, client: httpx.AsyncClient, now_iso: str | None = None) -> dict:
    """Resolve a city to air data: cache -> live -> scenario fallback.

    Never raises for a known sample city; returns None only if the city is
    unknown AND geocoding found nothing.
    """
    scenarios = _load_scenarios()
    key_name = city.strip().lower()
    scenario = scenarios.get(key_name)
    geo = _geo_cache.get(key_name)
    if geo is None:
        try:
            geo = await geocode(city, client)
        except httpx.HTTPError:
            geo = None
        if geo is not None:
            _geo_cache[key_name] = geo
    if geo:
        key = _round_key(geo["latitude"], geo["longitude"])
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < TTL_SECONDS:
            air = cached[1]
        else:
            try:
                payload = await fetch_air(*key, client)
                air = _live_air(payload, geo["name"], is_ncr(geo["name"], geo.get("admin1")),
                                now_iso or payload.get("current", {}).get("time", ""))
                _cache[key] = (time.monotonic(), air)
            except httpx.HTTPError:
                if scenario:
                    air = _scenario_air(scenario, now_iso)
                else:
                    raise
    elif scenario:
        air = _scenario_air(scenario, now_iso)
    else:
        return None
    return air


def clear_cache() -> None:
    _cache.clear()
    _geo_cache.clear()


def get_scenario_air(city_or_id: str) -> dict | None:
    """Directly load a sample scenario (manual/offline path, R11)."""
    scenario = _load_scenarios().get(city_or_id.strip().lower())
    return _scenario_air(scenario) if scenario else None
