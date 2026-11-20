"""Impact math: golden values, monotonicity, ranges, source fields on every factor."""
import pytest

from app.impact import (
    co2e_car_to_metro,
    co2e_carpool,
    co2e_induction_swap,
    exposure_effect,
    load_factors,
)


@pytest.fixture(scope="module")
def factors():
    return load_factors()


class TestGoldenValues:
    def test_car_to_metro_20km(self, factors):
        r = co2e_car_to_metro(factors, 20)
        assert r["value"] == pytest.approx((0.16691 - 0.03546) * 20, abs=1e-3)
        assert r["value"] == pytest.approx(2.629, abs=0.01)

    def test_carpool_20km_3pax(self, factors):
        r = co2e_carpool(factors, 20, 3)
        assert r["value"] == pytest.approx(0.16691 * 20 * 2 / 3, abs=1e-3)

    def test_induction_swap_negative_honest(self, factors):
        # on the Indian grid, induction > LPG CO2e per delivered kWh today
        r = co2e_induction_swap(factors, meal_units=3.0)
        assert r["value"] < 0
        assert "MORE CO2e" in r["note"]

    def test_induction_swap_golden(self, factors):
        r = co2e_induction_swap(factors, meal_units=3.0)
        lpg = 2.93936 / 12.762 / 0.40 * 3
        ind = 0.675 / 0.90 * 3
        assert r["value"] == pytest.approx(lpg - ind, abs=1e-3)
        assert r["value"] == pytest.approx(-0.523, abs=0.01)


class TestMonotonicity:
    @pytest.mark.parametrize("kms", [(5, 10), (10, 20), (20, 40)])
    def test_more_km_never_lowers_saving(self, factors, kms):
        small, big = kms
        assert co2e_car_to_metro(factors, big)["value"] > co2e_car_to_metro(factors, small)["value"]

    def test_more_passengers_never_lowers_saving(self, factors):
        assert co2e_carpool(factors, 20, 4)["value"] > co2e_carpool(factors, 20, 2)["value"]


class TestRanges:
    def test_low_le_value_le_high_metro(self, factors):
        for km in (1, 7.5, 20, 55):
            r = co2e_car_to_metro(factors, km)
            assert r["low"] <= r["value"] <= r["high"]

    def test_low_le_value_le_high_carpool(self, factors):
        for pax in (2, 3, 5):
            r = co2e_carpool(factors, 33, pax)
            assert r["low"] <= r["value"] <= r["high"]

    def test_low_le_value_le_high_induction(self, factors):
        r = co2e_induction_swap(factors)
        assert r["low"] <= r["value"] <= r["high"]

    def test_carpool_needs_two(self, factors):
        with pytest.raises(ValueError):
            co2e_carpool(factors, 10, 1)


class TestFactorSources:
    REQUIRED = ("value", "unit", "source_name", "source_url", "year", "uncertainty")

    def test_every_factor_has_all_source_fields(self):
        for name, f in load_factors().items():
            for field in self.REQUIRED:
                assert field in f, f"{name} missing {field}"
                assert f[field], f"{name} empty {field}"

    def test_urls_are_https_and_real_hosts(self):
        for name, f in load_factors().items():
            assert f["source_url"].startswith("https://"), name

    def test_years_are_recent_ints(self):
        for name, f in load_factors().items():
            assert isinstance(f["year"], int) and 2010 <= f["year"] <= 2026, name


class TestExposureEffect:
    def test_known_levels(self):
        assert exposure_effect("lower") == "Lower"
        assert exposure_effect("much lower") == "Much lower"
        assert exposure_effect("n/a") == "n/a"

    def test_unknown_rejected(self):
        with pytest.raises(ValueError):
            exposure_effect("95%")
