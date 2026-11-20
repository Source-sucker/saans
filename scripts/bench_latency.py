"""Measure engine and API latency from real runs (numbers go into README)."""
import asyncio
import statistics
import time

import httpx

from app.air_data import get_scenario_air
from app.engine import build_plan

PROFILE = {"members": [{"label": "Ria (6)", "age_years": 6},
                       {"label": "Dada (70)", "age_years": 70, "sensitive": True}],
           "home": {"purifier": False, "cooking_fuel": "gas"},
           "commute": {"mode": "car", "km": 20}}


def bench_engine(n=200):
    air = get_scenario_air("Delhi")
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        build_plan(air, PROFILE)
        times.append((time.perf_counter() - t0) * 1000)
    return times


async def bench_api(base="http://127.0.0.1:8017"):
    async with httpx.AsyncClient(timeout=10.0) as c:
        # warmup
        await c.post(f"{base}/api/plan", json={"city": "Delhi", "profile": PROFILE})
        cached, fresh = [], []
        for i in range(15):
            t0 = time.perf_counter()
            r = await c.post(f"{base}/api/plan", json={"city": "Delhi", "profile": PROFILE})
            assert r.status_code == 200
            cached.append((time.perf_counter() - t0) * 1000)
        # distinct cities to defeat cache? same city uses cache; use manual endpoint for uncached path
        for i in range(4):
            t0 = time.perf_counter()
            r = await c.post(f"{base}/api/plan", json={"city": ["Pune", "Lucknow"][i % 2], "profile": PROFILE})
            assert r.status_code == 200
            fresh.append((time.perf_counter() - t0) * 1000)
    return cached, fresh


def stats(name, times):
    print(f"{name}: n={len(times)} median={statistics.median(times):.1f}ms "
          f"p95={sorted(times)[int(len(times) * 0.95)]:.1f}ms max={max(times):.1f}ms")


times = bench_engine()
stats("engine build_plan (pure)", times)
cached, fresh = asyncio.run(bench_api())
stats("API /api/plan cached", cached)
stats("API /api/plan first-hit city", fresh)
