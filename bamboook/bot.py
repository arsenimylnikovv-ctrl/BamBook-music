"""Minimal Telegram Bot API long-polling client."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from .catalog import search_tracks
from .providers import identify_service
from .storage import Library
from .webapp import start_webapp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bambook")


def load_env() -> None:
    path = ".env"
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


class Telegram:
    def __init__(self, token: str) -> None:
        self.token = token
        self.base = f"https://api.telegram.org/bot{token}/"

    def call(self, method: str, data: dict) -> dict:
        body = urlencode(data).encode()
        request = Request(self.base + method, data=body)
        with urlopen(request, timeout=40) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("ok"):
            raise RuntimeError(result.get("description", "Telegram API error"))
        return result["result"]

    def send(self, chat_id: int, text: str) -> None:
        self.call("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})

    def download(self, file_id: str) -> bytes:
        info = self.call("getFile", {"file_id": file_id})
        if info.get("file_size", 0) > 20 * 1024 * 1024:
            raise ValueError("Размер исходного файла превышает лимит Telegram Bot API (20 МБ).")
        request = Request(f"https://api.telegram.org/file/bot{self.token}/{info['file_path']}")
        with urlopen(request, timeout=60) as response:
            data = response.read(20 * 1024 * 1024 + 1)
        if len(data) > 20 * 1024 * 1024:
            raise ValueError("Размер исходного файла превышает лимит Telegram Bot API (20 МБ).")
        return data

    def send_audio(self, chat_id: int, path: str, title: str) -> None:
        boundary = "BamBookBoundary7MA4YWxkTrZu0gW"
        if os.path.getsize(path) > 50 * 1024 * 1024:
            raise ValueError("После конвертации файл больше лимита Telegram Bot API (50 МБ).")
        with open(path, "rb") as audio_file:
            audio_data = audio_file.read()
        fields = {"chat_id": str(chat_id), "title": title[:200], "performer": "BamBook"}
        parts = []
        for name, value in fields.items():
            parts.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ])
        parts.extend([
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="audio"; filename="bambook-audio.m4a"\r\n',
            b"Content-Type: audio/mp4\r\n\r\n",
            audio_data,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ])
        request = Request(
            self.base + "sendAudio",
            data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urlopen(request, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("ok"):
            raise RuntimeError(result.get("description", "Telegram audio upload failed"))


def transcode_to_m4a(audio_data: bytes, title: str) -> tuple[tempfile.TemporaryDirectory, str]:
    """Convert a user-provided file to AAC-LC in an M4A container."""
    temp_dir = tempfile.TemporaryDirectory(prefix="bambook-audio-")
    input_path = os.path.join(temp_dir.name, "input-audio")
    output_path = os.path.join(temp_dir.name, "bambook-audio.m4a")
    with open(input_path, "wb") as audio_file:
        audio_file.write(audio_data)
    binary = os.environ.get("FFMPEG_BINARY", "ffmpeg")
    try:
        subprocess.run(
            [binary, "-nostdin", "-v", "error", "-y", "-i", input_path,
             "-vn", "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "128k",
             "-ar", "44100", "-ac", "2", "-movflags", "+faststart",
             "-metadata", f"title={title[:200]}", output_path],
            check=True,
            capture_output=True,
            timeout=180,
        )
        return temp_dir, output_path
    except Exception:
        temp_dir.cleanup()
        raise


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def download_direct_audio(url: str) -> tuple[bytes, str]:
    """Download an audio file only from explicitly trusted HTTPS hosts."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise ValueError("Нужна прямая HTTPS-ссылка на аудиофайл.")
    trusted_hosts = {
        value.strip().lower()
        for value in os.environ.get("TRUSTED_AUDIO_HOSTS", "").split(",")
        if value.strip()
    }
    if host not in trusted_hosts:
        raise ValueError("Этот домен не разрешён для загрузки. В .env добавь его в TRUSTED_AUDIO_HOSTS, только если доверяешь источнику и имеешь право на файл.")
    suffix = os.path.splitext(parsed.path)[1].lower()
    allowed_extensions = {".mp3", ".wav", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".aiff", ".aif"}
    if suffix not in allowed_extensions:
        raise ValueError("Ссылка должна вести прямо на аудиофайл (.mp3, .flac, .ogg, .m4a и др.). Ссылки на страницы Spotify, YouTube Music и Яндекс Музыки не подходят.")
    opener = build_opener(_NoRedirectHandler())
    request = Request(url, headers={"User-Agent": "BamBook/0.1"})
    with opener.open(request, timeout=30) as response:
        content_type = response.headers.get_content_type()
        if not content_type.startswith("audio/") and content_type != "application/octet-stream":
            raise ValueError("Ссылка не вернула аудиофайл.")
        if int(response.headers.get("Content-Length", "0") or 0) > 20 * 1024 * 1024:
            raise ValueError("Исходный файл больше лимита Telegram Bot API (20 МБ).")
        data = response.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("Исходный файл больше лимита Telegram Bot API (20 МБ).")
    return data, os.path.splitext(os.path.basename(parsed.path))[0] or "BamBook audio"


def convert_and_send(telegram: Telegram, chat_id: int, audio_data: bytes, title: str) -> None:
    temp_dir, output_path = transcode_to_m4a(audio_data, title)
    try:
        telegram.send_audio(chat_id, output_path, title)
    finally:
        temp_dir.cleanup()


def format_track(track: tuple, index: int) -> str:
    _id, title, artist, album, url, source, *_metadata = track
    extra = f" · {album}" if album else ""
    source_label = f" [{source}]" if source else ""
    return f"{index}. {title} — {artist}{extra}{source_label}\n{url}"


def main() -> None:
    load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or token == "put-your-token-here":
        raise SystemExit("Укажите TELEGRAM_BOT_TOKEN в .env или переменной окружения.")
    telegram = Telegram(token)
    # Fail deployment immediately for an invalid token instead of reporting
    # healthy while the long-poll loop retries Telegram forever.
    telegram.call("getMe", {})
    database_path = os.environ.get("DATABASE_PATH", "data/bambook.sqlite3")
    library = Library(database_path)
    web_port = int(os.environ.get("PORT", os.environ.get("WEBAPP_PORT", "8080")))
    web_server = start_webapp(
        token,
        database_path,
        host=os.environ.get("WEBAPP_HOST", "0.0.0.0"),
        port=web_port,
    )
    threading.Thread(target=web_server.serve_forever, name="bambook-webapp", daemon=True).start()
    webapp_url = os.environ.get("WEBAPP_URL", "").strip()
    if not webapp_url:
        railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
        if railway_domain:
            webapp_url = f"https://{railway_domain.removeprefix('https://').rstrip('/')}"
    if webapp_url.startswith("https://") and "your-domain.example" not in webapp_url:
        try:
            telegram.call("setChatMenuButton", {
                "menu_button": json.dumps({
                    "type": "web_app",
                    "text": "BamBook",
                    "web_app": {"url": webapp_url},
                }),
            })
        except Exception as error:
            log.warning("Could not set Mini App menu button (%s)", type(error).__name__)
    else:
        log.info("Set WEBAPP_URL to the public HTTPS address to enable the Telegram Mini App button")
    offset = 0
    pending: dict[int, list[dict[str, str]]] = {}
    telegram.call("deleteWebhook", {"drop_pending_updates": "true"})
    log.info("BamBook запущен")

    while True:
        try:
            updates = telegram.call("getUpdates", {"offset": offset, "timeout": 30, "allowed_updates": '["message"]'})
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message", {})
                chat = message.get("chat", {}).get("id")
                user = message.get("from", {}).get("id")
                if not chat or not user:
                    continue
                media = message.get("audio") or message.get("voice")
                document = message.get("document")
                if not media and document and (document.get("mime_type") or "").startswith("audio/"):
                    media = document
                if media:
                    title = media.get("title") or os.path.splitext(media.get("file_name", ""))[0] or "BamBook audio"
                    try:
                        telegram.send(chat, "Конвертирую в AAC-LC (.m4a), скоро отправлю файл…")
                        audio_data = telegram.download(media["file_id"])
                        convert_and_send(telegram, chat, audio_data, title)
                    except FileNotFoundError:
                        telegram.send(chat, "Не найден FFmpeg. Установи его на сервере и перезапусти бота.")
                    except subprocess.CalledProcessError:
                        log.warning("FFmpeg could not transcode an uploaded audio file")
                        telegram.send(chat, "Не получилось прочитать этот аудиофайл. Пришли другой файл.")
                    except (HTTPError, URLError, TimeoutError, ValueError) as error:
                        log.warning("Audio conversion request failed (%s)", type(error).__name__)
                        telegram.send(chat, str(error) if isinstance(error, ValueError) else "Не получилось загрузить или отправить файл. Попробуй позже.")
                    except Exception:
                        log.exception("Audio conversion failed")
                        telegram.send(chat, "Не получилось обработать этот файл. Попробуй другой аудиофайл.")
                    continue
                text = (message.get("text") or "").strip()
                if not text:
                    continue
                command, _, argument = text.partition(" ")
                command = command.split("@", 1)[0].lower()
                try:
                    query = ""
                    audio_url = ""
                    if command in ("/start", "/help"):
                        telegram.send(chat, "Привет! Я BamBook — твоя музыкальная библиотека.\n\nНажми /search и отправь название трека или просто напиши запрос сообщением.\n/audio прямая_ссылка — конвертировать разрешённый аудиофайл в M4A\nОтправь аудиофайл — получить AAC-LC M4A\n/save номер — сохранить результат\n/library — моя коллекция\n/remove номер — удалить трек\n/import ссылка — сохранить ссылку из музыкального сервиса")
                    elif command == "/search":
                        if not argument:
                            telegram.send(chat, "Напиши название трека или исполнителя — например: Kanye West Gold Digger")
                            continue
                        query = argument
                    elif command == "/audio":
                        audio_url = argument
                        if not audio_url:
                            telegram.send(chat, "Пришли команду /audio и прямую HTTPS-ссылку на аудиофайл.")
                            continue
                    elif not command.startswith("/"):
                        if urlsplit(text).scheme:
                            audio_url = text
                        else:
                            query = text
                    else:
                        query = ""

                    if audio_url:
                        telegram.send(chat, "Проверяю прямую ссылку и конвертирую в AAC-LC (.m4a)…")
                        audio_data, title = download_direct_audio(audio_url)
                        convert_and_send(telegram, chat, audio_data, title)
                    elif query:
                        results = search_tracks(query)
                        pending[user] = results
                        if not results:
                            telegram.send(chat, "Не нашёл совпадений в подключённых каталогах. Попробуй уточнить запрос.")
                        else:
                            lines = [f"Нашёл {len(results)} совпадений. Сохрани через /save номер:"]
                            for index, track in enumerate(results, 1):
                                album = f" · {track['album']}" if track.get("album") else ""
                                links = "\n".join(f"{link['source']}: {link['url']}" for link in track.get("links", [{"source": track["source"], "url": track["url"]}]))
                                lines.append(f"{index}. {track['title']} — {track['artist']}{album}\n{links}")
                            telegram.send(chat, "\n\n".join(lines))
                    elif command == "/save":
                        if not argument.isdigit() or not 1 <= int(argument) <= len(pending.get(user, [])):
                            telegram.send(chat, "Сначала найди трек командой /search, затем укажи его номер: /save 1")
                            continue
                        added = library.save_track(user, pending[user][int(argument) - 1])
                        telegram.send(chat, "Сохранил в твою библиотеку 🎵" if added else "Этот трек уже есть в библиотеке.")
                    elif command in ("/library", "/list"):
                        tracks = library.list_tracks(user)
                        if not tracks:
                            telegram.send(chat, "Библиотека пока пуста. Найди трек: /search запрос")
                        else:
                            telegram.send(chat, "Твоя библиотека:\n\n" + "\n\n".join(format_track(row, i) for i, row in enumerate(tracks[:30], 1)))
                    elif command == "/remove":
                        if not argument.isdigit():
                            telegram.send(chat, "Укажи номер трека из /library: /remove 1")
                            continue
                        tracks = library.list_tracks(user)
                        index = int(argument)
                        if not 1 <= index <= len(tracks):
                            telegram.send(chat, "Не нашёл такой номер в библиотеке. Открой /library")
                        else:
                            removed = library.remove_track(user, tracks[index - 1][0])
                            telegram.send(chat, "Удалил из библиотеки." if removed else "Трек уже удалён.")
                    elif command == "/import":
                        service = identify_service(argument) if argument else None
                        if not service:
                            telegram.send(chat, "Пришли ссылку на плейлист или трек Spotify, Яндекс Музыки, YouTube Music, Apple Music или SoundCloud: /import ссылка")
                        else:
                            added = library.save_import(user, service, argument)
                            telegram.send(chat, f"Ссылку {service} сохранил." if added else "Эта ссылка уже сохранена.")
                            telegram.send(chat, "Пока я запомнил ссылку. Чтобы добавить треки из плейлиста автоматически, нужно подключить импорт сервиса.")
                    elif command.startswith("/"):
                        telegram.send(chat, "Не знаю такую команду. Открой /help")
                except (HTTPError, URLError, TimeoutError) as error:
                    log.warning("Request failed (%s)", type(error).__name__)
                    telegram.send(chat, "Сервис временно недоступен. Попробуй ещё раз чуть позже.")
                except ValueError as error:
                    telegram.send(chat, str(error))
                except FileNotFoundError:
                    telegram.send(chat, "Не найден FFmpeg. Установи его на сервере и перезапусти бота.")
                except subprocess.CalledProcessError:
                    telegram.send(chat, "Не получилось прочитать этот аудиофайл. Пришли другой файл.")
                except Exception:
                    log.exception("Message handling failed")
                    telegram.send(chat, "Не получилось обработать запрос. Попробуй ещё раз или напиши /help")
        except (HTTPError, URLError, TimeoutError) as error:
            log.warning("Polling failed: %s", error)
            time.sleep(3)

