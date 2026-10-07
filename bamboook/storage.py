"""SQLite persistence for user libraries, playlists, and imported links."""

from __future__ import annotations

import sqlite3
import re
import time
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
                CREATE TABLE IF NOT EXISTS telegram_audio_cache (
                    source TEXT NOT NULL,
                    source_track_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    performer TEXT NOT NULL,
                    telegram_file_id TEXT NOT NULL,
                    cached_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(source, source_track_id)
                );
                CREATE TABLE IF NOT EXISTS audio_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_audio_jobs_queue ON audio_jobs(status,next_attempt_at,id);
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
                CREATE TABLE IF NOT EXISTS user_subscriptions (
                    user_id INTEGER PRIMARY KEY,
                    premium_until INTEGER NOT NULL DEFAULT 0,
                    latest_charge_id TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS news_channels (
                    user_id INTEGER NOT NULL,
                    channel_key TEXT NOT NULL,
                    channel_name TEXT NOT NULL,
                    chat_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(user_id, channel_key)
                );
                CREATE TABLE IF NOT EXISTS news_posts (
                    chat_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    channel_name TEXT NOT NULL,
                    published_at INTEGER NOT NULL,
                    text TEXT NOT NULL DEFAULT '',
                    post_url TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(chat_id, message_id)
                );
                """
            )
            db.execute("PRAGMA foreign_keys=ON")
            news_columns = {row[1] for row in db.execute("PRAGMA table_info(news_channels)")}
            if "chat_id" not in news_columns:
                db.execute("ALTER TABLE news_channels ADD COLUMN chat_id TEXT NOT NULL DEFAULT ''")
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
            if "jamendo_id" not in columns:
                db.execute("ALTER TABLE tracks ADD COLUMN jamendo_id TEXT NOT NULL DEFAULT ''")
            if "soundcloud_url" not in columns:
                db.execute("ALTER TABLE tracks ADD COLUMN soundcloud_url TEXT NOT NULL DEFAULT ''")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def get_cached_audio(self, source: str, source_track_id: str) -> tuple[str, str, str] | None:
        """Return this bot's reusable Telegram file_id and audio metadata."""
        with self._connect() as db:
            row = db.execute(
                "SELECT telegram_file_id,title,performer FROM telegram_audio_cache WHERE source=? AND source_track_id=?",
                (source[:40], source_track_id[:100]),
            ).fetchone()
        return (str(row[0]), str(row[1]), str(row[2])) if row else None

    def cache_audio(self, source: str, source_track_id: str, title: str, performer: str, file_id: str) -> None:
        if not source or not source_track_id or not file_id:
            raise ValueError("Audio cache requires a source, track ID, and Telegram file_id")
        with self._connect() as db:
            db.execute(
                "INSERT INTO telegram_audio_cache(source,source_track_id,title,performer,telegram_file_id) VALUES(?,?,?,?,?) "
                "ON CONFLICT(source,source_track_id) DO UPDATE SET title=excluded.title,performer=excluded.performer,telegram_file_id=excluded.telegram_file_id,cached_at=CURRENT_TIMESTAMP",
                (source[:40], source_track_id[:100], title[:200], performer[:200], file_id[:1024]),
            )

    def remove_cached_audio(self, source: str, source_track_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "DELETE FROM telegram_audio_cache WHERE source=? AND source_track_id=?",
                (source[:40], source_track_id[:100]),
            )

    def enqueue_audio_job(self, chat_id: int, payload: str) -> int:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO audio_jobs(chat_id,payload) VALUES(?,?)",
                (chat_id, payload),
            )
            return int(cursor.lastrowid)

    def recover_interrupted_audio_jobs(self) -> None:
        """Return jobs claimed by a previous process to the durable queue."""
        with self._connect() as db:
            db.execute(
                "UPDATE audio_jobs SET status='queued',next_attempt_at=0,updated_at=CURRENT_TIMESTAMP "
                "WHERE status='processing'"
            )

    def prune_audio_jobs(self, retention_days: int = 30) -> None:
        with self._connect() as db:
            db.execute(
                "DELETE FROM audio_jobs WHERE status IN ('done','failed') "
                "AND created_at < datetime('now', ?)",
                (f"-{max(1, min(365, retention_days))} days",),
            )

    def claim_audio_job(self) -> tuple[int, int, str, int] | None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id,chat_id,payload,attempts FROM audio_jobs "
                "WHERE status='queued' AND next_attempt_at<=? ORDER BY id LIMIT 1",
                (time.time(),),
            ).fetchone()
            if not row:
                db.commit()
                return None
            job_id, chat_id, payload, attempts = row
            attempts += 1
            db.execute(
                "UPDATE audio_jobs SET status='processing',attempts=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (attempts, job_id),
            )
            db.commit()
            return int(job_id), int(chat_id), str(payload), int(attempts)

    def finish_audio_job(self, job_id: int) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE audio_jobs SET status='done',last_error='',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (job_id,),
            )

    def retry_audio_job(self, job_id: int, attempts: int, error_name: str, max_attempts: int = 3) -> bool:
        retry = attempts < max_attempts
        delay = min(300, 20 * (2 ** max(0, attempts - 1)))
        with self._connect() as db:
            db.execute(
                "UPDATE audio_jobs SET status=?,next_attempt_at=?,last_error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                ("queued" if retry else "failed", time.time() + delay if retry else 0, error_name[:120], job_id),
            )
        return retry

    def is_premium(self, user_id: int, now: int | None = None) -> bool:
        current = int(time.time()) if now is None else now
        with self._connect() as db:
            row = db.execute(
                "SELECT premium_until FROM user_subscriptions WHERE user_id=?",
                (user_id,),
            ).fetchone()
            return bool(row and int(row[0]) > current)

    def premium_until(self, user_id: int) -> int:
        with self._connect() as db:
            row = db.execute(
                "SELECT premium_until FROM user_subscriptions WHERE user_id=?",
                (user_id,),
            ).fetchone()
            return int(row[0]) if row else 0

    def activate_premium(self, user_id: int, premium_until: int, charge_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO user_subscriptions(user_id,premium_until,latest_charge_id) VALUES(?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET premium_until=MAX(user_subscriptions.premium_until,excluded.premium_until), "
                "latest_charge_id=excluded.latest_charge_id,updated_at=CURRENT_TIMESTAMP",
                (user_id, premium_until, charge_id[:255]),
            )

    def list_news_channels(self, user_id: int) -> list[tuple[str, str, str]]:
        with self._connect() as db:
            return db.execute(
                "SELECT channel_key,channel_name,chat_id FROM news_channels WHERE user_id=? ORDER BY created_at,channel_key",
                (user_id,),
            ).fetchall()

    def add_news_channel(self, user_id: int, channel_name: str, chat_id: str = "") -> bool:
        name = channel_name.strip().removeprefix("https://t.me/").removeprefix("t.me/").strip("/@")
        if not name or not re.fullmatch(r"[A-Za-z0-9_]{5,32}", name):
            raise ValueError("Укажи публичный Telegram-канал, например @bbcnews или t.me/bbcnews")
        channel_key = name.casefold()
        with self._connect() as db:
            row = db.execute(
                "SELECT premium_until FROM user_subscriptions WHERE user_id=?",
                (user_id,),
            ).fetchone()
            is_premium = bool(row and int(row[0]) > int(time.time()))
            existing = db.execute(
                "SELECT 1 FROM news_channels WHERE user_id=? AND channel_key=?",
                (user_id, channel_key),
            ).fetchone()
            if existing:
                return False
            count = db.execute(
                "SELECT COUNT(*) FROM news_channels WHERE user_id=?", (user_id,)
            ).fetchone()[0]
            if not is_premium and count >= 3:
                raise ValueError("В бесплатном плане можно добавить до 3 Telegram-каналов. BamBook Plus снимет это ограничение.")
            db.execute(
                "INSERT INTO news_channels(user_id,channel_key,channel_name,chat_id) VALUES(?,?,?,?)",
                (user_id, channel_key, "@" + name, str(chat_id)),
            )
            return True

    def save_news_post(self, post: dict) -> None:
        chat = post.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        message_id = int(post.get("message_id", 0))
        if not chat_id or not message_id:
            return
        channel_name = "@" + str(chat.get("username") or chat.get("title") or "Telegram")
        post_url = f"https://t.me/{chat['username']}/{message_id}" if chat.get("username") else ""
        content = str(post.get("text") or post.get("caption") or "").strip()
        if not content and post.get("photo"):
            content = "Фото"
        if not content and post.get("video"):
            content = "Видео"
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO news_posts(chat_id,message_id,channel_name,published_at,text,post_url) VALUES(?,?,?,?,?,?)",
                (chat_id, message_id, channel_name[:200], int(post.get("date", time.time())), content[:6000], post_url[:1000]),
            )

    def list_news_feed(self, user_id: int, limit: int = 40, days: int | None = None) -> list[tuple[str, int, str, int, str, str]]:
        with self._connect() as db:
            cutoff = int(time.time()) - days * 86400 if days is not None else 0
            return db.execute(
                "SELECT n.channel_name,n.message_id,n.text,n.published_at,n.post_url,n.chat_id "
                "FROM news_posts n JOIN news_channels c ON c.chat_id=n.chat_id "
                "WHERE c.user_id=? AND n.published_at>=? ORDER BY n.published_at DESC,n.message_id DESC LIMIT ?",
                (user_id, cutoff, max(1, min(100, limit))),
            ).fetchall()

    def remove_news_channel(self, user_id: int, channel_key: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "DELETE FROM news_channels WHERE user_id=? AND channel_key=?",
                (user_id, channel_key.casefold()),
            )
            return cursor.rowcount > 0

    def save_track(self, user_id: int, track: dict[str, str]) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO tracks(user_id,title,artist,album,url,source,artwork,duration,preview_url,youtube_id,spotify_id,jamendo_id,soundcloud_url) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (user_id, track["title"], track["artist"], track.get("album", ""), track["url"], track.get("source", ""), track.get("artwork", ""), track.get("duration", ""), track.get("preview_url", ""), track.get("youtube_id", ""), track.get("spotify_id", ""), track.get("jamendo_id", ""), track.get("soundcloud_url", "")),
            )
            db.execute(
                "UPDATE tracks SET preview_url=CASE WHEN preview_url='' THEN ? ELSE preview_url END, "
                "youtube_id=CASE WHEN youtube_id='' THEN ? ELSE youtube_id END, "
                "spotify_id=CASE WHEN spotify_id='' THEN ? ELSE spotify_id END, "
                "jamendo_id=CASE WHEN jamendo_id='' THEN ? ELSE jamendo_id END, "
                "soundcloud_url=CASE WHEN soundcloud_url='' THEN ? ELSE soundcloud_url END "
                "WHERE user_id=? AND url=?",
                (track.get("preview_url", ""), track.get("youtube_id", ""), track.get("spotify_id", ""), track.get("jamendo_id", ""), track.get("soundcloud_url", ""), user_id, track["url"]),
            )
            return cursor.rowcount > 0

    def save_import(self, user_id: int, service: str, url: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO imports(user_id,service,url) VALUES(?,?,?)",
                (user_id, service, url),
            )
            return cursor.rowcount > 0

    def list_tracks(self, user_id: int) -> list[tuple]:
        with self._connect() as db:
            return db.execute(
                "SELECT id,title,artist,album,url,source,artwork,duration,preview_url,youtube_id,spotify_id,jamendo_id,soundcloud_url FROM tracks WHERE user_id=? ORDER BY id DESC",
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
                "INSERT OR IGNORE INTO tracks(user_id,title,artist,album,url,source,artwork,duration,preview_url,youtube_id,spotify_id,jamendo_id,soundcloud_url) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (user_id, track["title"], track["artist"], track.get("album", ""), track["url"], track.get("source", ""), track.get("artwork", ""), track.get("duration", ""), track.get("preview_url", ""), track.get("youtube_id", ""), track.get("spotify_id", ""), track.get("jamendo_id", ""), track.get("soundcloud_url", "")),
            )
            db.execute(
                "UPDATE tracks SET preview_url=CASE WHEN preview_url='' THEN ? ELSE preview_url END, "
                "youtube_id=CASE WHEN youtube_id='' THEN ? ELSE youtube_id END, "
                "spotify_id=CASE WHEN spotify_id='' THEN ? ELSE spotify_id END, "
                "jamendo_id=CASE WHEN jamendo_id='' THEN ? ELSE jamendo_id END, "
                "soundcloud_url=CASE WHEN soundcloud_url='' THEN ? ELSE soundcloud_url END "
                "WHERE user_id=? AND url=?",
                (track.get("preview_url", ""), track.get("youtube_id", ""), track.get("spotify_id", ""), track.get("jamendo_id", ""), track.get("soundcloud_url", ""), user_id, track["url"]),
            )
            saved = db.execute("SELECT id FROM tracks WHERE user_id=? AND url=?", (user_id, track["url"])).fetchone()
            cursor = db.execute("INSERT OR IGNORE INTO playlist_tracks(playlist_id,track_id) VALUES(?,?)", (playlist_id, saved[0]))
            return saved is not None, cursor.rowcount > 0

    def list_playlist_tracks(self, user_id: int, playlist_id: int) -> list[tuple]:
        with self._connect() as db:
            return db.execute(
                "SELECT t.id,t.title,t.artist,t.album,t.url,t.source,t.artwork,t.duration,t.preview_url,t.youtube_id,t.spotify_id,t.jamendo_id,t.soundcloud_url "
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

