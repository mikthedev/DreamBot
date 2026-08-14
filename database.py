import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def _payload_has_hero_balance(raw: str | None) -> bool:
    """True when a stored patch should stay in the archive (retail or fun mode)."""
    if not raw:
        return False
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return False
    if data.get("fun_mode"):
        return True
    blob = f"{data.get('title') or ''} {data.get('fun_label') or ''}".lower()
    if "community crafted" in blob:
        return True
    if (data.get("date") or "").strip().lower() == "june 30, 2026":
        return True
    for hero in data.get("heroes") or []:
        if hero.get("changes"):
            return True
    return False


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS real_names (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    real_name TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id INTEGER PRIMARY KEY,
                    welcome_channel_id INTEGER,
                    auto_role_id INTEGER,
                    birthday_channel_id INTEGER,
                    now_playing_channel_id INTEGER,
                    now_playing_message_id INTEGER
                );

                CREATE TABLE IF NOT EXISTS birthdays (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    day INTEGER NOT NULL,
                    year INTEGER,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS birthday_announcements (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    year INTEGER NOT NULL,
                    announced_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (guild_id, user_id, year)
                );
                """
            )
            self._ensure_column(conn, "guild_settings", "birthday_channel_id", "INTEGER")
            self._ensure_column(conn, "guild_settings", "now_playing_channel_id", "INTEGER")
            self._ensure_column(conn, "guild_settings", "now_playing_message_id", "INTEGER")
            self._ensure_column(conn, "guild_settings", "birthday_signup_title", "TEXT")
            self._ensure_column(conn, "guild_settings", "birthday_signup_body", "TEXT")
            self._ensure_column(conn, "guild_settings", "birthday_signup_footer", "TEXT")
            self._ensure_column(conn, "birthdays", "set_by_admin", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "guild_settings", "anniversary_title", "TEXT")
            self._ensure_column(conn, "guild_settings", "anniversary_body", "TEXT")
            self._ensure_column(conn, "guild_settings", "anniversary_footer", "TEXT")
            self._ensure_column(conn, "guild_settings", "welcome_message", "TEXT")
            self._ensure_column(conn, "guild_settings", "ow_patch_channel_id", "INTEGER")
            self._ensure_column(conn, "guild_settings", "ow_patch_thread_id", "INTEGER")
            self._ensure_column(
                conn, "guild_settings", "ow_hero_history_thread_id", "INTEGER"
            )
            self._ensure_column(conn, "guild_settings", "ow_tier_channel_id", "INTEGER")
            self._ensure_column(conn, "guild_settings", "ow_tier_last_posted_at", "TEXT")
            self._ensure_column(conn, "guild_settings", "ow_tier_message_ids", "TEXT")
            self._ensure_column(conn, "guild_settings", "ow_tier_last_id", "TEXT")
            self._ensure_column(conn, "guild_settings", "ow_tier_thread_id", "INTEGER")
            self._ensure_column(conn, "guild_settings", "ow_meta_thread_id", "INTEGER")
            self._ensure_column(conn, "guild_settings", "ow_meta_message_ids", "TEXT")
            self._ensure_column(conn, "guild_settings", "ow_meta_last_id", "TEXT")
            self._ensure_column(conn, "guild_settings", "ow_meta_last_posted_at", "TEXT")
            self._ensure_column(conn, "guild_settings", "ow_news_channel_id", "INTEGER")
            self._ensure_column(conn, "guild_settings", "ow_news_seeded", "INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ow_news_posts (
                    guild_id INTEGER NOT NULL,
                    bsky_uri TEXT NOT NULL,
                    thread_id INTEGER,
                    posted_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (guild_id, bsky_uri)
                )
                """
            )
            self._ensure_column(
                conn, "ow_news_posts", "auto_close", "INTEGER NOT NULL DEFAULT 0"
            )
            self._ensure_column(conn, "ow_news_posts", "closed_at", "TEXT")
            self._ensure_column(conn, "guild_settings", "onboard_channel_id", "INTEGER")
            self._ensure_column(conn, "guild_settings", "onboard_message_id", "INTEGER")
            self._ensure_column(conn, "guild_settings", "onboard_title", "TEXT")
            self._ensure_column(conn, "guild_settings", "onboard_body", "TEXT")
            self._ensure_column(conn, "guild_settings", "ow_broadcast_role_id", "INTEGER")
            self._ensure_column(conn, "guild_settings", "voice_log_channel_id", "INTEGER")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ow_hero_emoji_icons (
                    emoji_name TEXT PRIMARY KEY,
                    icon_url TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS anniversary_announcements (
                    guild_id INTEGER NOT NULL,
                    year INTEGER NOT NULL,
                    announced_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (guild_id, year)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ow_patch_announcements (
                    guild_id INTEGER NOT NULL,
                    patch_id TEXT NOT NULL,
                    announced_at TEXT NOT NULL DEFAULT (datetime('now')),
                    message_ids TEXT,
                    payload TEXT,
                    PRIMARY KEY (guild_id, patch_id)
                )
                """
            )
            self._ensure_column(conn, "ow_patch_announcements", "message_ids", "TEXT")
            self._ensure_column(conn, "ow_patch_announcements", "payload", "TEXT")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS voice_log_messages (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    delete_at TEXT NOT NULL,
                    PRIMARY KEY (channel_id, message_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ow_hero_alerts (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    hero_key TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (guild_id, user_id, hero_key)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ow_hero_alert_sent (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    hero_key TEXT NOT NULL,
                    patch_id TEXT NOT NULL,
                    sent_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (guild_id, user_id, hero_key, patch_id)
                )
                """
            )
            self._init_play_together_schema(conn)
        self.purge_empty_ow_patches()

    def _init_play_together_schema(self, conn: sqlite3.Connection) -> None:
        for column, col_type in (
            ("play_suggest_channel_id", "INTEGER"),
            ("play_voice_channel_id", "INTEGER"),
            ("play_auto_enabled", "INTEGER NOT NULL DEFAULT 0"),
            ("play_auto_event", "INTEGER NOT NULL DEFAULT 1"),
            ("play_auto_expand", "INTEGER NOT NULL DEFAULT 1"),
            ("play_decay_days", "INTEGER NOT NULL DEFAULT 30"),
            ("play_detect_days", "INTEGER NOT NULL DEFAULT 14"),
            ("play_detect_min_people", "INTEGER NOT NULL DEFAULT 4"),
            ("play_default_hour", "INTEGER NOT NULL DEFAULT 19"),
            ("play_default_min_players", "INTEGER NOT NULL DEFAULT 3"),
            ("play_default_max_players", "INTEGER NOT NULL DEFAULT 6"),
            ("play_cooldown_days", "INTEGER NOT NULL DEFAULT 7"),
            ("play_suggest_title", "TEXT"),
            ("play_suggest_body", "TEXT"),
            ("play_suggest_footer", "TEXT"),
        ):
            self._ensure_column(conn, "guild_settings", column, col_type)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS play_activity (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                game_key TEXT NOT NULL,
                game_name TEXT NOT NULL,
                application_id INTEGER,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                play_count INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (guild_id, user_id, game_key)
            );
            CREATE TABLE IF NOT EXISTS play_games (
                guild_id INTEGER NOT NULL,
                game_key TEXT NOT NULL,
                game_name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                blocked INTEGER NOT NULL DEFAULT 0,
                min_players INTEGER,
                max_players INTEGER,
                steam_url TEXT,
                store_note TEXT,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (guild_id, game_key)
            );
            CREATE TABLE IF NOT EXISTS play_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                game_key TEXT NOT NULL,
                game_name TEXT NOT NULL,
                status TEXT NOT NULL,
                proposed_at TEXT NOT NULL,
                min_players INTEGER NOT NULL,
                max_players INTEGER NOT NULL,
                channel_id INTEGER,
                message_id INTEGER,
                discord_event_id INTEGER,
                steam_url TEXT,
                store_note TEXT,
                created_by INTEGER,
                created_at TEXT NOT NULL,
                reminder_sent INTEGER NOT NULL DEFAULT 0,
                expansion_sent INTEGER NOT NULL DEFAULT 0,
                auto_event INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS play_rsvps (
                suggestion_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                source TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (suggestion_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS play_voice_pairs (
                guild_id INTEGER NOT NULL,
                user_a INTEGER NOT NULL,
                user_b INTEGER NOT NULL,
                minutes INTEGER NOT NULL DEFAULT 0,
                last_together TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_a, user_b)
            );
            CREATE TABLE IF NOT EXISTS play_expansion_invites (
                suggestion_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY (suggestion_id, user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_play_activity_seen
                ON play_activity (guild_id, game_key, last_seen);
            CREATE INDEX IF NOT EXISTS idx_play_suggestions_guild
                ON play_suggestions (guild_id, status, game_key);
            """
        )
        for table, column, col_type in (
            ("play_games", "icon_url", "TEXT"),
            ("play_games", "image_url", "TEXT"),
            ("play_suggestions", "icon_url", "TEXT"),
            ("play_suggestions", "image_url", "TEXT"),
            ("play_suggestions", "price_text", "TEXT"),
        ):
            self._ensure_column(conn, table, column, col_type)

    def _ensure_column(
        self, conn: sqlite3.Connection, table: str, column: str, col_type: str
    ) -> None:
        cols = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

    def set_real_name(self, guild_id: int, user_id: int, real_name: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO real_names (guild_id, user_id, real_name, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    real_name = excluded.real_name,
                    updated_at = datetime('now')
                """,
                (guild_id, user_id, real_name),
            )

    def get_real_name(self, guild_id: int, user_id: int) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT real_name FROM real_names WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
        return row["real_name"] if row else None

    def all_real_names(self, guild_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT user_id, real_name FROM real_names WHERE guild_id = ?",
                (guild_id,),
            ).fetchall()

    def set_birthday(
        self,
        guild_id: int,
        user_id: int,
        month: int,
        day: int,
        year: int | None = None,
        *,
        set_by_admin: bool = False,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO birthdays (
                    guild_id, user_id, month, day, year, set_by_admin, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    month = excluded.month,
                    day = excluded.day,
                    year = excluded.year,
                    set_by_admin = excluded.set_by_admin,
                    updated_at = datetime('now')
                """,
                (guild_id, user_id, month, day, year, 1 if set_by_admin else 0),
            )

    def get_birthday(self, guild_id: int, user_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT month, day, year, set_by_admin
                FROM birthdays WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            ).fetchone()

    def clear_birthday(self, guild_id: int, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM birthdays WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )

    def birthdays_on(self, guild_id: int, month: int, day: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT user_id, month, day, year, set_by_admin FROM birthdays
                WHERE guild_id = ? AND month = ? AND day = ?
                """,
                (guild_id, month, day),
            ).fetchall()

    def all_birthdays(self, guild_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT user_id, month, day, year, set_by_admin
                FROM birthdays WHERE guild_id = ?
                """,
                (guild_id,),
            ).fetchall()

    def was_birthday_announced(self, guild_id: int, user_id: int, year: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM birthday_announcements
                WHERE guild_id = ? AND user_id = ? AND year = ?
                """,
                (guild_id, user_id, year),
            ).fetchone()
        return row is not None

    def mark_birthday_announced(self, guild_id: int, user_id: int, year: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO birthday_announcements (guild_id, user_id, year)
                VALUES (?, ?, ?)
                """,
                (guild_id, user_id, year),
            )

    def get_settings(self, guild_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM guild_settings WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()

    def set_welcome_channel(self, guild_id: int, channel_id: int | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (guild_id, welcome_channel_id)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET welcome_channel_id = excluded.welcome_channel_id
                """,
                (guild_id, channel_id),
            )

    def set_auto_role(self, guild_id: int, role_id: int | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (guild_id, auto_role_id)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET auto_role_id = excluded.auto_role_id
                """,
                (guild_id, role_id),
            )

    def set_birthday_channel(self, guild_id: int, channel_id: int | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (guild_id, birthday_channel_id)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET birthday_channel_id = excluded.birthday_channel_id
                """,
                (guild_id, channel_id),
            )

    def get_birthday_signup_copy(self, guild_id: int) -> dict[str, str | None]:
        settings = self.get_settings(guild_id)
        if not settings:
            return {"title": None, "body": None, "footer": None}
        return {
            "title": settings["birthday_signup_title"],
            "body": settings["birthday_signup_body"],
            "footer": settings["birthday_signup_footer"],
        }

    def set_birthday_signup_copy(
        self,
        guild_id: int,
        *,
        title: str | None,
        body: str | None,
        footer: str | None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (
                    guild_id, birthday_signup_title, birthday_signup_body, birthday_signup_footer
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    birthday_signup_title = excluded.birthday_signup_title,
                    birthday_signup_body = excluded.birthday_signup_body,
                    birthday_signup_footer = excluded.birthday_signup_footer
                """,
                (guild_id, title, body, footer),
            )

    def set_now_playing_panel(
        self, guild_id: int, channel_id: int | None, message_id: int | None
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (
                    guild_id, now_playing_channel_id, now_playing_message_id
                )
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    now_playing_channel_id = excluded.now_playing_channel_id,
                    now_playing_message_id = excluded.now_playing_message_id
                """,
                (guild_id, channel_id, message_id),
            )

    def get_anniversary_copy(self, guild_id: int) -> dict[str, str | None]:
        settings = self.get_settings(guild_id)
        if not settings:
            return {"title": None, "body": None, "footer": None}
        return {
            "title": settings["anniversary_title"],
            "body": settings["anniversary_body"],
            "footer": settings["anniversary_footer"],
        }

    def set_anniversary_copy(
        self,
        guild_id: int,
        *,
        title: str | None,
        body: str | None,
        footer: str | None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (
                    guild_id, anniversary_title, anniversary_body, anniversary_footer
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    anniversary_title = excluded.anniversary_title,
                    anniversary_body = excluded.anniversary_body,
                    anniversary_footer = excluded.anniversary_footer
                """,
                (guild_id, title, body, footer),
            )

    def get_welcome_message(self, guild_id: int) -> str | None:
        settings = self.get_settings(guild_id)
        if not settings:
            return None
        value = settings["welcome_message"]
        return value if value else None

    def set_welcome_message(self, guild_id: int, message: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (guild_id, welcome_message)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    welcome_message = excluded.welcome_message
                """,
                (guild_id, message),
            )

    def was_anniversary_announced(self, guild_id: int, year: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM anniversary_announcements
                WHERE guild_id = ? AND year = ?
                """,
                (guild_id, year),
            ).fetchone()
        return row is not None

    def mark_anniversary_announced(self, guild_id: int, year: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO anniversary_announcements (guild_id, year)
                VALUES (?, ?)
                """,
                (guild_id, year),
            )

    def get_now_playing_panel(self, guild_id: int) -> tuple[int | None, int | None]:
        settings = self.get_settings(guild_id)
        if not settings:
            return None, None
        return settings["now_playing_channel_id"], settings["now_playing_message_id"]

    def get_ow_patch_channel(self, guild_id: int) -> int | None:
        settings = self.get_settings(guild_id)
        if not settings:
            return None
        return settings["ow_patch_channel_id"]

    def set_ow_patch_channel(self, guild_id: int, channel_id: int | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (guild_id, ow_patch_channel_id)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    ow_patch_channel_id = excluded.ow_patch_channel_id
                """,
                (guild_id, channel_id),
            )

    def get_ow_patch_thread_id(self, guild_id: int) -> int | None:
        settings = self.get_settings(guild_id)
        if not settings or not settings["ow_patch_thread_id"]:
            return None
        return int(settings["ow_patch_thread_id"])

    def set_ow_patch_thread_id(self, guild_id: int, thread_id: int | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (guild_id, ow_patch_thread_id)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    ow_patch_thread_id = excluded.ow_patch_thread_id
                """,
                (guild_id, thread_id),
            )

    def get_ow_hero_history_thread_id(self, guild_id: int) -> int | None:
        settings = self.get_settings(guild_id)
        if not settings or not settings["ow_hero_history_thread_id"]:
            return None
        return int(settings["ow_hero_history_thread_id"])

    def set_ow_hero_history_thread_id(
        self, guild_id: int, thread_id: int | None
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (guild_id, ow_hero_history_thread_id)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    ow_hero_history_thread_id = excluded.ow_hero_history_thread_id
                """,
                (guild_id, thread_id),
            )

    def has_hero_alert(self, guild_id: int, user_id: int, hero_key: str) -> bool:
        key = (hero_key or "").strip().lower()
        if not key:
            return False
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM ow_hero_alerts
                WHERE guild_id = ? AND user_id = ? AND hero_key = ?
                """,
                (guild_id, user_id, key),
            ).fetchone()
        return row is not None

    def add_hero_alert(self, guild_id: int, user_id: int, hero_key: str) -> None:
        key = (hero_key or "").strip().lower()
        if not key:
            return
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO ow_hero_alerts (guild_id, user_id, hero_key)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, user_id, hero_key) DO NOTHING
                """,
                (guild_id, user_id, key),
            )

    def remove_hero_alert(self, guild_id: int, user_id: int, hero_key: str) -> None:
        key = (hero_key or "").strip().lower()
        if not key:
            return
        with self.connect() as conn:
            conn.execute(
                """
                DELETE FROM ow_hero_alerts
                WHERE guild_id = ? AND user_id = ? AND hero_key = ?
                """,
                (guild_id, user_id, key),
            )

    def toggle_hero_alert(self, guild_id: int, user_id: int, hero_key: str) -> bool:
        """True when the user is subscribed after this call."""
        if self.has_hero_alert(guild_id, user_id, hero_key):
            self.remove_hero_alert(guild_id, user_id, hero_key)
            return False
        self.add_hero_alert(guild_id, user_id, hero_key)
        return True

    def list_user_hero_alerts(self, guild_id: int, user_id: int) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT hero_key FROM ow_hero_alerts
                WHERE guild_id = ? AND user_id = ?
                ORDER BY hero_key
                """,
                (guild_id, user_id),
            ).fetchall()
        return [str(row["hero_key"]) for row in rows]

    def list_hero_alert_subscribers(self, guild_id: int, hero_key: str) -> list[int]:
        key = (hero_key or "").strip().lower()
        if not key:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT user_id FROM ow_hero_alerts
                WHERE guild_id = ? AND hero_key = ?
                """,
                (guild_id, key),
            ).fetchall()
        return [int(row["user_id"]) for row in rows]

    def was_hero_alert_sent(
        self, guild_id: int, user_id: int, hero_key: str, patch_id: str
    ) -> bool:
        key = (hero_key or "").strip().lower()
        pid = (patch_id or "").strip()
        if not key or not pid:
            return False
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM ow_hero_alert_sent
                WHERE guild_id = ? AND user_id = ? AND hero_key = ?
                  AND patch_id = ?
                """,
                (guild_id, user_id, key, pid),
            ).fetchone()
        return row is not None

    def mark_hero_alert_sent(
        self, guild_id: int, user_id: int, hero_key: str, patch_id: str
    ) -> None:
        key = (hero_key or "").strip().lower()
        pid = (patch_id or "").strip()
        if not key or not pid:
            return
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO ow_hero_alert_sent (
                    guild_id, user_id, hero_key, patch_id
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id, hero_key, patch_id) DO NOTHING
                """,
                (guild_id, user_id, key, pid),
            )

    def was_ow_patch_announced(self, guild_id: int, patch_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM ow_patch_announcements
                WHERE guild_id = ? AND patch_id = ?
                """,
                (guild_id, patch_id),
            ).fetchone()
        return row is not None

    def mark_ow_patch_announced(
        self, guild_id: int, patch_id: str, *, payload: str | None = None
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO ow_patch_announcements (guild_id, patch_id, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, patch_id) DO UPDATE SET
                    payload = COALESCE(excluded.payload, ow_patch_announcements.payload)
                """,
                (guild_id, patch_id, payload),
            )

    def save_ow_patch_live(
        self,
        guild_id: int,
        patch_id: str,
        message_ids: list[int],
        payload: str,
    ) -> None:
        """Mark this patch as the live channel post; clear message ids on older rows."""
        ids_json = json.dumps(message_ids)
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE ow_patch_announcements
                SET message_ids = NULL
                WHERE guild_id = ?
                """,
                (guild_id,),
            )
            conn.execute(
                """
                INSERT INTO ow_patch_announcements (
                    guild_id, patch_id, message_ids, payload, announced_at
                )
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(guild_id, patch_id) DO UPDATE SET
                    message_ids = excluded.message_ids,
                    payload = excluded.payload,
                    announced_at = datetime('now')
                """,
                (guild_id, patch_id, ids_json, payload),
            )

    def get_ow_live_message_ids(self, guild_id: int) -> list[int]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT message_ids FROM ow_patch_announcements
                WHERE guild_id = ? AND message_ids IS NOT NULL
                ORDER BY announced_at DESC
                LIMIT 1
                """,
                (guild_id,),
            ).fetchone()
        if not row or not row["message_ids"]:
            return []
        try:
            raw = json.loads(row["message_ids"])
        except json.JSONDecodeError:
            return []
        return [int(x) for x in raw if str(x).isdigit() or isinstance(x, int)]

    def list_ow_patch_history(self, guild_id: int, *, limit: int = 15) -> list[sqlite3.Row]:
        """Past patches with saved hero balance (excludes the currently live post)."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT patch_id, payload, announced_at
                FROM ow_patch_announcements
                WHERE guild_id = ?
                  AND payload IS NOT NULL
                  AND message_ids IS NULL
                ORDER BY announced_at DESC
                LIMIT ?
                """,
                (guild_id, max(limit * 4, 40)),
            ).fetchall()
        out: list[sqlite3.Row] = []
        for row in rows:
            if _payload_has_hero_balance(row["payload"]):
                out.append(row)
                if len(out) >= limit:
                    break
        return out

    def purge_empty_ow_patches(self) -> int:
        """Delete stored patch notes that have no retail hero balance changes."""
        deleted = 0
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT guild_id, patch_id, payload
                FROM ow_patch_announcements
                WHERE payload IS NOT NULL
                """
            ).fetchall()
            for row in rows:
                if _payload_has_hero_balance(row["payload"]):
                    continue
                conn.execute(
                    """
                    DELETE FROM ow_patch_announcements
                    WHERE guild_id = ? AND patch_id = ?
                    """,
                    (row["guild_id"], row["patch_id"]),
                )
                deleted += 1
        return deleted

    def get_ow_patch_payload(self, guild_id: int, patch_id: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM ow_patch_announcements
                WHERE guild_id = ? AND patch_id = ?
                """,
                (guild_id, patch_id),
            ).fetchone()
        return row["payload"] if row and row["payload"] else None

    def latest_ow_patch_id(self, guild_id: int) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT patch_id FROM ow_patch_announcements
                WHERE guild_id = ?
                ORDER BY announced_at DESC
                LIMIT 1
                """,
                (guild_id,),
            ).fetchone()
        return row["patch_id"] if row else None

    def get_ow_tier_channel(self, guild_id: int) -> int | None:
        settings = self.get_settings(guild_id)
        if not settings:
            return None
        return settings["ow_tier_channel_id"]

    def set_ow_tier_channel(self, guild_id: int, channel_id: int | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (guild_id, ow_tier_channel_id)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    ow_tier_channel_id = excluded.ow_tier_channel_id
                """,
                (guild_id, channel_id),
            )

    def get_ow_tier_thread_id(self, guild_id: int) -> int | None:
        settings = self.get_settings(guild_id)
        if not settings or not settings["ow_tier_thread_id"]:
            return None
        return int(settings["ow_tier_thread_id"])

    def set_ow_tier_thread_id(self, guild_id: int, thread_id: int | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (guild_id, ow_tier_thread_id)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    ow_tier_thread_id = excluded.ow_tier_thread_id
                """,
                (guild_id, thread_id),
            )

    def get_ow_tier_last_posted(self, guild_id: int) -> datetime | None:
        settings = self.get_settings(guild_id)
        if not settings or not settings["ow_tier_last_posted_at"]:
            return None
        raw = settings["ow_tier_last_posted_at"]
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def get_ow_tier_message_ids(self, guild_id: int) -> list[int]:
        settings = self.get_settings(guild_id)
        if not settings or not settings["ow_tier_message_ids"]:
            return []
        try:
            raw = json.loads(settings["ow_tier_message_ids"])
        except json.JSONDecodeError:
            return []
        return [int(x) for x in raw if str(x).isdigit() or isinstance(x, int)]

    def get_ow_tier_last_id(self, guild_id: int) -> str | None:
        settings = self.get_settings(guild_id)
        if not settings:
            return None
        return settings["ow_tier_last_id"]

    def save_ow_tier_live(
        self,
        guild_id: int,
        tier_id: str,
        message_ids: list[int],
    ) -> None:
        ids_json = json.dumps(message_ids)
        stamped = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (
                    guild_id,
                    ow_tier_message_ids,
                    ow_tier_last_posted_at,
                    ow_tier_last_id
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    ow_tier_message_ids = excluded.ow_tier_message_ids,
                    ow_tier_last_posted_at = excluded.ow_tier_last_posted_at,
                    ow_tier_last_id = excluded.ow_tier_last_id
                """,
                (guild_id, ids_json, stamped, tier_id),
            )

    def touch_ow_tier_schedule(self, guild_id: int) -> None:
        """Reset the biweekly timer without posting (e.g. when enabling the channel)."""
        stamped = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (guild_id, ow_tier_last_posted_at)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    ow_tier_last_posted_at = excluded.ow_tier_last_posted_at
                """,
                (guild_id, stamped),
            )

    def get_ow_meta_thread_id(self, guild_id: int) -> int | None:
        settings = self.get_settings(guild_id)
        if not settings or not settings["ow_meta_thread_id"]:
            return None
        return int(settings["ow_meta_thread_id"])

    def set_ow_meta_thread_id(self, guild_id: int, thread_id: int | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (guild_id, ow_meta_thread_id)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    ow_meta_thread_id = excluded.ow_meta_thread_id
                """,
                (guild_id, thread_id),
            )

    def get_ow_meta_last_posted(self, guild_id: int) -> datetime | None:
        settings = self.get_settings(guild_id)
        if not settings or not settings["ow_meta_last_posted_at"]:
            return None
        raw = settings["ow_meta_last_posted_at"]
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def get_ow_meta_message_ids(self, guild_id: int) -> list[int]:
        settings = self.get_settings(guild_id)
        if not settings or not settings["ow_meta_message_ids"]:
            return []
        try:
            raw = json.loads(settings["ow_meta_message_ids"])
        except json.JSONDecodeError:
            return []
        return [int(x) for x in raw if str(x).isdigit() or isinstance(x, int)]

    def get_ow_meta_last_id(self, guild_id: int) -> str | None:
        settings = self.get_settings(guild_id)
        if not settings:
            return None
        return settings["ow_meta_last_id"]

    def save_ow_meta_live(
        self,
        guild_id: int,
        meta_id: str,
        message_ids: list[int],
    ) -> None:
        ids_json = json.dumps(message_ids)
        stamped = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (
                    guild_id,
                    ow_meta_message_ids,
                    ow_meta_last_posted_at,
                    ow_meta_last_id
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    ow_meta_message_ids = excluded.ow_meta_message_ids,
                    ow_meta_last_posted_at = excluded.ow_meta_last_posted_at,
                    ow_meta_last_id = excluded.ow_meta_last_id
                """,
                (guild_id, ids_json, stamped, meta_id),
            )

    def touch_ow_meta_schedule(self, guild_id: int) -> None:
        stamped = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (guild_id, ow_meta_last_posted_at)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    ow_meta_last_posted_at = excluded.ow_meta_last_posted_at
                """,
                (guild_id, stamped),
            )

    def get_ow_news_channel(self, guild_id: int) -> int | None:
        """Resolved destination for news / custom posts (dedicated → patch)."""
        settings = self.get_settings(guild_id)
        if not settings:
            return None
        channel_id = settings["ow_news_channel_id"] or settings["ow_patch_channel_id"]
        if not channel_id:
            return None
        return int(channel_id)

    def get_ow_news_channel_configured(self, guild_id: int) -> int | None:
        """Only the dedicated news/custom forum — no fallback."""
        settings = self.get_settings(guild_id)
        if not settings or not settings["ow_news_channel_id"]:
            return None
        return int(settings["ow_news_channel_id"])

    def set_ow_news_channel(self, guild_id: int, channel_id: int | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (guild_id, ow_news_channel_id)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    ow_news_channel_id = excluded.ow_news_channel_id
                """,
                (guild_id, channel_id),
            )

    def is_ow_news_seeded(self, guild_id: int) -> bool:
        settings = self.get_settings(guild_id)
        if not settings:
            return False
        return bool(settings["ow_news_seeded"])

    def set_ow_news_seeded(self, guild_id: int, seeded: bool = True) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (guild_id, ow_news_seeded)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    ow_news_seeded = excluded.ow_news_seeded
                """,
                (guild_id, 1 if seeded else 0),
            )

    def was_ow_news_posted(self, guild_id: int, bsky_uri: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM ow_news_posts
                WHERE guild_id = ? AND bsky_uri = ?
                """,
                (guild_id, bsky_uri),
            ).fetchone()
        return row is not None

    def mark_ow_news_posted(
        self,
        guild_id: int,
        bsky_uri: str,
        thread_id: int | None = None,
        *,
        auto_close: bool = True,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO ow_news_posts (
                    guild_id, bsky_uri, thread_id, auto_close
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, bsky_uri) DO UPDATE SET
                    thread_id = COALESCE(excluded.thread_id, ow_news_posts.thread_id),
                    auto_close = CASE
                        WHEN ow_news_posts.auto_close = 1 THEN 1
                        ELSE excluded.auto_close
                    END
                """,
                (guild_id, bsky_uri, thread_id, 1 if auto_close else 0),
            )

    def unmark_ow_news_posted(self, guild_id: int, bsky_uri: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                DELETE FROM ow_news_posts
                WHERE guild_id = ? AND bsky_uri = ?
                """,
                (guild_id, bsky_uri),
            )

    def list_ow_news_due_to_close(
        self, *, older_than_hours: int
    ) -> list[sqlite3.Row]:
        """Upcoming-era news posts past the close delay (auto_close=1, not yet closed)."""
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT guild_id, bsky_uri, thread_id, posted_at
                FROM ow_news_posts
                WHERE auto_close = 1
                  AND closed_at IS NULL
                  AND thread_id IS NOT NULL
                  AND datetime(posted_at) <= datetime('now', ?)
                ORDER BY posted_at ASC
                """,
                (f"-{int(older_than_hours)} hours",),
            ).fetchall()

    def mark_ow_news_closed(self, guild_id: int, bsky_uri: str) -> None:
        stamped = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE ow_news_posts
                SET closed_at = ?
                WHERE guild_id = ? AND bsky_uri = ?
                """,
                (stamped, guild_id, bsky_uri),
            )

    def get_onboard_channel(self, guild_id: int) -> int | None:
        settings = self.get_settings(guild_id)
        return int(settings["onboard_channel_id"]) if settings and settings["onboard_channel_id"] else None

    def get_onboard_message_id(self, guild_id: int) -> int | None:
        settings = self.get_settings(guild_id)
        return int(settings["onboard_message_id"]) if settings and settings["onboard_message_id"] else None

    def set_onboard_channel(self, guild_id: int, channel_id: int | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (guild_id, onboard_channel_id)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET onboard_channel_id = excluded.onboard_channel_id
                """,
                (guild_id, channel_id),
            )

    def set_onboard_panel(
        self, guild_id: int, channel_id: int | None, message_id: int | None
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (
                    guild_id, onboard_channel_id, onboard_message_id
                )
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    onboard_channel_id = excluded.onboard_channel_id,
                    onboard_message_id = excluded.onboard_message_id
                """,
                (guild_id, channel_id, message_id),
            )

    def get_onboard_copy(self, guild_id: int) -> dict[str, str | None]:
        settings = self.get_settings(guild_id)
        if not settings:
            return {"title": None, "body": None}
        return {
            "title": settings["onboard_title"],
            "body": settings["onboard_body"],
        }

    def set_onboard_copy(
        self, guild_id: int, *, title: str | None, body: str | None
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (guild_id, onboard_title, onboard_body)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    onboard_title = excluded.onboard_title,
                    onboard_body = excluded.onboard_body
                """,
                (guild_id, title, body),
            )

    def get_ow_broadcast_role(self, guild_id: int) -> int | None:
        settings = self.get_settings(guild_id)
        return (
            int(settings["ow_broadcast_role_id"])
            if settings and settings["ow_broadcast_role_id"]
            else None
        )

    def set_ow_broadcast_role(self, guild_id: int, role_id: int | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (guild_id, ow_broadcast_role_id)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    ow_broadcast_role_id = excluded.ow_broadcast_role_id
                """,
                (guild_id, role_id),
            )

    def get_voice_log_channel(self, guild_id: int) -> int | None:
        settings = self.get_settings(guild_id)
        if not settings or not settings["voice_log_channel_id"]:
            return None
        return int(settings["voice_log_channel_id"])

    def set_voice_log_channel(self, guild_id: int, channel_id: int | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (guild_id, voice_log_channel_id)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    voice_log_channel_id = excluded.voice_log_channel_id
                """,
                (guild_id, channel_id),
            )

    def schedule_voice_log_delete(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int,
        delete_at: datetime,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO voice_log_messages (
                    guild_id, channel_id, message_id, delete_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(channel_id, message_id) DO UPDATE SET
                    delete_at = excluded.delete_at,
                    guild_id = excluded.guild_id
                """,
                (
                    guild_id,
                    channel_id,
                    message_id,
                    delete_at.astimezone(timezone.utc).isoformat(),
                ),
            )

    def list_due_voice_log_deletes(self, now: datetime | None = None) -> list[sqlite3.Row]:
        stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT guild_id, channel_id, message_id, delete_at
                    FROM voice_log_messages
                    WHERE delete_at <= ?
                    ORDER BY delete_at ASC
                    LIMIT 100
                    """,
                    (stamp,),
                ).fetchall()
            )

    def remove_voice_log_message(self, channel_id: int, message_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                DELETE FROM voice_log_messages
                WHERE channel_id = ? AND message_id = ?
                """,
                (channel_id, message_id),
            )

    def guild_stats(self, guild_id: int) -> dict[str, int]:
        with self.connect() as conn:
            birthdays = conn.execute(
                "SELECT COUNT(*) AS n FROM birthdays WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()["n"]
            names = conn.execute(
                "SELECT COUNT(*) AS n FROM real_names WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()["n"]
            announced = conn.execute(
                "SELECT COUNT(*) AS n FROM birthday_announcements WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()["n"]
        return {
            "birthdays": int(birthdays),
            "real_names": int(names),
            "announcements": int(announced),
        }

    def get_play_suggest_copy(self, guild_id: int) -> dict[str, str | None]:
        settings = self.get_settings(guild_id)
        if not settings:
            return {"title": None, "body": None, "footer": None}

        def _get(key: str) -> str | None:
            try:
                value = settings[key]
            except (IndexError, KeyError):
                return None
            return value if value else None

        return {
            "title": _get("play_suggest_title"),
            "body": _get("play_suggest_body"),
            "footer": _get("play_suggest_footer"),
        }

    def set_play_suggest_copy(
        self,
        guild_id: int,
        *,
        title: str | None,
        body: str | None,
        footer: str | None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (
                    guild_id, play_suggest_title, play_suggest_body, play_suggest_footer
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    play_suggest_title = excluded.play_suggest_title,
                    play_suggest_body = excluded.play_suggest_body,
                    play_suggest_footer = excluded.play_suggest_footer
                """,
                (guild_id, title, body, footer),
            )

    def get_hero_emoji_icon(self, emoji_name: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT emoji_name, icon_url, sha256 FROM ow_hero_emoji_icons WHERE emoji_name = ?",
                (emoji_name,),
            ).fetchone()

    def set_hero_emoji_icon(
        self, emoji_name: str, icon_url: str, sha256: str
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO ow_hero_emoji_icons (emoji_name, icon_url, sha256, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(emoji_name) DO UPDATE SET
                    icon_url = excluded.icon_url,
                    sha256 = excluded.sha256,
                    updated_at = excluded.updated_at
                """,
                (emoji_name, icon_url, sha256),
            )

    # --- Play together -------------------------------------------------

    _PLAY_SETTING_COLS = frozenset(
        {
            "play_suggest_channel_id",
            "play_voice_channel_id",
            "play_auto_enabled",
            "play_auto_event",
            "play_auto_expand",
            "play_decay_days",
            "play_detect_days",
            "play_detect_min_people",
            "play_default_hour",
            "play_default_min_players",
            "play_default_max_players",
            "play_cooldown_days",
        }
    )

    _PLAY_SETTING_DEFAULTS: dict[str, int | None] = {
        "play_suggest_channel_id": None,
        "play_voice_channel_id": None,
        "play_auto_enabled": 0,
        "play_auto_event": 1,
        "play_auto_expand": 1,
        "play_decay_days": 30,
        "play_detect_days": 14,
        "play_detect_min_people": 4,
        "play_default_hour": 19,
        "play_default_min_players": 3,
        "play_default_max_players": 6,
        "play_cooldown_days": 7,
    }

    def get_play_settings(self, guild_id: int) -> dict[str, int | None]:
        settings = self.get_settings(guild_id)
        out: dict[str, int | None] = {}
        for key, default in self._PLAY_SETTING_DEFAULTS.items():
            if settings is None:
                out[key] = default
                continue
            try:
                value = settings[key]
            except (IndexError, KeyError):
                out[key] = default
                continue
            if value is None:
                out[key] = default
            else:
                out[key] = int(value)
        return out

    def set_play_setting(self, guild_id: int, column: str, value: int | None) -> None:
        if column not in self._PLAY_SETTING_COLS:
            raise ValueError(f"unknown play setting: {column}")
        with self.connect() as conn:
            conn.execute(
                f"""
                INSERT INTO guild_settings (guild_id, {column})
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET {column} = excluded.{column}
                """,
                (guild_id, value),
            )

    def upsert_play_activity(
        self,
        guild_id: int,
        user_id: int,
        game_key: str,
        game_name: str,
        application_id: int | None,
        seen_at: str,
        *,
        session_gap_hours: int = 4,
    ) -> None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT last_seen, play_count FROM play_activity
                WHERE guild_id = ? AND user_id = ? AND game_key = ?
                """,
                (guild_id, user_id, game_key),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO play_activity (
                        guild_id, user_id, game_key, game_name, application_id,
                        first_seen, last_seen, play_count
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        guild_id,
                        user_id,
                        game_key,
                        game_name,
                        application_id,
                        seen_at,
                        seen_at,
                    ),
                )
            else:
                bump = 0
                try:
                    prev = datetime.fromisoformat(row["last_seen"])
                    now = datetime.fromisoformat(seen_at)
                    if prev.tzinfo is None:
                        prev = prev.replace(tzinfo=timezone.utc)
                    if now.tzinfo is None:
                        now = now.replace(tzinfo=timezone.utc)
                    gap = (now - prev).total_seconds()
                    if gap >= session_gap_hours * 3600:
                        bump = 1
                except (TypeError, ValueError):
                    bump = 1
                conn.execute(
                    """
                    UPDATE play_activity
                    SET game_name = ?,
                        application_id = COALESCE(?, application_id),
                        last_seen = ?,
                        play_count = play_count + ?
                    WHERE guild_id = ? AND user_id = ? AND game_key = ?
                    """,
                    (
                        game_name,
                        application_id,
                        seen_at,
                        bump,
                        guild_id,
                        user_id,
                        game_key,
                    ),
                )
            conn.execute(
                """
                INSERT INTO play_games (guild_id, game_key, game_name)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, game_key) DO UPDATE SET
                    game_name = CASE
                        WHEN play_games.game_name = excluded.game_name THEN play_games.game_name
                        ELSE play_games.game_name
                    END
                """,
                (guild_id, game_key, game_name),
            )

    def purge_old_play_activity(self, guild_id: int, before_iso: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                DELETE FROM play_activity
                WHERE guild_id = ? AND last_seen < ?
                """,
                (guild_id, before_iso),
            )

    def list_play_activity_for_game(
        self, guild_id: int, game_key: str, since_iso: str
    ) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT user_id, game_name, last_seen, play_count, first_seen
                FROM play_activity
                WHERE guild_id = ? AND game_key = ? AND last_seen >= ?
                ORDER BY last_seen DESC
                """,
                (guild_id, game_key, since_iso),
            ).fetchall()

    def list_user_play_activity(
        self, guild_id: int, user_id: int, since_iso: str | None = None
    ) -> list[sqlite3.Row]:
        sql = """
            SELECT game_key, game_name, last_seen, play_count
            FROM play_activity
            WHERE guild_id = ? AND user_id = ?
        """
        params: list[object] = [guild_id, user_id]
        if since_iso:
            sql += " AND last_seen >= ?"
            params.append(since_iso)
        sql += " ORDER BY last_seen DESC"
        with self.connect() as conn:
            return conn.execute(sql, params).fetchall()

    def list_recent_play_games(
        self, guild_id: int, since_iso: str, *, min_people: int = 1
    ) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT
                    a.game_key,
                    MAX(a.game_name) AS game_name,
                    COUNT(*) AS people,
                    MAX(a.last_seen) AS last_seen
                FROM play_activity a
                WHERE a.guild_id = ? AND a.last_seen >= ?
                GROUP BY a.game_key
                HAVING people >= ?
                ORDER BY people DESC, last_seen DESC
                """,
                (guild_id, since_iso, min_people),
            ).fetchall()

    def list_play_activity_recent(
        self, guild_id: int, since_iso: str, *, limit: int = 40
    ) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT user_id, game_key, game_name, last_seen, play_count
                FROM play_activity
                WHERE guild_id = ? AND last_seen >= ?
                ORDER BY last_seen DESC
                LIMIT ?
                """,
                (guild_id, since_iso, limit),
            ).fetchall()

    def count_play_activity(self, guild_id: int, since_iso: str) -> tuple[int, int]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n, COUNT(DISTINCT user_id) AS people
                FROM play_activity
                WHERE guild_id = ? AND last_seen >= ?
                """,
                (guild_id, since_iso),
            ).fetchone()
        return int(row["n"] or 0), int(row["people"] or 0)

    def list_known_play_games(self, guild_id: int, *, limit: int = 500) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT
                    g.game_key,
                    g.game_name,
                    g.enabled,
                    g.blocked,
                    g.min_players,
                    g.max_players,
                    g.steam_url,
                    g.store_note,
                    g.icon_url,
                    g.image_url
                FROM play_games g
                WHERE g.guild_id = ?
                ORDER BY g.blocked ASC, g.enabled DESC, g.game_name COLLATE NOCASE
                LIMIT ?
                """,
                (guild_id, limit),
            ).fetchall()

    def get_play_game(self, guild_id: int, game_key: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT game_key, game_name, enabled, blocked,
                       min_players, max_players, steam_url, store_note,
                       icon_url, image_url
                FROM play_games
                WHERE guild_id = ? AND game_key = ?
                """,
                (guild_id, game_key),
            ).fetchone()

    def upsert_play_game(
        self,
        guild_id: int,
        game_key: str,
        game_name: str,
        *,
        enabled: int | None = None,
        blocked: int | None = None,
        min_players: int | None = None,
        max_players: int | None = None,
        steam_url: str | None = None,
        store_note: str | None = None,
        icon_url: str | None = None,
        image_url: str | None = None,
        set_min: bool = False,
        set_max: bool = False,
        set_steam: bool = False,
        set_note: bool = False,
        set_icon: bool = False,
        set_image: bool = False,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO play_games (guild_id, game_key, game_name)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, game_key) DO UPDATE SET
                    game_name = excluded.game_name,
                    updated_at = datetime('now')
                """,
                (guild_id, game_key, game_name),
            )
            sets: list[str] = []
            params: list[object] = []
            if enabled is not None:
                sets.append("enabled = ?")
                params.append(1 if enabled else 0)
            if blocked is not None:
                sets.append("blocked = ?")
                params.append(1 if blocked else 0)
            if set_min:
                sets.append("min_players = ?")
                params.append(min_players)
            if set_max:
                sets.append("max_players = ?")
                params.append(max_players)
            if set_steam:
                sets.append("steam_url = ?")
                params.append(steam_url)
            if set_note:
                sets.append("store_note = ?")
                params.append(store_note)
            if set_icon:
                sets.append("icon_url = ?")
                params.append(icon_url)
            if set_image:
                sets.append("image_url = ?")
                params.append(image_url)
            if sets:
                params.extend([guild_id, game_key])
                conn.execute(
                    f"""
                    UPDATE play_games SET {', '.join(sets)}, updated_at = datetime('now')
                    WHERE guild_id = ? AND game_key = ?
                    """,
                    params,
                )

    def create_play_suggestion(
        self,
        guild_id: int,
        *,
        game_key: str,
        game_name: str,
        status: str,
        proposed_at: str,
        min_players: int,
        max_players: int,
        steam_url: str | None,
        store_note: str | None,
        created_by: int | None,
        auto_event: int = 1,
        icon_url: str | None = None,
        image_url: str | None = None,
        price_text: str | None = None,
    ) -> int:
        stamped = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO play_suggestions (
                    guild_id, game_key, game_name, status, proposed_at,
                    min_players, max_players, steam_url, store_note,
                    created_by, created_at, auto_event, icon_url, image_url,
                    price_text
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    game_key,
                    game_name,
                    status,
                    proposed_at,
                    min_players,
                    max_players,
                    steam_url,
                    store_note,
                    created_by,
                    stamped,
                    auto_event,
                    icon_url,
                    image_url,
                    price_text,
                ),
            )
            return int(cur.lastrowid)

    def get_play_suggestion(self, suggestion_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM play_suggestions WHERE id = ?",
                (suggestion_id,),
            ).fetchone()

    def get_play_suggestion_by_message(
        self, channel_id: int, message_id: int
    ) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM play_suggestions
                WHERE channel_id = ? AND message_id = ?
                """,
                (channel_id, message_id),
            ).fetchone()

    def update_play_suggestion(self, suggestion_id: int, **fields: object) -> None:
        allowed = {
            "status",
            "proposed_at",
            "min_players",
            "max_players",
            "channel_id",
            "message_id",
            "discord_event_id",
            "steam_url",
            "store_note",
            "icon_url",
            "image_url",
            "price_text",
            "reminder_sent",
            "expansion_sent",
            "auto_event",
            "game_name",
        }
        sets: list[str] = []
        params: list[object] = []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"unknown suggestion field: {key}")
            sets.append(f"{key} = ?")
            params.append(value)
        if not sets:
            return
        params.append(suggestion_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE play_suggestions SET {', '.join(sets)} WHERE id = ?",
                params,
            )

    def list_play_suggestions(
        self,
        guild_id: int,
        *,
        statuses: tuple[str, ...] | None = None,
        game_key: str | None = None,
        limit: int = 20,
    ) -> list[sqlite3.Row]:
        sql = "SELECT * FROM play_suggestions WHERE guild_id = ?"
        params: list[object] = [guild_id]
        if statuses:
            placeholders = ",".join("?" * len(statuses))
            sql += f" AND status IN ({placeholders})"
            params.extend(statuses)
        if game_key:
            sql += " AND game_key = ?"
            params.append(game_key)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            return conn.execute(sql, params).fetchall()

    def latest_play_suggestion_for_game(
        self, guild_id: int, game_key: str
    ) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM play_suggestions
                WHERE guild_id = ? AND game_key = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (guild_id, game_key),
            ).fetchone()

    def list_due_play_reminders(self, now_iso: str, start_iso: str) -> list[sqlite3.Row]:
        """Suggestions whose start is between now and start_iso, reminder not sent."""
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM play_suggestions
                WHERE reminder_sent = 0
                  AND status IN ('published', 'event')
                  AND proposed_at > ?
                  AND proposed_at <= ?
                """,
                (now_iso, start_iso),
            ).fetchall()

    def list_play_suggestions_to_complete(self, cutoff_iso: str) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM play_suggestions
                WHERE status IN ('published', 'event')
                  AND proposed_at <= ?
                """,
                (cutoff_iso,),
            ).fetchall()

    def set_play_rsvp(
        self,
        suggestion_id: int,
        user_id: int,
        status: str,
        source: str,
        updated_at: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO play_rsvps (
                    suggestion_id, user_id, status, source, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(suggestion_id, user_id) DO UPDATE SET
                    status = excluded.status,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (suggestion_id, user_id, status, source, updated_at),
            )

    def get_play_rsvp(
        self, suggestion_id: int, user_id: int
    ) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT status, source FROM play_rsvps
                WHERE suggestion_id = ? AND user_id = ?
                """,
                (suggestion_id, user_id),
            ).fetchone()

    def list_play_rsvps(
        self, suggestion_id: int, *, status: str | None = None
    ) -> list[sqlite3.Row]:
        sql = """
            SELECT user_id, status, source, updated_at
            FROM play_rsvps
            WHERE suggestion_id = ?
        """
        params: list[object] = [suggestion_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY updated_at ASC"
        with self.connect() as conn:
            return conn.execute(sql, params).fetchall()

    def remove_play_rsvp(self, suggestion_id: int, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                DELETE FROM play_rsvps
                WHERE suggestion_id = ? AND user_id = ?
                """,
                (suggestion_id, user_id),
            )

    def add_voice_pair_minutes(
        self,
        guild_id: int,
        user_a: int,
        user_b: int,
        minutes: int,
        seen_at: str,
    ) -> None:
        a, b = (user_a, user_b) if user_a < user_b else (user_b, user_a)
        if a == b:
            return
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO play_voice_pairs (
                    guild_id, user_a, user_b, minutes, last_together
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_a, user_b) DO UPDATE SET
                    minutes = play_voice_pairs.minutes + excluded.minutes,
                    last_together = excluded.last_together
                """,
                (guild_id, a, b, minutes, seen_at),
            )

    def voice_minutes_between(
        self, guild_id: int, user_id: int, other_ids: list[int]
    ) -> dict[int, int]:
        if not other_ids:
            return {}
        others = list({int(x) for x in other_ids if int(x) != user_id})
        if not others:
            return {}
        placeholders = ",".join("?" * len(others))
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT user_a, user_b, minutes
                FROM play_voice_pairs
                WHERE guild_id = ?
                  AND (
                    (user_a = ? AND user_b IN ({placeholders}))
                    OR (user_b = ? AND user_a IN ({placeholders}))
                  )
                """,
                (guild_id, user_id, *others, user_id, *others),
            ).fetchall()
        out: dict[int, int] = {oid: 0 for oid in others}
        for row in rows:
            a, b = int(row["user_a"]), int(row["user_b"])
            other = b if a == user_id else a
            out[other] = int(row["minutes"])
        return out

    def shared_play_session_counts(
        self, guild_id: int, user_id: int, other_ids: list[int]
    ) -> dict[int, int]:
        """How many past suggestions both users confirmed (I'm in / admin)."""
        others = list({int(x) for x in other_ids if int(x) != user_id})
        out = {oid: 0 for oid in others}
        if not others:
            return out
        placeholders = ",".join("?" * len(others))
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT r2.user_id AS other_id, COUNT(*) AS n
                FROM play_rsvps r1
                JOIN play_rsvps r2
                  ON r1.suggestion_id = r2.suggestion_id
                 AND r2.user_id IN ({placeholders})
                JOIN play_suggestions s ON s.id = r1.suggestion_id
                WHERE s.guild_id = ?
                  AND r1.user_id = ?
                  AND r1.status = 'in'
                  AND r2.status = 'in'
                  AND s.status IN ('event', 'completed')
                GROUP BY r2.user_id
                """,
                (*others, guild_id, user_id),
            ).fetchall()
        for row in rows:
            out[int(row["other_id"])] = int(row["n"])
        return out

    def add_play_expansion_invite(
        self, suggestion_id: int, user_id: int, sent_at: str
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO play_expansion_invites (suggestion_id, user_id, sent_at)
                VALUES (?, ?, ?)
                ON CONFLICT(suggestion_id, user_id) DO NOTHING
                """,
                (suggestion_id, user_id, sent_at),
            )

    def list_play_expansion_invites(self, suggestion_id: int) -> set[int]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT user_id FROM play_expansion_invites
                WHERE suggestion_id = ?
                """,
                (suggestion_id,),
            ).fetchall()
        return {int(row["user_id"]) for row in rows}
