"""Saans API: routes, security headers, in-memory rate limit, static UI mount."""
from __future__ import annotations

import json
import time
from functools import lru_cache
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import air_data
from .ai import ai_available, ask_ai
from .engine import build_plan, community
from .models import AskRequest, ManualAQIRequest, PlanRequest

_STATIC = Path(__file__).parent.parent / "static"

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}


class RateLimiter:
    """Tiny fixed-window limiter for /api/* keyed by client IP."""

    def __init__(self, max_requests: int = 20, window_seconds: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window = window_seconds
        self.hits: dict[str, list[float]] = {}

    def check(self, key: str) -> bool:
        now = time.monotonic()
        recent = [t for t in self.hits.get(key, []) if now - t < self.window]
        if len(recent) >= self.max_requests:
            self.hits[key] = recent
            return False
        recent.append(now)
        self.hits[key] = recent
        return True


limiter = RateLimiter()

app = FastAPI(title="Saans", docs_url=None, redoc_url=None, openapi_url="/api/openapi.json")
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


_RATE_LIMITED = ("/api/plan", "/api/ask")


@app.middleware("http")
async def security_and_rate_limit(request: Request, call_next):
    if request.url.path.startswith(_RATE_LIMITED):
        ip = request.client.host if request.client else "unknown"
        if not limiter.check(ip):
            return JSONResponse({"detail": "Too many requests. Try again in a minute."},
                                status_code=429, headers=SECURITY_HEADERS)
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers[k] = v
    return response


@app.get("/")
async def index():
    return FileResponse(_STATIC / "index.html")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(8.0))


async def _resolve_air(city: str) -> dict:
    try:
        async with _client() as client:
            air = await air_data.get_air(city, client)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503,
                            detail="Air data service unreachable and no sample available. Try again shortly.") from exc
    if air is None:
        raise HTTPException(status_code=404, detail=f"City not found: {city}. Try the sample cities: Delhi, Lucknow, Pune.")
    return air


@app.post("/api/plan")
async def plan(req: PlanRequest) -> dict:
    air = await _resolve_air(req.city)
    profile = req.profile.model_dump()
    plan_dict = build_plan(air, profile)
    plan_dict["community"] = community(plan_dict)
    return plan_dict


@app.post("/api/plan/manual")
async def plan_manual(req: ManualAQIRequest) -> dict:
    """R11: manual PM entry when the user has their own reading (e.g. CPCB bulletin)."""
    from .aqi import aqi as aqi_of
    from .aqi import band as band_of
    from .aqi import grap_stage

    if req.pm25 is None and req.pm10 is None:
        raise HTTPException(status_code=422, detail="Provide pm25 or pm10 (µg/m³).")
    a = aqi_of(req.pm25, req.pm10)
    air = {"city": req.city, "aqi": a, "band": band_of(a), "pm25": req.pm25, "pm10": req.pm10,
           "forecast": [], "now": None, "stale": False, "sample": False,
           "observed_at": None, "grap_stage": grap_stage(a, air_data.is_ncr(req.city)),
           "source": "manual entry (no forecast available)"}
    plan_dict = build_plan(air, req.profile.model_dump())
    plan_dict["community"] = community(plan_dict)
    return plan_dict


@app.post("/api/ask")
async def ask(req: AskRequest) -> dict:
    air = await _resolve_air(req.city)
    plan_dict = build_plan(air, req.profile.model_dump())
    plan_dict["community"] = community(plan_dict)
    result = await ask_ai(plan_dict, req.question)
    return result


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "ai": ai_available()}


@lru_cache(maxsize=1)
def _strings() -> dict:
    """UI strings EN/HI from one file; templates are deterministic, no model needed."""
    return json.loads((Path(__file__).parent / "data" / "strings.json").read_text())


@app.get("/api/strings")
async def strings() -> dict:
    return _strings()
