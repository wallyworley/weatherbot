from datetime import datetime, timezone

import pytest

from weather_bot.config import Station
from weather_bot.data import official_guidance_fetcher as og


def test_parse_lamp_hourly_temperature_rows():
    station = Station("KAUS", "Austin-Bergstrom", 30.195, -97.67, "America/Chicago")
    run_time = datetime(2026, 6, 5, 12, tzinfo=timezone.utc)
    text = """
AUS   GFS LAMP GUIDANCE   6/05/2026  1200 UTC
UTC  13 14 15 16 17 18
HR   13 14 15 16 17 18
TMP  72 74 77 80 82 83
WDR  18 18 19 19 20 20

DFW   GFS LAMP GUIDANCE   6/05/2026  1200 UTC
HR   13 14 15
TMP  78 80 82
"""
    rows = og.parse_hourly_temp_guidance(text, station, "LAMP", run_time)

    assert len(rows) == 6
    assert {r["station"] for r in rows} == {"KAUS"}
    assert {r["source"] for r in rows} == {"LAMP"}
    assert rows[0]["valid_time"].hour == 13
    assert rows[-1]["value"] == 83.0
    assert rows[-1]["valid_date"].isoformat() == "2026-06-05"


def test_parse_hourly_temperature_rolls_utc_date():
    station = Station("KNYC", "New York Central Park", 40.7794, -73.9692, "America/New_York")
    run_time = datetime(2026, 6, 5, 18, tzinfo=timezone.utc)
    text = """
NYC   GFS MOS GUIDANCE    6/05/2026  1800 UTC
HR   21 00 03 06
TMP  79 75 72 70
"""
    rows = og.parse_hourly_temp_guidance(text, station, "MAV", run_time)

    assert [r["valid_time"].day for r in rows] == [5, 6, 6, 6]
    assert [r["valid_time"].hour for r in rows] == [21, 0, 3, 6]


def test_parse_mav_fixed_width_three_digit_temperatures():
    station = Station("KPHX", "Phoenix Sky Harbor", 33.4352, -112.0101, "America/Phoenix")
    run_time = datetime(2026, 6, 5, 12, tzinfo=timezone.utc)
    text = """
 KPHX   GFS MOS GUIDANCE    6/05/2026  1200 UTC
 HR   18 21 00 03 06 09 12 15 18 21 00 03 06 09 12 15 18 21 00 06 12
 TMP  99106107101 93 88 81 85 96103104100 92 87 82 85 94101102 90 80
"""
    rows = og.parse_hourly_temp_guidance(text, station, "MAV", run_time)

    assert len(rows) == 21
    assert [r["value"] for r in rows[:5]] == [99.0, 106.0, 107.0, 101.0, 93.0]
    assert max(r["value"] for r in rows) == 107.0


def test_lamp_candidate_cycles_use_temperature_half_hour_products(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 6, 5, 19, 54, tzinfo=tz)

    monkeypatch.setattr(og, "datetime", FrozenDateTime)
    cycles = og._candidate_cycles("LAMP")

    assert cycles[0] == datetime(2026, 6, 5, 19, 30, tzinfo=timezone.utc)
    assert all(c.minute == 30 for c in cycles)
    assert [c.hour for c in cycles[:3]] == [19, 18, 17]


@pytest.mark.parametrize("source", ["MAV", "LAMP"])
def test_parse_hourly_guidance_ignores_blocks_without_tmp(source):
    station = Station("KPHX", "Phoenix Sky Harbor", 33.4352, -112.0101, "America/Phoenix")
    run_time = datetime(2026, 6, 5, 19, tzinfo=timezone.utc)
    marker = "GFS LAMP GUIDANCE" if source == "LAMP" else "GFS MOS GUIDANCE"
    text = f"""
 KPHX   {marker}   6/05/2026  1900 UTC
 UTC  20 21 22
 CIG   8  8  8
 VIS   7  7  7
 OBV   N  N  N
"""
    assert og.parse_hourly_temp_guidance(text, station, source, run_time) == []


def test_parse_pfm_mxmn_best_effort():
    station = Station("KAUS", "Austin-Bergstrom", 30.195, -97.67, "America/Chicago")
    issued = datetime(2026, 6, 5, 10, tzinfo=timezone.utc)
    text = """
FOUS54 KEWX 051000
PFMEWX

AUSTIN BERGSTROM INTERNATIONAL AIRPORT-TX
400 AM CST FRI JUN 5 2026
DATE             FRI 06/05/26            SAT 06/06/26
UTC 3HRLY        12 15 18 21 00 03 06 09 12 15 18 21
MX/MN                    91          73          94          75
TEMP             72 78 86 91 84 78 74 73 76 84 92 94
$$
"""
    rows = og.parse_pfm_mxmn(text, station, issued)

    assert rows[0]["source"] == "NWS_PFM"
    assert rows[0]["var"] == "TMAX_DAILY"
    assert rows[0]["value"] == 91.0
    assert rows[1]["var"] == "TMIN_DAILY"
    assert rows[2]["valid_date"].isoformat() == "2026-06-06"


def test_nws_grid_temperature_conversion(monkeypatch):
    station = Station("KZZZ", "Test", 30.0, -90.0, "America/Chicago")

    class Resp:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_get(url, headers=None, timeout=None, params=None):
        if "/points/" in url:
            return Resp({"properties": {"forecastGridData": "https://example.test/grid"}})
        return Resp({
            "properties": {
                "updateTime": "2026-06-05T12:00:00+00:00",
                "gridId": "XXX",
                "gridX": 1,
                "gridY": 2,
                "maxTemperature": {
                    "uom": "wmoUnit:degC",
                    "values": [{"validTime": "2026-06-05T12:00:00+00:00/PT12H", "value": 35}],
                },
                "temperature": {
                    "uom": "wmoUnit:degC",
                    "values": [{"validTime": "2026-06-05T18:00:00+00:00/PT1H", "value": 30}],
                },
            }
        })

    monkeypatch.setattr("weather_bot.data.official_guidance_fetcher.requests.get", fake_get)
    rows = og.fetch_nws_grid(station)

    assert {(r["var"], round(r["value"], 1)) for r in rows} == {
        ("TMAX_DAILY", 95.0),
        ("TMP_2M", 86.0),
    }


def test_default_station_codes_can_include_neighbors():
    with_neighbors = og.default_station_codes(include_neighbors=True)
    without_neighbors = og.default_station_codes(include_neighbors=False)

    assert "KNYC" in without_neighbors
    assert "KJFK" not in without_neighbors
    assert "KJFK" in with_neighbors
    assert len(with_neighbors) > len(without_neighbors)


def test_nomads_text_paths_match_current_layout():
    run_time = datetime(2026, 6, 5, 18, 30, tzinfo=timezone.utc)

    assert og._text_urls("LAMP", run_time)[0].endswith(
        "/lmp/prod/lmp.20260605/lmp.t1830z.lavtxt.ascii"
    )

    mav_run = datetime(2026, 6, 5, 12, tzinfo=timezone.utc)
    assert og._text_urls("MAV", mav_run)[0].endswith(
        "/gfs_mos/prod/gfs_mos.20260605/mdl_gfsmav.t12z"
    )
