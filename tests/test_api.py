"""API tests: validation, security headers, fallback, rate limit. Live upstream is
mocked by monkeypatching air_data.get_air so no network is needed."""
import pytest
from fastapi.testclient import TestClient

from app import air_data
from app.main import app

client = TestClient(app)

SAMPLE_AIR = {
    "city": "Delhi", "aqi": 430, "band": "Severe", "pm25": 260, "pm10": 390,
    "forecast": [{"time": f"2026-11-20T{h:02d}:00", "pm25": 200 + h, "pm10": 300 + h} for h in range(24)],
    "now": "2026-11-20T08:00", "stale": False, "sample": True,
    "observed_at": "2026-11-20T08:00", "grap_stage": "III", "source": "sample scenario delhi-severe",
}


@pytest.fixture(autouse=True)
def fake_air(monkeypatch):
    async def fake_get_air(city, client, now_iso=None):
        if "bad" in city.lower():
            return None
        return dict(SAMPLE_AIR)
    monkeypatch.setattr(air_data, "get_air", fake_get_air)
    from app.main import limiter
    limiter.hits.clear()  # fresh rate-limit window per test
    yield
    limiter.hits.clear()


def base_profile():
    return {"members": [{"label": "Ria (6)", "age_years": 6}],
            "home": {"purifier": False, "cooking_fuel": "gas"},
            "commute": {"mode": "car", "km": 20}}


class TestPlan:
    def test_happy_path(self):
        r = client.post("/api/plan", json={"city": "Delhi", "profile": base_profile()})
        assert r.status_code == 200
        body = r.json()
        assert body["aqi"] == 430
        assert body["community"]["households"] == 50
        assert any(a["id"] == "vent_close" for a in body["actions"])
        assert any("why" in a and "impact" in a for a in body["actions"])

    def test_empty_profile_defaults(self):
        r = client.post("/api/plan", json={"city": "Delhi"})
        assert r.status_code == 200

    def test_unknown_city_404(self):
        r = client.post("/api/plan", json={"city": "Badcity"})
        assert r.status_code == 404
        assert "not found" in r.json()["detail"].lower()

    def test_city_too_long_rejected(self):
        r = client.post("/api/plan", json={"city": "x" * 81})
        assert r.status_code == 422

    def test_city_charset_rejected(self):
        r = client.post("/api/plan", json={"city": "Delhi<script>"})
        assert r.status_code == 422

    def test_nine_members_rejected(self):
        profile = base_profile()
        profile["members"] = [{"label": f"m{i}"} for i in range(9)]
        r = client.post("/api/plan", json={"city": "Delhi", "profile": profile})
        assert r.status_code == 422

    def test_bad_commute_mode_rejected(self):
        profile = base_profile()
        profile["commute"]["mode"] = "helicopter"
        r = client.post("/api/plan", json={"city": "Delhi", "profile": profile})
        assert r.status_code == 422

    def test_negative_km_rejected(self):
        profile = base_profile()
        profile["commute"]["km"] = -5
        r = client.post("/api/plan", json={"city": "Delhi", "profile": profile})
        assert r.status_code == 422

    def test_manual_pm_entry(self):
        r = client.post("/api/plan/manual", json={"city": "Delhi", "pm25": 260, "pm10": 390,
                                                  "profile": base_profile()})
        assert r.status_code == 200
        assert r.json()["band"] == "Severe"

    def test_manual_needs_a_value(self):
        r = client.post("/api/plan/manual", json={"city": "Delhi"})
        assert r.status_code == 422


class TestAsk:
    def test_ask_without_key_uses_template(self):
        import os
        had = os.environ.pop("GEMINI_API_KEY", None)
        r = client.post("/api/ask", json={"city": "Delhi", "question": "when can I open windows?",
                                          "profile": base_profile()})
        assert r.status_code == 200
        body = r.json()
        assert body["ai_used"] is False
        assert body["source"].startswith("template")
        assert len(body["answer"]) > 20
        if had:
            os.environ["GEMINI_API_KEY"] = had

    def test_ask_question_length_capped(self):
        r = client.post("/api/ask", json={"city": "Delhi", "question": "x" * 301})
        assert r.status_code == 422


class TestSecurityHeaders:
    def test_headers_on_api(self):
        r = client.post("/api/plan", json={"city": "Delhi"})
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["Referrer-Policy"] == "no-referrer"
        assert "default-src 'self'" in r.headers["Content-Security-Policy"]
        assert r.headers["X-Frame-Options"] == "DENY"

    def test_headers_on_static(self):
        r = client.get("/api/health")
        assert r.headers["X-Content-Type-Options"] == "nosniff"


class TestRateLimit:
    def test_rate_limit_triggers(self):
        # make 20 requests then expect 429
        codes = []
        for _ in range(25):
            r = client.post("/api/plan", json={"city": "Delhi"})
            codes.append(r.status_code)
            if r.status_code == 429:
                break
        assert 429 in codes

    def test_health_and_strings_not_rate_limited(self):
        # only compute routes (/api/plan, /api/ask) are limited
        for _ in range(25):
            client.get("/api/health")
            client.get("/api/strings")
        r = client.get("/api/health")
        assert r.status_code == 200
        assert client.get("/api/strings").status_code == 200


class TestHealth:
    def test_health(self):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert "ai" in r.json()
