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
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO birthdays (guild_id, user_id, month, day, year, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    month = excluded.month,
                    day = excluded.day,
                    year = excluded.year,
                    updated_at = datetime('now')
                """,
                (guild_id, user_id, month, day, year),
            )

    def get_birthday(self, guild_id: int, user_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT month, day, year FROM birthdays WHERE guild_id = ? AND user_id = ?",
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
                SELECT user_id, month, day, year FROM birthdays
                WHERE guild_id = ? AND month = ? AND day = ?
                """,
                (guild_id, month, day),
            ).fetchall()

    def all_birthdays(self, guild_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT user_id, month, day, year FROM birthdays WHERE guild_id = ?",
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
