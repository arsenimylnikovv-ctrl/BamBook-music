"""SQLite persistence for user libraries, playlists, and imported links."""

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
                CREATE TABLE IF NOT EXISTS playlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, name)
                );
                CREATE TABLE IF NOT EXISTS playlist_tracks (
                    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
                    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
                    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(playlist_id, track_id)
                );
                """
            )
            db.execute("PRAGMA foreign_keys=ON")
            columns = {row[1] for row in db.execute("PRAGMA table_info(tracks)")}
            if "artwork" not in columns:
                db.execute("ALTER TABLE tracks ADD COLUMN artwork TEXT NOT NULL DEFAULT ''")
            if "duration" not in columns:
                db.execute("ALTER TABLE tracks ADD COLUMN duration TEXT NOT NULL DEFAULT ''")
            if "preview_url" not in columns:
                db.execute("ALTER TABLE tracks ADD COLUMN preview_url TEXT NOT NULL DEFAULT ''")
            if "youtube_id" not in columns:
                db.execute("ALTER TABLE tracks ADD COLUMN youtube_id TEXT NOT NULL DEFAULT ''")
            if "spotify_id" not in columns:
                db.execute("ALTER TABLE tracks ADD COLUMN spotify_id TEXT NOT NULL DEFAULT ''")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def save_track(self, user_id: int, track: dict[str, str]) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO tracks(user_id,title,artist,album,url,source,artwork,duration,preview_url,youtube_id,spotify_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (user_id, track["title"], track["artist"], track.get("album", ""), track["url"], track.get("source", ""), track.get("artwork", ""), track.get("duration", ""), track.get("preview_url", ""), track.get("youtube_id", ""), track.get("spotify_id", "")),
            )
            db.execute(
                "UPDATE tracks SET preview_url=CASE WHEN preview_url='' THEN ? ELSE preview_url END, "
                "youtube_id=CASE WHEN youtube_id='' THEN ? ELSE youtube_id END, "
                "spotify_id=CASE WHEN spotify_id='' THEN ? ELSE spotify_id END "
                "WHERE user_id=? AND url=?",
                (track.get("preview_url", ""), track.get("youtube_id", ""), track.get("spotify_id", ""), user_id, track["url"]),
            )
            return cursor.rowcount > 0

    def save_import(self, user_id: int, service: str, url: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO imports(user_id,service,url) VALUES(?,?,?)",
                (user_id, service, url),
            )
            return cursor.rowcount > 0

    def list_tracks(self, user_id: int) -> list[tuple[int, str, str, str, str, str, str, str, str, str, str]]:
        with self._connect() as db:
            return db.execute(
                "SELECT id,title,artist,album,url,source,artwork,duration,preview_url,youtube_id,spotify_id FROM tracks WHERE user_id=? ORDER BY id DESC",
                (user_id,),
            ).fetchall()

    def create_playlist(self, user_id: int, name: str) -> int:
        with self._connect() as db:
            cursor = db.execute("INSERT INTO playlists(user_id,name) VALUES(?,?)", (user_id, name))
            return int(cursor.lastrowid)

    def list_playlists(self, user_id: int) -> list[tuple[int, str, int]]:
        with self._connect() as db:
            return db.execute(
                "SELECT p.id,p.name,COUNT(pt.track_id) FROM playlists p "
                "LEFT JOIN playlist_tracks pt ON pt.playlist_id=p.id "
                "WHERE p.user_id=? GROUP BY p.id ORDER BY p.id DESC",
                (user_id,),
            ).fetchall()

    def add_to_playlist(self, user_id: int, playlist_id: int, track: dict[str, str]) -> tuple[bool, bool]:
        """Save a searched track if needed, then add it to one of this user's playlists."""
        with self._connect() as db:
            playlist = db.execute("SELECT id FROM playlists WHERE id=? AND user_id=?", (playlist_id, user_id)).fetchone()
            if not playlist:
                raise ValueError("Плейлист не найден.")
            db.execute(
                "INSERT OR IGNORE INTO tracks(user_id,title,artist,album,url,source,artwork,duration,preview_url,youtube_id,spotify_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (user_id, track["title"], track["artist"], track.get("album", ""), track["url"], track.get("source", ""), track.get("artwork", ""), track.get("duration", ""), track.get("preview_url", ""), track.get("youtube_id", ""), track.get("spotify_id", "")),
            )
            db.execute(
                "UPDATE tracks SET preview_url=CASE WHEN preview_url='' THEN ? ELSE preview_url END, "
                "youtube_id=CASE WHEN youtube_id='' THEN ? ELSE youtube_id END, "
                "spotify_id=CASE WHEN spotify_id='' THEN ? ELSE spotify_id END "
                "WHERE user_id=? AND url=?",
                (track.get("preview_url", ""), track.get("youtube_id", ""), track.get("spotify_id", ""), user_id, track["url"]),
            )
            saved = db.execute("SELECT id FROM tracks WHERE user_id=? AND url=?", (user_id, track["url"])).fetchone()
            cursor = db.execute("INSERT OR IGNORE INTO playlist_tracks(playlist_id,track_id) VALUES(?,?)", (playlist_id, saved[0]))
            return saved is not None, cursor.rowcount > 0

    def list_playlist_tracks(self, user_id: int, playlist_id: int) -> list[tuple[int, str, str, str, str, str, str, str, str, str, str]]:
        with self._connect() as db:
            return db.execute(
                "SELECT t.id,t.title,t.artist,t.album,t.url,t.source,t.artwork,t.duration,t.preview_url,t.youtube_id,t.spotify_id "
                "FROM playlist_tracks pt JOIN tracks t ON t.id=pt.track_id "
                "JOIN playlists p ON p.id=pt.playlist_id WHERE p.user_id=? AND p.id=? ORDER BY pt.added_at,pt.rowid",
                (user_id, playlist_id),
            ).fetchall()

    def remove_from_playlist(self, user_id: int, playlist_id: int, track_id: int) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "DELETE FROM playlist_tracks WHERE playlist_id=? AND track_id=? "
                "AND playlist_id IN (SELECT id FROM playlists WHERE user_id=?)",
                (playlist_id, track_id, user_id),
            )
            return cursor.rowcount > 0

    def delete_playlist(self, user_id: int, playlist_id: int) -> bool:
        with self._connect() as db:
            db.execute("DELETE FROM playlist_tracks WHERE playlist_id=? AND playlist_id IN (SELECT id FROM playlists WHERE user_id=?)", (playlist_id, user_id))
            cursor = db.execute("DELETE FROM playlists WHERE id=? AND user_id=?", (playlist_id, user_id))
            return cursor.rowcount > 0

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

