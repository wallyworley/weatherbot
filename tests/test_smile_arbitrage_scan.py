from datetime import date, datetime, timezone

from weather_bot.jobs.smile_arbitrage_scan import (
    BucketQuote,
    EventScan,
    _format_md,
    _is_partition,
)


def _q(lo, hi, ask=None, bid=None, ask_size=10, bid_size=10, ts=None):
    return BucketQuote(
        ticker=f"T{lo}_{hi}",
        lower_f=lo, upper_f=hi,
        yes_ask=ask, yes_bid=bid,
        yes_ask_size=ask_size, yes_bid_size=bid_size,
        snapshot_ts=ts or datetime(2026, 5, 17, 14, tzinfo=timezone.utc),
        status="active",
    )


def test_is_partition_clean_buckets():
    buckets = [
        _q(None, 50.0),
        _q(50.0, 60.0),
        _q(60.0, 70.0),
        _q(70.0, None),
    ]
    assert _is_partition(buckets) is True


def test_is_partition_rejects_gap():
    buckets = [_q(50.0, 60.0), _q(65.0, 70.0)]  # 60→65 gap
    assert _is_partition(buckets) is False


def test_is_partition_rejects_overlap():
    buckets = [_q(50.0, 65.0), _q(60.0, 70.0)]
    assert _is_partition(buckets) is False


def test_yes_mid_handles_missing_quotes():
    assert _q(50, 60, ask=None, bid=0.30).yes_mid is None
    assert _q(50, 60, ask=0.40, bid=0.30).yes_mid == 0.35


def test_format_md_empty():
    md = _format_md([], 0.03, 30, datetime(2026, 5, 17, 14, tzinfo=timezone.utc))
    assert "No events flagged" in md


def test_format_md_renders_event():
    q1 = _q(None, 60.0, ask=0.10, bid=0.08)   # mid 0.09
    q2 = _q(60.0, 70.0, ask=0.55, bid=0.50)   # mid 0.525
    q3 = _q(70.0, None, ask=0.55, bid=0.50)   # mid 0.525
    over = 0.09 + 0.525 + 0.525  # = 1.14
    scored = [(q, q.yes_mid / over, q.yes_mid / over - q.yes_mid) for q in (q1, q2, q3)]
    ev = EventScan(
        event_ticker="KXTEST",
        station="KNYC", var="TMAX_DAILY",
        valid_date=date(2026, 5, 17),
        over_round=over, bucket_count=3, quotes_complete=3,
        buckets=scored,
    )
    md = _format_md([ev], 0.03, 30, datetime(2026, 5, 17, 14, tzinfo=timezone.utc))
    assert "KXTEST" in md
    assert "overpaying YES" in md
    assert "Suggested" in md
