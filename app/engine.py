"""Plan engine: filters the action catalog by band x household x home x commute.

The engine decides and computes. Rules live in actions.json as `applies`
conditions; this module is a generic filter. Pure and deterministic.
"""
from __future__ import annotations

import json
from pathlib import Path

from .aqi import BANDS, worse_band
from .impact import co2e_car_to_metro, co2e_carpool, co2e_induction_swap, load_factors

DATA = Path(__file__).parent / "data"

_BAND_MIN = {b: i for i, b in enumerate(BANDS)}


def load_actions() -> list[dict]:
    return json.loads((DATA / "actions.json").read_text())["actions"]


def best_block(forecast: list[dict], window: int = 3, now: str | None = None) -> dict | None:
    """Lowest-mean-PM2.5 contiguous block of `window` hours starting today.

    forecast items: {"time": "2026-11-20T14:00", "pm25": 12.3, ...}
    Returns {"start": "14:00", "end": "17:00", "pm25": mean} or None.
    """
    if not forecast:
        return None
    start_idx = 0
    if now:
        for i, h in enumerate(forecast):
            if h["time"] >= now:
                start_idx = i
                break
        else:
            return None
    visible = forecast[start_idx:]
    if len(visible) < window:
        return None
    best_i, best_mean = 0, float("inf")
    for i in range(len(visible) - window + 1):
        vals = [h["pm25"] for h in visible[i:i + window] if h.get("pm25") is not None]
        if len(vals) < window:
            continue
        mean = sum(vals) / window
        if mean < best_mean:
            best_i, best_mean = i, mean
    if best_mean == float("inf"):
        return None
    block = visible[best_i:best_i + window]
    return {
        "start": block[0]["time"][11:16],
        "end": block[-1]["time"][11:16],
        "pm25": round(best_mean, 1),
    }


def effective_band(band_name: str, sensitive: bool) -> str:
    """Sensitive household members shift the effective band one step worse."""
    return worse_band(band_name) if sensitive else band_name


def _member_flags(household: dict) -> dict:
    members = household.get("members", [])
    return {
        "has_child": any(m.get("age_years") is not None and m["age_years"] <= 12 for m in members),
        "has_elder": any(m.get("age_years") is not None and m["age_years"] >= 65 for m in members),
        "sensitive": any(m.get("sensitive") is True for m in members),
        "has_under5": any(m.get("age_years") is not None and m["age_years"] <= 5 for m in members),
    }


def _applies(action: dict, ctx: dict) -> bool:
    """Evaluate the `applies` condition object against the plan context."""
    cond = action.get("applies", {})
    if cond.get("unvented_combustion") and not ctx.get("unvented_combustion"):
        return False
    if cond.get("has_purifier") is True and not ctx.get("has_purifier"):
        return False
    if cond.get("has_purifier") is False and ctx.get("has_purifier") is not False:
        # only show DIY when the household genuinely has no purifier
        return False
    if "cooking_fuel" in cond and ctx.get("cooking_fuel") != cond["cooking_fuel"]:
        return False
    if "commute_mode" in cond and ctx.get("commute_mode") != cond["commute_mode"]:
        return False
    if "commute_mode_any" in cond and ctx.get("commute_mode") not in cond["commute_mode_any"]:
        return False
    if "has_child" in cond and not ctx.get("has_child"):
        return False
    if "has_elder" in cond and not ctx.get("has_elder"):
        return False
    band_i = _BAND_MIN[ctx["band"]]
    if "band_min" in cond and band_i < _BAND_MIN[cond["band_min"]]:
        return False
    return "band_max" not in cond or band_i <= _BAND_MIN[cond["band_max"]]


def _co2e(kind: str, ctx: dict, factors: dict) -> dict | None:
    if kind == "car_to_metro":
        km = ctx.get("commute_km") or 0
        return co2e_car_to_metro(factors, km) if km > 0 else None
    if kind == "induction_swap":
        return co2e_induction_swap(factors)
    if kind == "carpool":
        return co2e_carpool(factors, ctx.get("commute_km") or 0, ctx.get("carpool_passengers") or 2)
    return None


def build_plan(air: dict, profile: dict) -> dict:
    """Build the full plan: filtered actions with why + impact + math.

    air: {"aqi": int, "band": str, "pm25": float, "pm10": float, "forecast": [...],
          "stale": bool, "sample": bool, "city": str, "grap_stage": str|None, "observed_at": str}
    profile: {"members": [...], "home": {...}, "commute": {...}}
    """
    factors = load_factors()
    flags = _member_flags(profile)
    home = profile.get("home", {})
    commute = profile.get("commute", {})
    sensitive = flags["sensitive"]
    band_now = air["band"]
    eff = effective_band(band_now, sensitive)
    best = best_block(air.get("forecast", []), now=air.get("now"))
    ctx = {
        **flags,
        "band": eff,
        "aqi": air["aqi"],
        "has_purifier": home.get("purifier", False),
        "unvented_combustion": home.get("unvented_combustion", False),
        "cooking_fuel": home.get("cooking_fuel"),
        "commute_mode": commute.get("mode"),
        "commute_km": commute.get("km"),
        "carpool_passengers": commute.get("carpool_passengers", 2),
        "best_block_start": best["start"] if best else "--:--",
        "block": best,
    }
    out = []
    fmt = ctx_fmt(ctx, best, air)
    for a in load_actions():
        if not _applies(a, ctx):
            continue
        why = {
            "trigger": f"effective band {eff}" + (" (sensitive member: shifted one band worse)" if sensitive and eff != band_now else ""),
            "rule_id": a["rule_id"],
            "inputs": {"aqi": air["aqi"], "band": eff, "best_block": best},
        }
        text_en = a["en"].format(**fmt)
        text_hi = a["hi"].format(**fmt)
        item = {
            "id": a["id"],
            "category": a["category"],
            "priority": a["priority"],
            "en": text_en,
            "hi": text_hi,
            "why": why,
            "exposure": a["exposure"],
        }
        if "co2e" in a:
            calc = _co2e(a["co2e"], ctx, factors)
            if calc:
                item["impact"] = calc
            else:
                item["impact"] = {"value": None, "note": "no CO2e claimed; benefit is exposure"}
        else:
            item["impact"] = {"value": None, "note": "no CO2e claimed; benefit is exposure"}
        if a.get("mask_note"):
            item["mask_note"] = a["mask_note"]
        out.append(item)
    out.sort(key=lambda x: -x["priority"])
    plan = {
        "city": air.get("city"),
        "aqi": air["aqi"],
        "band": band_now,
        "effective_band": eff,
        "sensitive_shift": sensitive and eff != band_now,
        "grap_stage": air.get("grap_stage"),
        "pm25": air.get("pm25"),
        "pm10": air.get("pm10"),
        "stale": air.get("stale", False),
        "sample": air.get("sample", False),
        "observed_at": air.get("observed_at"),
        "best_block": best,
        "actions": out,
        "members": _member_cards(profile, eff),
    }
    return plan


def ctx_fmt(ctx: dict, best: dict | None, air: dict) -> dict:
    """Substitution values for EN/HI templates (str() everything)."""
    return {
        "aqi": air["aqi"],
        "band": ctx["band"],
        "best_block_start": ctx["best_block_start"],
        "best_block_end": best["end"] if best else "--:--",
        "best_block_pm25": best["pm25"] if best else "n/a",
    }


def _member_cards(profile: dict, eff: str) -> list[dict]:
    """Per-member card: who, their constraints, the actions that apply to them."""
    cards = []
    for m in profile.get("members", []):
        age = m.get("age_years")
        acts = []
        if age is not None and age <= 12:
            if _BAND_MIN[eff] >= _BAND_MIN["Very Poor"]:
                acts.append("school_indoor")
            if age <= 5:
                acts.append("mask_under5_none")
        if age is not None and age >= 65 and _BAND_MIN[eff] >= _BAND_MIN["Moderate"]:
            acts.append("elder_walk")
        if m.get("sensitive") and _BAND_MIN[eff] >= _BAND_MIN["Poor"]:
            acts.append("keep_medicines_plan")
        cards.append({
            "label": m.get("label") or (f"age {age}" if age is not None else "member"),
            "age_years": age,
            "sensitive": bool(m.get("sensitive")),
            "actions": acts,
        })
    return cards
