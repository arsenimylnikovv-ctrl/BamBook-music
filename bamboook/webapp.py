"""Small authenticated HTTP server for the Telegram Mini App."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import subprocess
import tempfile
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from .catalog import search_tracks
from .providers import identify_service
from .storage import Library

WEB_ROOT = Path(__file__).with_name("web")
log = logging.getLogger("bambook.webapp")


def telegram_api(bot_token: str, method: str, parameters: dict) -> dict:
    request = Request(
        f"https://api.telegram.org/bot{bot_token}/{method}",
        data=urlencode(parameters).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise ValueError(result.get("description", "Telegram Bot API запрос завершился ошибкой"))
    return result["result"]


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


def _download_jamendo_cc0(track_id: str) -> tuple[bytes, str, str]:
    """Fetch a Jamendo file only when downloads are enabled and the license is CC0."""
    client_id = os.environ.get("JAMENDO_CLIENT_ID", "").strip()
    if not client_id or not re.fullmatch(r"\d{1,12}", track_id):
        raise ValueError("Полный файл для этого трека недоступен")
    params = urlencode({
        "client_id": client_id, "format": "json", "id": track_id,
        "include": "licenses", "audioformat": "mp32", "audiodlformat": "mp32",
    })
    request = Request(
        f"https://api.jamendo.com/v3.0/tracks/?{params}",
        headers={"User-Agent": "BamBook/0.1"},
    )
    with urlopen(request, timeout=12) as response:
        catalog = json.loads(response.read().decode("utf-8"))
    tracks = catalog.get("results", [])
    if not tracks:
        raise ValueError("Трек не найден в Jamendo")
    track = tracks[0]
    license_url = (track.get("license_ccurl") or "").lower()
    audio_url = track.get("audiodownload", "")
    parsed_audio = urlsplit(audio_url)
    if (
        not track.get("audiodownload_allowed")
        or "creativecommons.org/publicdomain/zero/" not in license_url
        or parsed_audio.scheme != "https"
        or (parsed_audio.hostname or "").lower() != "prod-1.storage.jamendo.com"
    ):
        raise ValueError("У этого трека нет разрешённого скачивания с лицензией CC0")
    audio_request = Request(audio_url, headers={"User-Agent": "BamBook/0.1"})
    with urlopen(audio_request, timeout=45) as response:
        final = urlsplit(response.geturl())
        final_host = (final.hostname or "").lower()
        content_type = response.headers.get_content_type()
        if final.scheme != "https" or not (final_host == "jamendo.com" or final_host.endswith(".jamendo.com")):
            raise ValueError("Jamendo вернул неожиданный адрес файла")
        if not content_type.startswith("audio/") and content_type != "application/octet-stream":
            raise ValueError("Jamendo не вернул аудиофайл")
        audio_data = response.read(20 * 1024 * 1024 + 1)
    if len(audio_data) > 20 * 1024 * 1024:
        raise ValueError("Исходный аудиофайл превышает лимит 20 МБ")
    return audio_data, str(track.get("name", "BamBook track"))[:200], str(track.get("artist_name", "Jamendo"))[:200]


def _send_jamendo_audio(bot_token: str, user_id: int, track_id: str) -> None:
    audio_data, title, artist = _download_jamendo_cc0(track_id)
    with tempfile.TemporaryDirectory(prefix="bambook-send-") as temp_dir:
        input_path = os.path.join(temp_dir, "source.mp3")
        output_path = os.path.join(temp_dir, "bambook-audio.m4a")
        with open(input_path, "wb") as audio_file:
            audio_file.write(audio_data)
        subprocess.run(
            [os.environ.get("FFMPEG_BINARY", "ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", input_path,
             "-vn", "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "128k", "-ar", "44100", "-ac", "2",
             "-movflags", "+faststart", "-metadata", f"title={title}", output_path],
            check=True, capture_output=True, timeout=180,
        )
        with open(output_path, "rb") as audio_file:
            converted = audio_file.read(50 * 1024 * 1024 + 1)
        if len(converted) > 50 * 1024 * 1024:
            raise ValueError("После конвертации файл превышает лимит Telegram 50 МБ")
        boundary = "BamBook" + secrets.token_hex(16)
        parts = []
        for name, value in (("chat_id", str(user_id)), ("title", title), ("performer", artist)):
            parts.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"), b"\r\n",
            ])
        parts.extend([
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="audio"; filename="bambook-audio.m4a"\r\n',
            b"Content-Type: audio/mp4\r\n\r\n", converted, b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ])
        request = Request(
            f"https://api.telegram.org/bot{bot_token}/sendAudio",
            data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urlopen(request, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("ok"):
            raise ValueError(result.get("description", "Telegram не смог отправить аудио"))


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
                if body.get("action") == "play_jamendo":
                    audio_data, _title, _artist = _download_jamendo_cc0(str(body.get("jamendoId", "")))
                    self._respond(200, audio_data, "audio/mpeg")
                    return
                if body.get("action") == "send_jamendo_audio":
                    _send_jamendo_audio(bot_token, user_id, str(body.get("jamendoId", "")))
                    self._json(200, {"sent": True})
                    return
                action = body.get("action")
                library = Library(database_path)
                if action == "search":
                    query = str(body.get("query", "")).strip()
                    if not query or len(query) > 160:
                        self._json(400, {"error": "Enter a search query (up to 160 characters)"})
                        return
                    tracks = search_tracks(query, limit=8)
                    for track in tracks:
                        track.pop("links", None)
                        # Keep Jamendo download URLs private to the bot's audio
                        # delivery path; the Mini App only needs catalog metadata.
                        track.pop("download_url", None)
                        track.pop("audio_url", None)
                    # Spotify-only matches are useful even though playback is
                    # handed off to the Spotify app instead of the Mini App.
                    tracks = [track for track in tracks if (
                        track.get("jamendo_id") or track.get("youtube_id") or track.get("spotify_id")
                    )]
                    self._json(200, {"tracks": tracks})
                elif action == "library":
                    tracks = [
                        {"id": row[0], "title": row[1], "artist": row[2], "album": row[3], "source": row[5], "artwork": row[6], "duration": row[7], "youtube_id": row[9], "spotify_id": row[10], "jamendo_id": row[11]}
                        for row in library.list_tracks(user_id)
                    ]
                    self._json(200, {"tracks": tracks})
                elif action == "playlists":
                    playlists = [
                        {"id": row[0], "name": row[1], "count": row[2]}
                        for row in library.list_playlists(user_id)
                    ]
                    self._json(200, {"playlists": playlists})
                elif action == "playlist_tracks":
                    try:
                        playlist_id = int(body.get("playlistId"))
                    except (ValueError, TypeError):
                        self._json(400, {"error": "Invalid playlist ID"})
                        return
                    if not any(item[0] == playlist_id for item in library.list_playlists(user_id)):
                        self._json(404, {"error": "Плейлист не найден"})
                        return
                    tracks = [
                        {"id": row[0], "title": row[1], "artist": row[2], "album": row[3], "source": row[5], "artwork": row[6], "duration": row[7], "youtube_id": row[9], "spotify_id": row[10], "jamendo_id": row[11]}
                        for row in library.list_playlist_tracks(user_id, playlist_id)
                    ]
                    self._json(200, {"tracks": tracks})
                elif action == "create_playlist":
                    name = str(body.get("name", "")).strip()
                    if not name or len(name) > 60:
                        self._json(400, {"error": "Название плейлиста должно содержать от 1 до 60 символов"})
                        return
                    if any(item[1].casefold() == name.casefold() for item in library.list_playlists(user_id)):
                        self._json(409, {"error": "Плейлист с таким названием уже есть"})
                        return
                    playlist_id = library.create_playlist(user_id, name)
                    self._json(200, {"created": True, "playlistId": playlist_id})
                elif action == "delete_playlist":
                    try:
                        playlist_id = int(body.get("playlistId"))
                    except (ValueError, TypeError):
                        self._json(400, {"error": "Invalid playlist ID"})
                        return
                    self._json(200, {"deleted": library.delete_playlist(user_id, playlist_id)})
                elif action == "add_to_playlist":
                    try:
                        playlist_id = int(body.get("playlistId"))
                    except (ValueError, TypeError):
                        self._json(400, {"error": "Invalid playlist ID"})
                        return
                    track = body.get("track")
                    if not isinstance(track, dict):
                        self._json(400, {"error": "Invalid track"})
                        return
                    url = str(track.get("url", ""))
                    if urlsplit(url).scheme != "https" or not urlsplit(url).netloc:
                        self._json(400, {"error": "Invalid track"})
                        return
                    clean_track = {
                        "title": str(track.get("title", "Track"))[:200],
                        "artist": str(track.get("artist", "Unknown artist"))[:200],
                        "album": str(track.get("album", ""))[:200],
                        "url": url[:1000],
                        "source": str(track.get("source", ""))[:80],
                        "artwork": str(track.get("artwork", ""))[:1000] if urlsplit(str(track.get("artwork", ""))).scheme == "https" else "",
                        "duration": str(track.get("duration", ""))[:12],
                        "jamendo_id": str(track.get("jamendo_id", "")) if re.fullmatch(r"\d{1,12}", str(track.get("jamendo_id", ""))) else "",
                        "youtube_id": str(track.get("youtube_id", "")) if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", str(track.get("youtube_id", ""))) else "",
                        "spotify_id": str(track.get("spotify_id", "")) if re.fullmatch(r"[A-Za-z0-9]{22}", str(track.get("spotify_id", ""))) else "",
                    }
                    saved, added = library.add_to_playlist(user_id, playlist_id, clean_track)
                    self._json(200, {"saved": saved, "added": added})
                elif action == "remove_from_playlist":
                    try:
                        playlist_id = int(body.get("playlistId"))
                        track_id = int(body.get("trackId"))
                    except (ValueError, TypeError):
                        self._json(400, {"error": "Invalid playlist track"})
                        return
                    self._json(200, {"removed": library.remove_from_playlist(user_id, playlist_id, track_id)})
                elif action == "imports":
                    imports = [
                        {"id": row[0], "service": row[1], "url": row[2]}
                        for row in library.list_imports(user_id)
                    ]
                    self._json(200, {"imports": imports})
                elif action == "account":
                    channels = library.list_news_channels(user_id)
                    is_plus = library.is_premium(user_id)
                    self._json(200, {
                        "plan": "plus" if is_plus else "free",
                        "premiumUntil": library.premium_until(user_id),
                        "premiumPriceStars": max(1, min(10000, int(os.environ.get("PREMIUM_PRICE_STARS", "100")))),
                        "newsChannelLimit": None if is_plus else 3,
                        "newsHistoryDays": 90 if is_plus else 7,
                        "newsChannels": [{"key": key, "name": name} for key, name, _chat_id in channels],
                        "imports": [{"id": row[0], "service": row[1]} for row in library.list_imports(user_id)],
                        "newsFeed": [
                            {"channel": row[0], "messageId": row[1], "text": row[2], "publishedAt": row[3], "url": row[4]}
                            for row in library.list_news_feed(user_id, days=90 if is_plus else 7)
                        ],
                    })
                elif action == "add_news_channel":
                    name = str(body.get("channel", ""))
                    clean_name = name.strip().removeprefix("https://t.me/").removeprefix("http://t.me/").removeprefix("t.me/").strip("/@")
                    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", clean_name):
                        self._json(400, {"error": "Укажи публичный канал: @название или t.me/название"})
                        return
                    chat = telegram_api(bot_token, "getChat", {"chat_id": "@" + clean_name})
                    if chat.get("type") != "channel" or not chat.get("username"):
                        self._json(400, {"error": "Нужен публичный Telegram-канал с адресом @username"})
                        return
                    bot_id = int(bot_token.split(":", 1)[0])
                    try:
                        membership = telegram_api(bot_token, "getChatMember", {"chat_id": chat["id"], "user_id": bot_id})
                    except ValueError as error:
                        log.info("Could not verify BamBook membership in a channel (%s)", error)
                        self._json(400, {"error": "Не получилось проверить BamBook в канале. Добавь бота в канал как участника и повтори попытку."})
                        return
                    if membership.get("status") not in {"member", "administrator", "creator"}:
                        self._json(400, {"error": "Сначала добавь BamBook в канал как участника, затем повтори подключение."})
                        return
                    canonical_name = chat["username"]
                    added = library.add_news_channel(user_id, canonical_name, str(chat["id"]))
                    self._json(200, {"added": added, "channel": "@" + canonical_name})
                elif action == "remove_news_channel":
                    key = str(body.get("channelKey", ""))
                    self._json(200, {"removed": library.remove_news_channel(user_id, key)})
                elif action == "premium_invoice":
                    price = max(1, min(10000, int(os.environ.get("PREMIUM_PRICE_STARS", "100"))))
                    payload = f"bambook_plus:{user_id}"
                    invoice_data = urlencode({
                        "title": "BamBook Plus",
                        "description": "Безлимитные новостные Telegram-каналы и архив новостей за 90 дней на 1 месяц.",
                        "payload": payload,
                        "currency": "XTR",
                        "prices": json.dumps([{"label": "BamBook Plus · 1 месяц", "amount": price}], ensure_ascii=False),
                        "subscription_period": 2592000,
                    }).encode()
                    request = Request(
                        f"https://api.telegram.org/bot{bot_token}/createInvoiceLink",
                        data=invoice_data,
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                    with urlopen(request, timeout=20) as response:
                        invoice_result = json.loads(response.read().decode("utf-8"))
                    if not invoice_result.get("ok"):
                        raise ValueError(invoice_result.get("description", "Telegram не смог создать счёт"))
                    self._json(200, {"invoiceLink": invoice_result["result"]})
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
                        "jamendo_id": str(track.get("jamendo_id", "")) if re.fullmatch(r"\d{1,12}", str(track.get("jamendo_id", ""))) else "",
                        "youtube_id": str(track.get("youtube_id", "")) if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", str(track.get("youtube_id", ""))) else "",
                        "spotify_id": str(track.get("spotify_id", "")) if re.fullmatch(r"[A-Za-z0-9]{22}", str(track.get("spotify_id", ""))) else "",
                    }
                    added = library.save_track(user_id, clean_track)
                    self._json(200, {"saved": added})
                elif action == "import":
                    url = str(body.get("url", "")).strip()
                    service = identify_service(url)
                    if not service or urlsplit(url).scheme != "https":
                        self._json(400, {"error": "Пришли HTTPS-ссылку на поддерживаемый музыкальный или аудиокнижный сервис"})
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

