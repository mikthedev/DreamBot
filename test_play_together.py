from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from database import Database
from play_together import (
    detect_groups,
    format_days_ago,
    format_play_when,
    game_key,
    join_names,
    next_saturday_evening,
    parse_party_size,
    parse_play_when,
    recency_weight,
    should_auto_suggest,
    social_score,
)


def test_game_key():
    assert game_key("  The  Big Walk ") == "the big walk"
    assert game_key("CS2") == "cs2"


def test_recency_weight():
    assert recency_weight(0) == 1.0
    assert abs(recency_weight(7) - 0.5) < 1e-9
    assert recency_weight(14) < recency_weight(7)
    assert recency_weight(30) < 0.1


def test_should_auto_suggest():
    assert not should_auto_suggest(1, 1.0, 4)
    assert not should_auto_suggest(3, 3.0, 4)
    assert not should_auto_suggest(4, 1.0, 4)
    assert should_auto_suggest(4, 2.0, 4)
    assert should_auto_suggest(4, 3.0, 4)


def test_next_saturday_evening():
    tz = ZoneInfo("Europe/Kyiv")
    wed = datetime(2026, 8, 12, 10, 0, tzinfo=tz)
    slot = next_saturday_evening(wed, hour=19)
    assert slot.weekday() == 5
    assert slot.day == 15
    assert slot.hour == 19

    sat_morning = datetime(2026, 8, 15, 10, 0, tzinfo=tz)
    assert next_saturday_evening(sat_morning, hour=19).day == 15

    sat_night = datetime(2026, 8, 15, 20, 0, tzinfo=tz)
    later = next_saturday_evening(sat_night, hour=19)
    assert later.day == 22


def test_parse_play_when():
    tz = ZoneInfo("Europe/Kyiv")
    now = datetime(2026, 8, 13, 12, 0, tzinfo=tz)
    empty = parse_play_when("", now, hour=19)
    assert empty is not None and empty.day == 15 and empty.hour == 19
    parsed = parse_play_when("15.08.2026 19:00", now)
    assert parsed == datetime(2026, 8, 15, 19, 0, tzinfo=tz)
    yearless = parse_play_when("15.08 19:00", now)
    assert yearless == datetime(2026, 8, 15, 19, 0, tzinfo=tz)
    assert parse_play_when("nope", now) is None


def test_parse_party_size():
    assert parse_party_size("3-6", 3, 6) == (3, 6)
    assert parse_party_size("4/10", 3, 6) == (4, 10)
    assert parse_party_size("5", 3, 6) == (5, 6)
    assert parse_party_size("", 3, 6) == (3, 6)
    assert parse_party_size("6-3", 3, 6) == (3, 6)


def test_join_and_relative():
    assert join_names(["Mike"]) == "Mike"
    assert join_names(["Mike", "Ilya"]) == "Mike and Ilya"
    assert join_names(["Mike", "Ilya", "Sasha"]) == "Mike, Ilya and Sasha"
    assert format_days_ago(0.2) == "today"
    assert format_days_ago(1.2) == "yesterday"
    assert format_days_ago(12) == "12 days ago"
    dt = datetime(2026, 8, 15, 19, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
    assert "Saturday" in format_play_when(dt)
    assert "15" in format_play_when(dt)
    assert "19:00" in format_play_when(dt)


def test_social_score_prefers_voice_and_this_game():
    confirmed = [1, 2]
    low = social_score(
        this_game_weight=0.1,
        voice_minutes={1: 0, 2: 0},
        shared_sessions={1: 0, 2: 0},
        confirmed_ids=confirmed,
    )
    high = social_score(
        this_game_weight=1.0,
        voice_minutes={1: 180, 2: 60},
        shared_sessions={1: 2, 2: 1},
        confirmed_ids=confirmed,
        shared_game_affinity=2.0,
    )
    assert high > low
    assert high > 8


def test_activity_session_and_detection():
    with TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        gid = 1
        now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
        t0 = now.isoformat()
        t1 = (now + timedelta(hours=1)).isoformat()
        t2 = (now + timedelta(hours=5)).isoformat()
        for uid in (10, 11, 12, 13):
            db.upsert_play_activity(gid, uid, "minecraft", "Minecraft", None, t0)
        db.upsert_play_activity(gid, 10, "minecraft", "Minecraft", None, t1)
        db.upsert_play_activity(gid, 10, "minecraft", "Minecraft", None, t2)
        rows = db.list_play_activity_for_game(gid, "minecraft", t0)
        counts = {int(r["user_id"]): int(r["play_count"]) for r in rows}
        assert counts[10] == 2
        assert counts[11] == 1

        db.upsert_play_game(gid, "minecraft", "Minecraft", enabled=1, blocked=0)
        db.upsert_play_game(gid, "warframe", "Warframe", enabled=0, blocked=1)
        db.upsert_play_activity(gid, 10, "warframe", "Warframe", None, t0)
        db.upsert_play_activity(gid, 11, "warframe", "Warframe", None, t0)
        db.upsert_play_activity(gid, 12, "warframe", "Warframe", None, t0)
        db.upsert_play_activity(gid, 13, "warframe", "Warframe", None, t0)

        groups = {g.game_key: g for g in detect_groups(db, gid, now=now + timedelta(hours=6))}
        assert groups["minecraft"].allowed
        assert groups["minecraft"].count == 4
        assert should_auto_suggest(
            groups["minecraft"].count, groups["minecraft"].weight_sum, 4
        )
        assert groups["warframe"].blocked
        assert not groups["warframe"].allowed


if __name__ == "__main__":
    test_game_key()
    test_recency_weight()
    test_should_auto_suggest()
    test_next_saturday_evening()
    test_parse_play_when()
    test_parse_party_size()
    test_join_and_relative()
    test_social_score_prefers_voice_and_this_game()
    test_activity_session_and_detection()
    print("ok")
