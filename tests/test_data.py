from datetime import datetime, timedelta, timezone

from codex_usage_widget.data import _extract_rate_limits


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def event(primary=None, secondary=None):
    return {"timestamp": NOW.timestamp(), "payload": {"rate_limits": {"primary": primary, "secondary": secondary}}}


def window(minutes, used, reset):
    return {"window_minutes": minutes, "used_percent": used, "resets_at": reset.timestamp()}


def test_valid_5h_and_weekly_data():
    snapshot = _extract_rate_limits(
        event(
            window(300, 12, NOW + timedelta(hours=2)),
            window(10080, 34, NOW + timedelta(days=3)),
        ),
        NOW,
    )

    assert snapshot.primary.used_percent == 12
    assert snapshot.primary.reset_at == NOW + timedelta(hours=2)
    assert snapshot.secondary.used_percent == 34
    assert snapshot.secondary.reset_at == NOW + timedelta(days=3)


def test_expired_5h_window_is_stale():
    snapshot = _extract_rate_limits(
        event(
            window(300, 12, NOW - timedelta(minutes=1)),
            window(10080, 34, NOW + timedelta(days=3)),
        ),
        NOW,
    )

    assert not snapshot.primary.has_data
    assert snapshot.primary.stale_reason == "expired"
    assert snapshot.secondary.has_data


def test_expired_weekly_window_is_stale():
    snapshot = _extract_rate_limits(
        event(
            window(300, 12, NOW + timedelta(hours=2)),
            window(10080, 34, NOW - timedelta(minutes=1)),
        ),
        NOW,
    )

    assert snapshot.primary.has_data
    assert not snapshot.secondary.has_data
    assert snapshot.secondary.stale_reason == "expired"


def test_missing_5h_data_is_stale():
    snapshot = _extract_rate_limits(event(None, window(10080, 34, NOW + timedelta(days=3))), NOW)

    assert not snapshot.primary.has_data
    assert snapshot.primary.stale_reason == "missing"
    assert snapshot.secondary.has_data


def test_missing_weekly_data_is_stale():
    snapshot = _extract_rate_limits(event(window(300, 12, NOW + timedelta(hours=2)), None), NOW)

    assert snapshot.primary.has_data
    assert not snapshot.secondary.has_data
    assert snapshot.secondary.stale_reason == "missing"


def test_weekly_reset_cannot_populate_5h_row():
    snapshot = _extract_rate_limits(event(window(10080, 8, NOW + timedelta(days=6, hours=20)), None), NOW)

    assert not snapshot.primary.has_data
    assert snapshot.primary.reset_at is None
    assert snapshot.secondary.used_percent == 8
    assert snapshot.secondary.reset_at == NOW + timedelta(days=6, hours=20)
