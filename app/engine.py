"""Plan engine: filters the action catalog by band x household x home x commute.

The engine decides and computes. Rules live in actions.json as `applies`
conditions; this module is a generic filter. Pure and deterministic.
"""
from __future__ import annotations

import json
from pathlib import Path

from .aqi import BANDS, worse_band
from .impact import co2e_car_to_metro, co2e_induction_swap, load_factors

DATA = Path(__file__).parent / "data"

_BAND_MIN = {b: i for i, b in enumerate(BANDS)}


from functools import lru_cache


@lru_cache(maxsize=1)
def load_actions() -> tuple[dict, ...]:
    return tuple(json.loads((DATA / "actions.json").read_text())["actions"])


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
        "cooking_fuel": (home.get("cooking_fuel") in ("lpg", "png") and "gas") or home.get("cooking_fuel"),
        "commute_mode": commute.get("mode"),
        "commute_km": commute.get("km"),
        "block": best,
    }
    out = []
    fmt = ctx_fmt(ctx, best, air)
    for a in load_actions():  # cached tuple of dicts
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
    if best:
        block, pm = f"{best['start']}-{best['end']}", best["pm25"]
    else:  # no forecast (manual entry): plain words instead of placeholder junk
        block, pm = "the lowest-pollution hours of your local forecast", "n/a"
    return {"aqi": air["aqi"], "band": ctx["band"], "best_block": block, "best_block_pm25": pm}


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


def community(plan: dict, households: int = 50) -> dict | None:
    """RWA/school notice from the same engine, with the N-households total (R10).

    Only generated when the effective band is Poor or worse (R10); None otherwise.
    """
    if _BAND_MIN[plan["band"]] < _BAND_MIN["Poor"]:
        return None
    co2e_actions = [a for a in plan["actions"] if a.get("impact", {}).get("value")]
    per_home = sum(a["impact"]["value"] for a in co2e_actions if a["impact"]["value"] > 0)
    total = per_home * households
    city = plan["city"] or "our city"
    en = (
        f"Subject: Air quality action for {city} - AQI {plan['aqi']} ({plan['band']})\n\n"
        "Dear RWA / School committee,\n\n"
        f"Today's air quality index in {city} is {plan['aqi']} ({plan['band']})."
        + (f" GRAP Stage {plan['grap_stage']} restrictions are in force in NCR." if plan.get("grap_stage") else "")
        + "\nWe request everyone to:\n"
        "1. Stop all waste and leaf burning immediately.\n"
        "2. Cover construction material and wet dust-removal inside the colony.\n"
        "3. Carpool or use metro/bus for school and office runs.\n"
        "4. Run diesel generators only in emergencies.\n"
        "5. Move morning assembly and PE indoors until air improves.\n\n"
        f"If {households} households do this for one day, together we avoid about "
        f"{total:.1f} kg CO2e (from commute swaps alone, using DEFRA 2024 factors) "
        "and cut everyone's smoke exposure.\n\n"
        "This message was generated by Saans, with the math shown in the app."
    )
    hi = (
        f"विषय: {city} में वायु गुणवत्ता कार्रवाई - AQI {plan['aqi']} ({plan['band']})\n\n"
        "आदरणीय RWA / स्कूल समिति,\n\n"
        f"आज {city} में वायु गुणवत्ता सूचकांक {plan['aqi']} ({plan['band']}) है।"
        + (f" NCR में GRAP चरण {plan['grap_stage']} के प्रतिबंध लागू हैं।" if plan.get("grap_stage") else "")
        + "\nहम सभी से अनुरोध है:\n"
        "1. कचरा और पत्तियां जलाना तुरंत बंद करें।\n"
        "2. कॉलोनी के भीतर निर्माण सामग्री ढकें और धूल को गीला करके हटाएं।\n"
        "3. स्कूल और दफ़्तर की यात्रा के लिए कारपूल करें या मेट्रो/बस लें।\n"
        "4. डीज़ल जेनरेटर केवल आपातकाल में चलाएं।\n"
        "5. हवा सुधरने तक सुबह की असेंबली और PE अंदर करें।\n\n"
        f"अगर {households} परिवार एक दिन ऐसा करें, तो मिलकर लगभग {total:.1f} kg CO2e "
        "(केवल यात्रा परिवर्तन से, DEFRA 2024 कारक) बचाया जा सकता है और सबके स्मॉक-एक्सपोज़र में कमी आएगी।\n\n"
        "यह संदेश Saans ऐप ने बनाया है, जिसका पूरा गणित ऐप में दिखाया गया है।"
    )
    return {
        "households": households,
        "per_household_co2e_kg": round(per_home, 3),
        "total_co2e_kg": round(total, 1),
        "math": f"{per_home:.3f} kg CO2e x {households} households = {total:.1f} kg CO2e",
        "actions_counted": [a["id"] for a in co2e_actions if a["impact"]["value"] > 0],
        "en": en,
        "hi": hi,
    }
