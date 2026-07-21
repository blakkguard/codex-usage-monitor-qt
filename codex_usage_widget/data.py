from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3


log = logging.getLogger(__name__)

CODEX_HOME = Path.home() / ".codex"
LOG_DB = CODEX_HOME / "logs_2.sqlite"
STATE_DB = CODEX_HOME / "state_5.sqlite"
SESSIONS_DIR = CODEX_HOME / "sessions"
WEEKLY_MINUTES = 7 * 24 * 60


@dataclass(frozen=True)
class LimitWindow:
    name: str
    used_percent: int | None
    window_minutes: int | None
    reset_at: datetime | None
    reset_after_seconds: int | None
    stale_reason: str | None = None

    @property
    def has_data(self) -> bool:
        return self.stale_reason is None and self.used_percent is not None and self.reset_at is not None


@dataclass(frozen=True)
class RateLimitSnapshot:
    updated_at: datetime | None
    plan_type: str | None
    allowed: bool | None
    limit_reached: bool | None
    weekly: LimitWindow
    log_id: int | None


@dataclass(frozen=True)
class ThreadSnapshot:
    model: str | None
    reasoning_effort: str | None
    updated_at: datetime | None
    cwd: str | None
    title: str | None
    tokens_used: int | None


@dataclass(frozen=True)
class UsageSnapshot:
    rate_limits: RateLimitSnapshot
    thread: ThreadSnapshot
    read_at: datetime
    error: str | None = None


def _connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _from_epoch(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).astimezone()
    except (OSError, TypeError, ValueError):
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone()
        return None


def _extract_rate_limits(data: object, now: datetime | None = None) -> RateLimitSnapshot | None:
    if not isinstance(data, dict):
        return None
    if now is None:
        now = datetime.now().astimezone()

    source = data
    payload = data.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("rate_limits"), dict):
        source = payload

    rate_limits = source.get("rate_limits")
    if not isinstance(rate_limits, dict):
        return None

    windows = [_limit_window("raw", rate_limits.get("primary")), _limit_window("raw", rate_limits.get("secondary"))]
    weekly = _select_window("weekly", WEEKLY_MINUTES, windows, now)
    log.debug(
        "parsed rate limits updated_at=%s weekly=%s raw=%s",
        _from_epoch(data.get("ts") or data.get("timestamp") or source.get("ts") or source.get("timestamp")),
        weekly,
        windows,
    )
    return RateLimitSnapshot(
        updated_at=_from_epoch(data.get("ts") or data.get("timestamp") or source.get("ts") or source.get("timestamp")),
        plan_type=source.get("plan_type") or rate_limits.get("plan_type") or data.get("plan_type"),
        allowed=rate_limits.get("allowed"),
        limit_reached=rate_limits.get("limit_reached"),
        weekly=weekly,
        log_id=None,
    )


def _extract_event(body: str) -> dict | None:
    marker = 'websocket event: {"type":"codex.rate_limits"'
    pos = body.find(marker)
    if pos == -1:
        marker = 'sse event: {"type":"codex.rate_limits"'
        pos = body.find(marker)
    if pos == -1:
        return None

    json_start = body.find("{", pos)
    if json_start == -1:
        return None

    decoder = json.JSONDecoder()
    try:
        event, _ = decoder.raw_decode(body[json_start:])
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _limit_window(name: str, data: object) -> LimitWindow:
    if not isinstance(data, dict):
        return LimitWindow(name, None, None, None, None, "missing")

    used = data.get("used_percent")
    window = data.get("window_minutes")
    reset_after = data.get("reset_after_seconds")
    try:
        used = int(used) if used is not None else None
    except (TypeError, ValueError):
        used = None
    try:
        window = int(window) if window is not None else None
    except (TypeError, ValueError):
        window = None
    try:
        reset_after = int(reset_after) if reset_after is not None else None
    except (TypeError, ValueError):
        reset_after = None

    return LimitWindow(
        name=name,
        used_percent=used,
        window_minutes=window,
        reset_at=_from_epoch(data.get("reset_at") or data.get("resets_at")),
        reset_after_seconds=reset_after,
    )


def _select_window(name: str, expected_minutes: int, windows: list[LimitWindow], now: datetime) -> LimitWindow:
    for window in windows:
        if window.window_minutes != expected_minutes:
            continue
        if window.used_percent is None or window.reset_at is None:
            log.debug(
                "%s rate limit stale: incomplete data used=%s reset_at=%s window_minutes=%s",
                name,
                window.used_percent,
                window.reset_at,
                window.window_minutes,
            )
            return LimitWindow(name, None, expected_minutes, None, window.reset_after_seconds, "incomplete")
        if window.reset_at <= now:
            log.debug(
                "%s rate limit stale: reset_at=%s now=%s used=%s",
                name,
                window.reset_at,
                now,
                window.used_percent,
            )
            return LimitWindow(name, None, expected_minutes, None, window.reset_after_seconds, "expired")
        log.debug(
            "%s rate limit current: used=%s reset_at=%s reset_after_seconds=%s",
            name,
            window.used_percent,
            window.reset_at,
            window.reset_after_seconds,
        )
        return LimitWindow(name, window.used_percent, expected_minutes, window.reset_at, window.reset_after_seconds)
    log.debug("%s rate limit stale: no %s-minute window in latest event", name, expected_minutes)
    return LimitWindow(name, None, expected_minutes, None, None, "missing")


def _load_rate_limits_from_sessions(sessions_dir: Path = SESSIONS_DIR) -> RateLimitSnapshot:
    empty = RateLimitSnapshot(
        updated_at=None,
        plan_type=None,
        allowed=None,
        limit_reached=None,
        weekly=_limit_window("weekly", None),
        log_id=None,
    )
    if not sessions_dir.exists():
        return empty

    best_snapshot: RateLimitSnapshot | None = None
    best_updated_at: datetime | None = None
    session_files = sorted(
        sessions_dir.rglob("*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for session_file in session_files:
        try:
            lines = session_file.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "token_count":
                continue
            snapshot = _extract_rate_limits(event)
            if snapshot is None or snapshot.updated_at is None:
                continue
            if best_updated_at is None or snapshot.updated_at > best_updated_at:
                best_snapshot = snapshot
                best_updated_at = snapshot.updated_at

    return best_snapshot or empty


def load_rate_limits(log_db: Path = LOG_DB) -> RateLimitSnapshot:
    session_snapshot = _load_rate_limits_from_sessions()
    if session_snapshot.updated_at is not None:
        return session_snapshot

    empty = RateLimitSnapshot(
        updated_at=None,
        plan_type=None,
        allowed=None,
        limit_reached=None,
        weekly=_limit_window("weekly", None),
        log_id=None,
    )
    if not log_db.exists():
        return empty

    with _connect_readonly(log_db) as con:
        rows = con.execute(
            """
            select id, ts, feedback_log_body
            from logs
            where feedback_log_body like '%websocket event: {"type":"codex.rate_limits"%'
               or feedback_log_body like '%sse event: {"type":"codex.rate_limits"%'
            order by id desc
            limit 200
            """
        ).fetchall()

    best_snapshot: RateLimitSnapshot | None = None
    best_updated_at: datetime | None = None
    for log_id, ts, body in rows:
        if not isinstance(body, str):
            continue
        event = _extract_event(body)
        if not event:
            continue
        snapshot = _extract_rate_limits(event)
        if snapshot is None:
            continue
        candidate = RateLimitSnapshot(
            updated_at=snapshot.updated_at or _from_epoch(ts),
            plan_type=snapshot.plan_type,
            allowed=snapshot.allowed,
            limit_reached=snapshot.limit_reached,
            weekly=snapshot.weekly,
            log_id=int(log_id),
        )
        if candidate.updated_at is None:
            continue
        if best_updated_at is None or candidate.updated_at > best_updated_at:
            best_snapshot = candidate
            best_updated_at = candidate.updated_at

    return best_snapshot or empty


def load_thread(state_db: Path = STATE_DB) -> ThreadSnapshot:
    empty = ThreadSnapshot(None, None, None, None, None, None)
    if not state_db.exists():
        return empty

    with _connect_readonly(state_db) as con:
        row = con.execute(
            """
            select model, reasoning_effort, updated_at, cwd, title, tokens_used
            from threads
            where source = 'cli'
            order by updated_at desc
            limit 1
            """
        ).fetchone()

    if not row:
        return empty

    model, effort, updated_at, cwd, title, tokens_used = row
    try:
        tokens_used = int(tokens_used) if tokens_used is not None else None
    except (TypeError, ValueError):
        tokens_used = None
    return ThreadSnapshot(
        model=model,
        reasoning_effort=effort,
        updated_at=_from_epoch(updated_at),
        cwd=cwd,
        title=title,
        tokens_used=tokens_used,
    )


def load_usage() -> UsageSnapshot:
    read_at = datetime.now().astimezone()
    errors: list[str] = []
    try:
        rate_limits = load_rate_limits()
    except sqlite3.Error as exc:
        rate_limits = RateLimitSnapshot(
            updated_at=None,
            plan_type=None,
            allowed=None,
            limit_reached=None,
            weekly=_limit_window("weekly", None),
            log_id=None,
        )
        errors.append(f"rate limits: {exc}")

    try:
        thread = load_thread()
    except sqlite3.Error as exc:
        thread = ThreadSnapshot(None, None, None, None, None, None)
        errors.append(f"thread: {exc}")

    return UsageSnapshot(
        rate_limits=rate_limits,
        thread=thread,
        read_at=read_at,
        error="; ".join(errors) if errors else None,
    )


def _debug_print() -> None:
    snapshot = load_usage()
    print(snapshot)


if __name__ == "__main__":
    _debug_print()
