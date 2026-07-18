import sqlite3
from contextlib import contextmanager
from pathlib import Path


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
