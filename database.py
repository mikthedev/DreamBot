import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def _payload_has_hero_balance(raw: str | None) -> bool:
    """True when a stored patch JSON includes at least one hero change line."""
    if not raw:
        return False
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return False
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
        self.purge_empty_ow_patches()

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
