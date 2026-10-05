"""Small authenticated HTTP server for the Telegram Mini App."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from .catalog import search_tracks
from .providers import identify_service
from .storage import Library

WEB_ROOT = Path(__file__).with_name("web")
log = logging.getLogger("bambook.webapp")


def validate_init_data(init_data: str, bot_token: str) -> int | None:
    """Verify Telegram's signed initData and return its user ID."""
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    fields = dict(pairs)
    if len(fields) != len(pairs) or "hash" not in fields or "user" not in fields:
        return None
    supplied_hash = fields.pop("hash")
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated_hash, supplied_hash):
        return None
    try:
        auth_date = int(fields["auth_date"])
        if auth_date > time.time() + 60 or time.time() - auth_date > 86400:
            return None
        user = json.loads(fields["user"])
        user_id = int(user["id"])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return user_id


def make_handler(bot_token: str, database_path: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "BamBook/0.1"

        def log_message(self, format_string: str, *args) -> None:
            log.info("Mini App HTTP request: %s", format_string % args)

        def _respond(self, status: int, payload: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _json(self, status: int, payload: dict) -> None:
            self._respond(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            assets = {
                "/": (WEB_ROOT / "index.html", "text/html; charset=utf-8"),
                "/app.js": (WEB_ROOT / "app.js", "text/javascript; charset=utf-8"),
                "/style.css": (WEB_ROOT / "style.css", "text/css; charset=utf-8"),
            }
            if path == "/health":
                self._json(200, {"ok": True})
                return
            if path not in assets:
                self._json(404, {"error": "Not found"})
                return
            asset_path, content_type = assets[path]
            self._respond(200, asset_path.read_bytes(), content_type)

        def do_POST(self) -> None:
            if self.path != "/api/action":
                self._json(404, {"error": "Not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 64 * 1024:
                    self._json(413, {"error": "Request is too large"})
                    return
                body = json.loads(self.rfile.read(length))
                user_id = validate_init_data(str(body.get("initData", "")), bot_token)
                if user_id is None:
                    self._json(401, {"error": "Open BamBook from Telegram to continue"})
                    return
                action = body.get("action")
                library = Library(database_path)
                if action == "search":
                    query = str(body.get("query", "")).strip()
                    if not query or len(query) > 160:
                        self._json(400, {"error": "Enter a search query (up to 160 characters)"})
                        return
                    self._json(200, {"tracks": search_tracks(query, limit=8)})
                elif action == "library":
                    tracks = [
                        {"id": row[0], "title": row[1], "artist": row[2], "album": row[3], "url": row[4], "source": row[5], "artwork": row[6], "duration": row[7]}
                        for row in library.list_tracks(user_id)
                    ]
                    self._json(200, {"tracks": tracks})
                elif action == "imports":
                    imports = [
                        {"id": row[0], "service": row[1], "url": row[2]}
                        for row in library.list_imports(user_id)
                    ]
                    self._json(200, {"imports": imports})
                elif action == "save":
                    track = body.get("track")
                    if not isinstance(track, dict):
                        self._json(400, {"error": "Invalid track"})
                        return
                    url = str(track.get("url", ""))
                    if urlsplit(url).scheme != "https" or not urlsplit(url).netloc:
                        self._json(400, {"error": "Invalid track link"})
                        return
                    clean_track = {
                        "title": str(track.get("title", "Track"))[:200],
                        "artist": str(track.get("artist", "Unknown artist"))[:200],
                        "album": str(track.get("album", ""))[:200],
                        "url": url[:1000],
                        "source": str(track.get("source", ""))[:80],
                        "artwork": str(track.get("artwork", ""))[:1000] if urlsplit(str(track.get("artwork", ""))).scheme == "https" else "",
                        "duration": str(track.get("duration", ""))[:12],
                    }
                    added = library.save_track(user_id, clean_track)
                    self._json(200, {"saved": added})
                elif action == "import":
                    url = str(body.get("url", "")).strip()
                    service = identify_service(url)
                    if not service or urlsplit(url).scheme != "https":
                        self._json(400, {"error": "Пришли HTTPS-ссылку Spotify, Яндекс Музыки, YouTube Music, Apple Music или SoundCloud"})
                        return
                    added = library.save_import(user_id, service, url[:1000])
                    self._json(200, {"saved": added, "service": service})
                elif action == "remove":
                    try:
                        track_id = int(body.get("trackId"))
                    except (ValueError, TypeError):
                        self._json(400, {"error": "Invalid track ID"})
                        return
                    self._json(200, {"removed": library.remove_track(user_id, track_id)})
                else:
                    self._json(400, {"error": "Unknown action"})
            except (ValueError, json.JSONDecodeError):
                self._json(400, {"error": "Invalid request"})
            except Exception:
                log.exception("Mini App API request failed")
                self._json(500, {"error": "Something went wrong. Try again."})

    return Handler


def start_webapp(bot_token: str, database_path: str, host: str = "0.0.0.0", port: int = 8080) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(bot_token, database_path))
    server.daemon_threads = True
    return server
