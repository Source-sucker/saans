"""CPCB AQI computation: sub-indices, band, NCR GRAP stage. Pure functions."""
from __future__ import annotations

from itertools import pairwise

# PM2.5 / PM10 24h breakpoints: (concentration µg/m³, sub-index). CPCB 2014.
_PM25 = [(0, 0), (30, 50), (60, 100), (90, 200), (120, 300), (250, 400)]
_PM10 = [(0, 0), (50, 50), (100, 100), (250, 200), (350, 300), (430, 400)]
# CPCB band order, index = severity rank 0..5
BANDS = ["Good", "Satisfactory", "Moderate", "Poor", "Very Poor", "Severe"]
GRAP_MIN = 201  # Stage I starts at AQI 201
_MAX = 500


def sub_index(conc: float, breakpoints: list[tuple[float, float]]) -> int:
    """Piecewise-linear CPCB sub-index; 500 above the top breakpoint.

    Rounding to the nearest integer is our convention; CPCB publishes
    integer AQIs without documenting a rounding rule.
    """
    if conc < 0:
        raise ValueError("concentration must be >= 0")
    if conc > breakpoints[-1][0]:
        return _MAX
    for (c_lo, i_lo), (c_hi, i_hi) in pairwise(breakpoints):
        if c_lo <= conc <= c_hi:
            return round(i_lo + (i_hi - i_lo) * (conc - c_lo) / (c_hi - c_lo))
    return round(breakpoints[-1][1])  # non-contiguous breakpoints only


def pm25_sub(c: float) -> int:
    return sub_index(c, _PM25)


def pm10_sub(c: float) -> int:
    return sub_index(c, _PM10)


def aqi(pm25: float | None, pm10: float | None) -> int | None:
    """Overall AQI = worst (max) sub-index; None if no pollutant given."""
    subs = [s for s in (pm25_sub(pm25) if pm25 is not None else None,
                        pm10_sub(pm10) if pm10 is not None else None) if s is not None]
    return max(subs) if subs else None


def band(aqi_value: int) -> str:
    """CPCB band name for an AQI value."""
    if not 0 <= aqi_value <= _MAX:
        raise ValueError(f"AQI must be 0..{_MAX}")
    for i, (lo, hi) in enumerate([(0, 50), (51, 100), (101, 200), (201, 300), (301, 400), (401, 500)]):
        if lo <= aqi_value <= hi:
            return BANDS[i]
    raise ValueError(f"AQI must be 0..{_MAX}")


def band_index(band_name: str) -> int:
    """Severity rank of a band name (0=Good ... 5=Severe)."""
    return BANDS.index(band_name)


def worse_band(band_name: str, steps: int = 1) -> str:
    """Band shifted `steps` towards Severe (stopping at Severe)."""
    return BANDS[min(BANDS.index(band_name) + steps, len(BANDS) - 1)]


def better_band(band_name: str, steps: int = 1) -> str:
    return BANDS[max(BANDS.index(band_name) - steps, 0)]


def grap_stage(aqi_value: int, in_ncr: bool) -> str | None:
    """GRAP stage for NCR cities; None outside NCR (CAQM revised schedule, 29.09.2026)."""
    if not in_ncr:
        return None
    if aqi_value >= 451:
        return "IV"
    if aqi_value >= 401:
        return "III"
    if aqi_value >= 301:
        return "II"
    if aqi_value >= GRAP_MIN:
        return "I"
    return None

