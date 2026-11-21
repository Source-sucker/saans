"""AQI boundaries: every breakpoint edge, worst-of, GRAP, non-NCR."""
import pytest

from app.aqi import (
    aqi,
    band,
    band_index,
    better_band,
    grap_stage,
    pm10_sub,
    pm25_sub,
    sub_index,
    worse_band,
)


class TestSubIndex:
    @pytest.mark.parametrize("conc,expected", [
        (0, 0), (30, 50), (31, 52), (60, 100), (61, 103), (90, 200), (91, 203),
        (120, 300), (121, 301), (250, 400), (251, 500), (900, 500),
    ])
    def test_pm25_breakpoints(self, conc, expected):
        assert pm25_sub(conc) == expected

    @pytest.mark.parametrize("conc,expected", [
        (0, 0), (50, 50), (51, 51), (100, 100), (101, 101), (250, 200), (251, 201),
        (350, 300), (351, 301), (430, 400), (431, 500), (2000, 500),
    ])
    def test_pm10_breakpoints(self, conc, expected):
        assert pm10_sub(conc) == expected

    def test_midpoint_interpolates(self):
        # halfway between (30,50) and (60,100)
        assert pm25_sub(45) == 75

    def test_halfway_45_pm10(self):
        assert pm10_sub(75) == 75

    def test_negative_concentration_rejected(self):
        with pytest.raises(ValueError):
            pm25_sub(-1)

    def test_bad_breakpoints_rejected(self):
        with pytest.raises(ValueError):
            sub_index(-5, [(0, 0), (30, 50)])


class TestWorstOf:
    def test_pm25_dominates(self):
        assert aqi(pm25=100, pm10=50) == pm25_sub(100)

    def test_pm10_dominates(self):
        assert aqi(pm25=20, pm10=400) == pm10_sub(400)

    def test_equal(self):
        assert aqi(pm25=30, pm10=50) == 50

    def test_none_when_no_data(self):
        assert aqi(None, None) is None

    def test_single_pollutant(self):
        assert aqi(pm25=61, pm10=None) == 103


class TestBand:
    @pytest.mark.parametrize("aqi_value,expected", [
        (0, "Good"), (50, "Good"), (51, "Satisfactory"), (100, "Satisfactory"),
        (101, "Moderate"), (200, "Moderate"), (201, "Poor"), (300, "Poor"),
        (301, "Very Poor"), (400, "Very Poor"), (401, "Severe"), (500, "Severe"),
    ])
    def test_band_edges(self, aqi_value, expected):
        assert band(aqi_value) == expected

    def test_out_of_range(self):
        with pytest.raises(ValueError):
            band(501)

    def test_negative(self):
        with pytest.raises(ValueError):
            band(-1)


class TestBandHelpers:
    def test_band_index_roundtrip(self):
        assert band_index("Poor") == 3

    def test_worse_band(self):
        assert worse_band("Good") == "Satisfactory"
        assert worse_band("Severe") == "Severe"  # stops at top

    def test_better_band(self):
        assert better_band("Very Poor") == "Poor"
        assert better_band("Good") == "Good"  # stops at bottom


class TestGRAP:
    @pytest.mark.parametrize("aqi_value,expected", [
        (200, None), (201, "I"), (300, "I"), (301, "II"), (400, "II"),
        (401, "III"), (450, "III"), (451, "IV"), (500, "IV"),
    ])
    def test_ncr(self, aqi_value, expected):
        assert grap_stage(aqi_value, in_ncr=True) == expected

    @pytest.mark.parametrize("aqi_value", [150, 250, 350, 420, 460, 500])
    def test_non_ncr_returns_none(self, aqi_value):
        assert grap_stage(aqi_value, in_ncr=False) is None
