"""CO2e computations from sourced factors + qualitative exposure effects. Pure."""
from __future__ import annotations

import json
from pathlib import Path

DATA = Path(__file__).parent / "data"

_SOURCE_FIELDS = ("source_name", "source_url", "year", "uncertainty")


def load_factors() -> dict:
    """Load emission-factor catalog; every factor must carry full source fields."""
    factors = json.loads((DATA / "factors.json").read_text())
    for name, f in factors.items():
        missing = [k for k in _SOURCE_FIELDS if k not in f]
        if missing:
            raise ValueError(f"factor {name!r} missing source fields: {missing}")
    return factors


def co2e_car_to_metro(factors: dict, km: float) -> dict:
    """Switching a car km to metro/rail: car minus rail, in kg CO2e per trip."""
    car = factors["car_avg"]["value"]
    rail = factors["rail_national"]["value"]
    saving = (car - rail) * km
    return {
        "value": round(saving, 3),
        "low": round(saving * 0.8, 3),
        "high": round(saving * 1.2, 3),
        "formula": f"({car} - {rail}) kg CO2e per km x {km} km",
        "inputs": {"car_kg_per_km": car, "rail_kg_per_pkm": rail, "km": km},
        "factors": ["car_avg", "rail_national"],
        "source_name": f"{factors['car_avg']['source_name']}; {factors['rail_national']['source_name']}",
        "source_url": factors["rail_national"]["source_url"],
        "year": factors["rail_national"]["year"],
        "note": "Proxy UK factors applied to an Indian trip; see factor uncertainties.",
    }


def co2e_carpool(factors: dict, km: float, passengers: int) -> dict:
    """Adding co-riders to a car trip: avoided car km, in kg CO2e per trip."""
    if passengers < 2:
        raise ValueError("carpool needs >= 2 people")
    car = factors["car_avg"]["value"]
    avoided_km = km * (passengers - 1) / passengers
    saving = car * avoided_km
    return {
        "value": round(saving, 3),
        "low": round(saving * 0.8, 3),
        "high": round(saving * 1.2, 3),
        "formula": f"{car} kg CO2e/km x {km} km x {passengers - 1}/{passengers}",
        "inputs": {"car_kg_per_km": car, "km": km, "passengers": passengers},
        "factors": ["car_avg"],
        "source_name": factors["car_avg"]["source_name"],
        "source_url": factors["car_avg"]["source_url"],
        "year": factors["car_avg"]["year"],
        "note": "Avoided car km if co-riders would otherwise drive alone; proxy factor.",
    }


def co2e_induction_swap(factors: dict, meal_units: float = 3.0) -> dict:
    """LPG vs induction for the same delivered cooking energy.

    Honest math: induction is MORE CO2e per meal on the Indian grid today
    (0.675 kg/kWh, CEA v22.0) because LPG delivers combustion energy at
    2.939 kg CO2e per kg / 12.762 kWh per kg even at 40% stove efficiency.
    The swap is recommended for indoor-air reasons, not CO2e.
    """
    grid = factors["grid_co2_india"]["value"]
    lpg_co2 = factors["lpg_kg"]["value"]
    lpg_kwh = factors["lpg_kwh_per_kg"]["value"]
    eff_i = factors["induction_efficiency"]["value"]
    eff_g = factors["gas_efficiency"]["value"]
    lpg_emis = lpg_co2 * (meal_units / eff_g) / lpg_kwh
    ind_emis = grid * (meal_units / eff_i)
    delta = lpg_emis - ind_emis
    lo, hi = (delta * 0.8, delta * 1.2) if delta >= 0 else (delta * 1.2, delta * 0.8)
    return {
        "value": round(delta, 3),
        "low": round(lo, 3),
        "high": round(hi, 3),
        "formula": (
            f"LPG: {lpg_co2} kgCO2e/kg / ({lpg_kwh} kWh/kg x {eff_g} gas eff) = "
            f"{lpg_co2 / lpg_kwh / eff_g:.4f} kgCO2e per delivered kWh; "
            f"induction: {grid} kgCO2e/kWh / {eff_i} induction eff = {grid / eff_i:.4f}. "
            f"Delta x {meal_units:g} meal-units = {delta:.3f} kgCO2e (negative = induction higher)."
        ),
        "inputs": {"grid_kg_per_kwh": grid, "lpg_kg_co2e_per_kg": lpg_co2,
                   "lpg_kwh_per_kg": lpg_kwh, "induction_eff": eff_i, "gas_eff": eff_g,
                   "meal_units": meal_units},
        "factors": ["grid_co2_india", "lpg_kg", "lpg_kwh_per_kg", "gas_efficiency", "induction_efficiency"],
        "source_name": "CEA v22.0 (grid); DEFRA 2024 (LPG); ACEEE 2014 (efficiencies)",
        "source_url": factors["grid_co2_india"]["source_url"],
        "year": factors["grid_co2_india"]["year"],
        "note": "Honest result: on today's Indian grid, induction emits MORE CO2e per meal than LPG. The swap still removes indoor combustion pollutants (CO, NO2, PM2.5) from the kitchen.",
    }


_EXPOSURE_LABEL = {"lower": "Lower", "much lower": "Much lower", "n/a": "n/a"}


def exposure_effect(level: str) -> str:
    """Qualitative exposure label for an action; never a made-up percentage."""
    try:
        return _EXPOSURE_LABEL[level]
    except KeyError:
        raise ValueError(f"unknown exposure level {level!r}") from None
