from datetime import datetime, timedelta, timezone

from codex_usage_widget.data import _extract_rate_limits


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def event(primary=None, secondary=None):
    return {"timestamp": NOW.timestamp(), "payload": {"rate_limits": {"primary": primary, "secondary": secondary}}}


def window(minutes, used, reset):
    return {"window_minutes": minutes, "used_percent": used, "resets_at": reset.timestamp()}


def test_valid_weekly_data():
    snapshot = _extract_rate_limits(
        event(
            window(300, 12, NOW + timedelta(hours=2)),
            window(10080, 34, NOW + timedelta(days=3)),
        ),
        NOW,
    )

    assert snapshot.weekly.used_percent == 34
    assert snapshot.weekly.reset_at == NOW + timedelta(days=3)


def test_expired_weekly_window_is_stale():
    snapshot = _extract_rate_limits(
        event(
            window(300, 12, NOW + timedelta(hours=2)),
            window(10080, 34, NOW - timedelta(minutes=1)),
        ),
        NOW,
    )

    assert not snapshot.weekly.has_data
    assert snapshot.weekly.stale_reason == "expired"


def test_missing_weekly_data_is_stale():
    snapshot = _extract_rate_limits(event(window(300, 12, NOW + timedelta(hours=2)), None), NOW)

    assert not snapshot.weekly.has_data
    assert snapshot.weekly.stale_reason == "missing"


def test_weekly_window_can_appear_in_primary_slot():
    snapshot = _extract_rate_limits(
        event(
            window(10080, 8, NOW + timedelta(days=6, hours=20)),
            None,
        ),
        NOW,
    )

    assert snapshot.weekly.used_percent == 8
    assert snapshot.weekly.reset_at == NOW + timedelta(days=6, hours=20)
