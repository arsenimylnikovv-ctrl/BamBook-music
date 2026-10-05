"""SQLite persistence for user libraries and imported links."""

from __future__ import annotations

import sqlite3
from pathlib import Path


class Library:
    def __init__(self, database_path: str) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    artist TEXT NOT NULL,
                    album TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT '',
                    artwork TEXT NOT NULL DEFAULT '',
                    duration TEXT NOT NULL DEFAULT '',
                    saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, url)
                );
                CREATE TABLE IF NOT EXISTS imports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    service TEXT NOT NULL,
                    url TEXT NOT NULL,
                    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, url)
                );
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(tracks)")}
            if "artwork" not in columns:
                db.execute("ALTER TABLE tracks ADD COLUMN artwork TEXT NOT NULL DEFAULT ''")
            if "duration" not in columns:
                db.execute("ALTER TABLE tracks ADD COLUMN duration TEXT NOT NULL DEFAULT ''")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def save_track(self, user_id: int, track: dict[str, str]) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO tracks(user_id,title,artist,album,url,source,artwork,duration) VALUES(?,?,?,?,?,?,?,?)",
                (user_id, track["title"], track["artist"], track.get("album", ""), track["url"], track.get("source", ""), track.get("artwork", ""), track.get("duration", "")),
            )
            return cursor.rowcount > 0

    def save_import(self, user_id: int, service: str, url: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO imports(user_id,service,url) VALUES(?,?,?)",
                (user_id, service, url),
            )
            return cursor.rowcount > 0

    def list_tracks(self, user_id: int) -> list[tuple[int, str, str, str, str, str, str, str]]:
        with self._connect() as db:
            return db.execute(
                "SELECT id,title,artist,album,url,source,artwork,duration FROM tracks WHERE user_id=? ORDER BY id DESC",
                (user_id,),
            ).fetchall()

    def list_imports(self, user_id: int) -> list[tuple[int, str, str]]:
        with self._connect() as db:
            return db.execute(
                "SELECT id,service,url FROM imports WHERE user_id=? ORDER BY id DESC",
                (user_id,),
            ).fetchall()

    def remove_track(self, user_id: int, track_id: int) -> bool:
        with self._connect() as db:
            cursor = db.execute("DELETE FROM tracks WHERE user_id=? AND id=?", (user_id, track_id))
            return cursor.rowcount > 0
