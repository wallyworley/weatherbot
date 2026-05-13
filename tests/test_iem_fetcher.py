from weather_bot.data.iem_fetcher import _parse_recent_csv


def test_parse_temp_only_t_group_when_tmpf_missing():
    csv_text = "\n".join(
        [
            "station,valid,tmpf,dwpf,sknt,metar",
            "MIA,2026-05-08 17:30,,,5,KMIA 081730Z AUTO 20005KT 10SM CLR A2997 RMK T0330 MADISHF",
        ]
    )

    rows = _parse_recent_csv(csv_text, "KMIA")

    assert len(rows) == 1
    assert rows[0]["temp_f"] == 91.4
    assert rows[0]["dewpoint_f"] is None


def test_parse_full_t_group_still_sets_temp_and_dewpoint():
    csv_text = "\n".join(
        [
            "station,valid,tmpf,dwpf,sknt,metar",
            "MIA,2026-05-08 17:50,,,5,KMIA 081750Z AUTO 12009KT 10SM CLR 32/20 A2996 RMK T03200200 MADISHF",
        ]
    )

    rows = _parse_recent_csv(csv_text, "KMIA")

    assert len(rows) == 1
    assert rows[0]["temp_f"] == 89.6
    assert rows[0]["dewpoint_f"] == 68.0
