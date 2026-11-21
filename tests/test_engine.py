"""One test per rule R1-R11, plus sensitive shift, determinism, member cards."""

from typing import ClassVar

import pytest

from app.engine import best_block, build_plan, load_actions


def fc(hours_pm25, day="2026-11-20", start_hour=0):
    return [{"time": f"{day}T{start_hour + i:02d}:00", "pm25": p} for i, p in enumerate(hours_pm25)]


def air(band, aqi=None, forecast=None, **kw):
    aqi_map = {"Good": 40, "Satisfactory": 80, "Moderate": 150, "Poor": 250,
               "Very Poor": 350, "Severe": 430}
    return {"aqi": aqi if aqi is not None else aqi_map[band], "band": band,
            "pm25": 100, "pm10": 150, "forecast": forecast or fc(range(24)),
            "now": "2026-11-20T08:00", "city": "Delhi", "grap_stage": None,
            "stale": False, "sample": False, "observed_at": "2026-11-20T08:00", **kw}


def profile(members=None, home=None, commute=None):
    return {"members": members or [], "home": home or {}, "commute": commute or {}}


def ids(plan):
    return [a["id"] for a in plan["actions"]]


def get(plan, action_id):
    return next(a for a in plan["actions"] if a["id"] == action_id)


class TestBestBlock:
    def test_picks_true_minimum(self):
        hours = [300, 280, 260, 240, 200, 150, 120, 110, 105, 100, 110, 130]
        b = best_block(fc(hours), now="2026-11-20T00:00")
        # windows 07:00 and 08:00 tie at 105.0; the earlier start wins deterministically
        assert (b["start"], b["end"]) == ("07:00", "09:00")
        assert b["pm25"] == pytest.approx(105.0)

    def test_respects_now(self):
        b = best_block(fc([100, 90, 80, 300, 310, 320]), now="2026-11-20T03:00")
        assert b["start"] == "03:00"  # cannot reach yesterday's low

    def test_short_forecast_returns_none(self):
        assert best_block(fc([100, 90]), now="2026-11-20T00:00") is None

    def test_empty_forecast(self):
        assert best_block([], now="2026-11-20T00:00") is None

    def test_none_pm25_skipped(self):
        data = fc([100, None, 90, 80, 70])
        b = best_block(data, now="2026-11-20T00:00")
        assert b is not None  # block 02:00-05:00 has all values


class TestR1Ventilation:
    def test_poor_shuts_windows_with_best_block(self):
        p = build_plan(air("Poor", forecast=fc([200, 190, 180, 120, 110, 100, 130, 140])),
                       profile())
        a = get(p, "vent_close")
        assert "15:00" not in a["en"]  # block is 03:00-06:00
        assert "shut" in a["en"]
        assert "vent_open" not in ids(p)

    def test_satisfactory_opens_windows(self):
        p = build_plan(air("Satisfactory"), profile())
        assert "vent_open" in ids(p) and "vent_close" not in ids(p)

    def test_moderate_neither(self):
        p = build_plan(air("Moderate"), profile())
        assert "vent_open" not in ids(p) and "vent_close" not in ids(p)


class TestR2COSafety:
    def test_override_beats_close_windows(self):
        p = build_plan(air("Severe"), profile(home={"unvented_combustion": True}))
        assert ids(p)[0] == "co_safety"
        assert "vent_close" in ids(p)
        a = get(p, "co_safety")
        assert "Never seal" in a["en"] or "SAFETY" in a["en"]

    def test_no_combustion_no_override(self):
        p = build_plan(air("Severe"), profile(home={"unvented_combustion": False}))
        assert "co_safety" not in ids(p)

    def test_applies_even_in_good_air(self):
        p = build_plan(air("Good"), profile(home={"unvented_combustion": True}))
        assert ids(p)[0] == "co_safety"


class TestR3Purifier:
    def test_owns_purifier_runs_it(self):
        p = build_plan(air("Poor"), profile(home={"purifier": True}))
        assert "purifier_run" in ids(p) and "diy_purifier" not in ids(p)

    def test_missing_purifier_gets_diy(self):
        p = build_plan(air("Poor"), profile(home={"purifier": False}))
        assert "diy_purifier" in ids(p) and "purifier_run" not in ids(p)

    def test_diy_not_in_good_air(self):
        p = build_plan(air("Good"), profile(home={"purifier": False}))
        assert "diy_purifier" not in ids(p)


class TestR4Cooking:
    def test_exhaust_always(self):
        for b in ("Good", "Moderate", "Severe"):
            assert "cooking_exhaust" in ids(build_plan(air(b), profile()))

    def test_biomass_gets_swap(self):
        p = build_plan(air("Good"), profile(home={"cooking_fuel": "biomass"}))
        assert "swap_biomass_cooking" in ids(p)

    def test_gas_gets_induction_with_honest_co2e(self):
        p = build_plan(air("Good"), profile(home={"cooking_fuel": "gas"}))
        a = get(p, "swap_gas_induction")
        assert a["impact"]["value"] < 0  # honest: induction is worse CO2e today
        assert "MORE CO2e" in a["impact"]["note"]

    def test_induction_never_lpg_co2e_without_gas(self):
        p = build_plan(air("Poor"), profile(home={"cooking_fuel": "biomass"}))
        assert "swap_gas_induction" not in ids(p)


class TestR5Commute:
    def test_walk_in_very_poor_shifts(self):
        p = build_plan(air("Very Poor"), profile(commute={"mode": "walk", "km": 4}))
        assert "commute_shift" in ids(p)

    def test_walk_in_poor_not_shifted(self):
        p = build_plan(air("Poor"), profile(commute={"mode": "walk", "km": 4}))
        assert "commute_shift" not in ids(p)

    def test_cycle_in_very_poor_shifts(self):
        p = build_plan(air("Very Poor"), profile(commute={"mode": "cycle", "km": 8}))
        assert "commute_shift" in ids(p)

    def test_two_wheeler_gets_mask(self):
        p = build_plan(air("Poor"), profile(commute={"mode": "two_wheeler", "km": 15}))
        assert "twowheeler_mask" in ids(p)
        assert "N95" in get(p, "twowheeler_mask")["en"]

    def test_two_wheeler_good_air_no_mask(self):
        p = build_plan(air("Good"), profile(commute={"mode": "two_wheeler", "km": 15}))
        assert "twowheeler_mask" not in ids(p)

    def test_car_gets_swap_with_math(self):
        p = build_plan(air("Moderate"), profile(commute={"mode": "car", "km": 20}))
        a = get(p, "car_swap")
        assert a["impact"]["value"] == pytest.approx(2.629, abs=0.01)
        assert "0.16691" in a["impact"]["formula"]

    def test_car_no_km_no_math(self):
        p = build_plan(air("Moderate"), profile(commute={"mode": "car", "km": 0}))
        assert get(p, "car_swap")["impact"]["value"] is None


class TestR6Children:
    def test_very_poor_child_indoor(self):
        members = [{"label": "Ria (6)", "age_years": 6}]
        p = build_plan(air("Very Poor"), profile(members=members))
        assert "school_indoor" in ids(p)

    def test_poor_child_not_forced_indoor(self):
        members = [{"label": "Ria (6)", "age_years": 6}]
        p = build_plan(air("Poor"), profile(members=members))
        assert "school_indoor" not in ids(p)

    def test_no_child_no_school_action(self):
        p = build_plan(air("Severe"), profile(members=[{"label": "Dad", "age_years": 40}]))
        assert "school_indoor" not in ids(p)

    def test_mask_note_cites_who(self):
        members = [{"label": "Ria (6)", "age_years": 6}]
        p = build_plan(air("Very Poor"), profile(members=members))
        assert "WHO" in get(p, "school_indoor")["mask_note"]

    def test_under5_card_says_no_mask(self):
        members = [{"label": "Baby (2)", "age_years": 2}]
        p = build_plan(air("Severe"), profile(members=members))
        card = p["members"][0]
        assert "mask_under5_none" in card["actions"]


class TestR7Elders:
    def test_moderate_elder_walk_advice(self):
        members = [{"label": "Dada (70)", "age_years": 70}]
        p = build_plan(air("Moderate"), profile(members=members))
        assert "elder_walk" in ids(p)

    def test_good_no_advice(self):
        members = [{"label": "Dada (70)", "age_years": 70}]
        p = build_plan(air("Good"), profile(members=members))
        assert "elder_walk" not in ids(p)

    def test_sensitive_asthma_member_gets_medicine_plan(self):
        members = [{"label": "Mom (38, asthma)", "age_years": 38, "sensitive": True}]
        p = build_plan(air("Poor"), profile(members=members))
        card = p["members"][0]
        assert "keep_medicines_plan" in card["actions"]


class TestR8Exercise:
    def test_poor_moves_indoors(self):
        p = build_plan(air("Poor"), profile())
        assert "exercise_indoor" in ids(p)

    def test_moderate_no_forced_indoor(self):
        p = build_plan(air("Moderate"), profile())
        assert "exercise_indoor" not in ids(p)


class TestR9Heating:
    def test_always_advises_no_burning(self):
        assert "heating_no_burn" in ids(build_plan(air("Good"), profile()))
        assert "heating_no_burn" in ids(build_plan(air("Severe"), profile()))


class TestR10Community:
    def test_poor_generates_notice(self):
        p = build_plan(air("Poor"), profile())
        assert "community_notice" in ids(p)

    def test_satisfactory_no_notice(self):
        p = build_plan(air("Satisfactory"), profile())
        assert "community_notice" not in ids(p)


class TestR11BadData:
    def test_stale_flag_passed_through(self):
        p = build_plan(air("Poor", stale=True), profile())
        assert p["stale"] is True

    def test_sample_flag_passed_through(self):
        p = build_plan(air("Poor", sample=True), profile())
        assert p["sample"] is True


class TestSensitiveShift:
    def test_shifts_one_band_worse(self):
        p = build_plan(air("Moderate"), profile(members=[{"age_years": 40, "sensitive": True}]))
        assert p["effective_band"] == "Poor"
        assert p["sensitive_shift"] is True

    def test_shift_changes_actions(self):
        # Moderate alone: no forced indoor exercise; with sensitive member: Poor -> indoor
        p = build_plan(air("Moderate"), profile(members=[{"age_years": 40, "sensitive": True}]))
        assert "exercise_indoor" in ids(p)

    def test_severe_cannot_shift_further(self):
        p = build_plan(air("Severe"), profile(members=[{"age_years": 40, "sensitive": True}]))
        assert p["effective_band"] == "Severe"
        assert p["sensitive_shift"] is False


class TestDeterminismAndShape:
    THREE_HOUSEHOLDS: ClassVar = [
        (air("Severe", aqi=430), profile(
            members=[{"label": "Ria (6)", "age_years": 6},
                     {"label": "Dada (70)", "age_years": 70, "sensitive": True}],
            home={"purifier": False, "cooking_fuel": "gas"},
            commute={"mode": "car", "km": 20})),
        (air("Very Poor", aqi=340), profile(
            members=[{"label": "Aarav (10)", "age_years": 10, "sensitive": True}],
            home={"purifier": True, "unvented_combustion": True, "cooking_fuel": "biomass"},
            commute={"mode": "two_wheeler", "km": 12})),
        (air("Satisfactory", aqi=70), profile(
            members=[{"label": "Sole adult", "age_years": 35}],
            home={"cooking_fuel": "gas"},
            commute={"mode": "metro", "km": 15})),
    ]

    def test_full_plan_no_network_no_ai(self):
        for a, prof in self.THREE_HOUSEHOLDS:
            p = build_plan(a, prof)
            assert p["actions"], "plan must not be empty"
            assert all("why" in x and "impact" in x and "en" in x and "hi" in x for x in p["actions"])

    def test_deterministic_output(self):
        for a, prof in self.THREE_HOUSEHOLDS:
            assert build_plan(a, prof) == build_plan(a, prof)

    def test_every_action_has_rule_id(self):
        catalog = {a["id"]: a for a in load_actions()}
        for a, prof in self.THREE_HOUSEHOLDS:
            p = build_plan(a, prof)
            for item in p["actions"]:
                assert item["why"]["rule_id"] == catalog[item["id"]]["rule_id"]

    def test_catalog_rule_ids_are_the_documented_set(self):
        got = {a["rule_id"] for a in load_actions()}
        assert got == {"R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10"}

    def test_r11_is_data_layer_flag(self):
        assert "R11" not in {a["rule_id"] for a in load_actions()}  # handled in air_data/main


class TestMemberCards:
    def test_card_labels_and_flags(self):
        members = [{"label": "Ria (6)", "age_years": 6},
                   {"label": "Dada (70)", "age_years": 70, "sensitive": True}]
        p = build_plan(air("Severe"), profile(members=members))
        labels = [c["label"] for c in p["members"]]
        assert labels == ["Ria (6)", "Dada (70)"]
        assert p["members"][1]["sensitive"] is True
