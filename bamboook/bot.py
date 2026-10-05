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

    def send_audio(self, chat_id: int, path: str, title: str, performer: str = "BamBook") -> None:
        boundary = "BamBookBoundary7MA4YWxkTrZu0gW"
        if os.path.getsize(path) > 50 * 1024 * 1024:
            raise ValueError("После конвертации файл больше лимита Telegram Bot API (50 МБ).")
        with open(path, "rb") as audio_file:
            audio_data = audio_file.read()
        fields = {"chat_id": str(chat_id), "title": title[:200], "performer": performer[:200]}
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


def download_apple_preview(url: str) -> bytes:
    """Fetch only Apple's catalog preview clips, not full streaming tracks."""
    allowed_suffixes = (".itunes.apple.com", ".mzstatic.com")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.lower().endswith(allowed_suffixes):
        raise ValueError("Apple returned an unexpected preview host")
    request = Request(url, headers={"User-Agent": "BamBook/0.1"})
    with urlopen(request, timeout=30) as response:
        final_host = (urlsplit(response.geturl()).hostname or "").lower()
        if not final_host.endswith(allowed_suffixes):
            raise ValueError("Apple preview redirected to an unexpected host")
        content_type = response.headers.get_content_type()
        if not content_type.startswith("audio/") and content_type != "application/octet-stream":
            raise ValueError("Apple preview URL did not return audio")
        data = response.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("Apple preview is larger than Telegram's 20 MB download limit")
    return data


def convert_and_send(telegram: Telegram, chat_id: int, audio_data: bytes, title: str, performer: str = "BamBook") -> None:
    temp_dir, output_path = transcode_to_m4a(audio_data, title)
    try:
        telegram.send_audio(chat_id, output_path, title, performer)
    finally:
        temp_dir.cleanup()


def format_track(track: tuple, index: int) -> str:
    _id, title, artist, album, *_metadata = track
    extra = f" · {album}" if album else ""
    return f"{index}. {title} — {artist}{extra}"


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
                        telegram.send(chat, "Привет! Я BamBook — твоя музыкальная библиотека.\n\nНажми /search и отправь запрос или просто напиши название. Доступные аудиопревью пришлю прямо сюда.\n/audio прямая_ссылка — конвертировать разрешённый файл в M4A\nОтправь аудиофайл — получить AAC-LC M4A\n/save номер — сохранить результат поиска\n/library — моя коллекция\n/remove номер — удалить трек\n/playlists — мои плейлисты\n/playlist_new название — создать плейлист\n/playlist_add номер_плейлиста номер_трека — добавить трек из библиотеки\n/playlist_show номер — показать треки\n/playlist_remove номер_плейлиста номер_трека — убрать трек\n/playlist_delete номер — удалить плейлист")
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
                            previews = [(index, track) for index, track in enumerate(results, 1) if track.get("preview_url")][:3]
                            if previews:
                                telegram.send(chat, f"Нашёл {len(results)} совпадений. Вот официальные аудиофрагменты из каталога Apple Music:")
                                for index, track in previews:
                                    try:
                                        telegram.send(chat, f"{index}. {track['title']} — {track['artist']}")
                                        audio_data = download_apple_preview(track["preview_url"])
                                        convert_and_send(telegram, chat, audio_data, track["title"], track["artist"])
                                    except Exception as error:
                                        log.warning("Could not send Apple preview (%s)", type(error).__name__)
                                telegram.send(chat, "Чтобы сохранить трек, отправь /save с его номером выше. Превью — короткий фрагмент, каталог не выдаёт полные записи.")
                            else:
                                top = "\n".join(
                                    f"{index}. {track['title']} — {track['artist']}"
                                    for index, track in enumerate(results[:5], 1)
                                )
                                telegram.send(chat, "Нашёл совпадения, но каталоги не предоставили для них аудиопревью.\n\n" + top + "\n\nПолную запись нельзя получить из обычной ссылки Spotify/YouTube/Apple. Пришли боту свой аудиофайл — я перекодирую его в AAC-LC M4A.")
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
                    elif command == "/playlists":
                        playlists = library.list_playlists(user)
                        if not playlists:
                            telegram.send(chat, "Плейлистов пока нет. Создай первый: /playlist_new Грустные песни")
                        else:
                            lines = [f"{index}. {name} — {count} трек(ов)" for index, (_playlist_id, name, count) in enumerate(playlists, 1)]
                            telegram.send(chat, "Твои плейлисты:\n\n" + "\n".join(lines) + "\n\nСоздать: /playlist_new название")
                    elif command in ("/playlist_new", "/playlist_create"):
                        name = argument.strip()
                        if not name or len(name) > 60:
                            telegram.send(chat, "Напиши название длиной до 60 символов: /playlist_new Грустные песни")
                            continue
                        if any(item[1].casefold() == name.casefold() for item in library.list_playlists(user)):
                            telegram.send(chat, "Плейлист с таким названием уже существует.")
                            continue
                        library.create_playlist(user, name)
                        telegram.send(chat, f"Создал плейлист «{name}». Добавляй треки из /library командой /playlist_add номер_плейлиста номер_трека.")
                    elif command == "/playlist_add":
                        values = argument.split()
                        playlists = library.list_playlists(user)
                        tracks = library.list_tracks(user)
                        if len(values) != 2 or not all(value.isdigit() for value in values):
                            telegram.send(chat, "Формат: /playlist_add номер_плейлиста номер_трека. Номера смотри в /playlists и /library.")
                            continue
                        playlist_index, track_index = map(int, values)
                        if not 1 <= playlist_index <= len(playlists) or not 1 <= track_index <= len(tracks):
                            telegram.send(chat, "Не нашёл такой номер. Проверь /playlists и /library.")
                            continue
                        row = tracks[track_index - 1]
                        track = {"title": row[1], "artist": row[2], "album": row[3], "url": row[4], "source": row[5], "artwork": row[6], "duration": row[7], "preview_url": row[8], "youtube_id": row[9], "spotify_id": row[10]}
                        _saved, added = library.add_to_playlist(user, playlists[playlist_index - 1][0], track)
                        telegram.send(chat, "Добавил трек в плейлист 🎵" if added else "Этот трек уже есть в плейлисте.")
                    elif command == "/playlist_show":
                        playlists = library.list_playlists(user)
                        if not argument.isdigit() or not 1 <= int(argument) <= len(playlists):
                            telegram.send(chat, "Укажи номер из /playlists: /playlist_show 1")
                            continue
                        playlist_id, name, _count = playlists[int(argument) - 1]
                        tracks = library.list_playlist_tracks(user, playlist_id)
                        body = "\n".join(f"{index}. {row[1]} — {row[2]}" for index, row in enumerate(tracks, 1)) or "Пока пусто. Добавь трек из /library."
                        telegram.send(chat, f"Плейлист «{name}»:\n\n{body}\n\nУбрать трек: /playlist_remove {argument} номер_трека")
                    elif command == "/playlist_remove":
                        values = argument.split()
                        playlists = library.list_playlists(user)
                        if len(values) != 2 or not all(value.isdigit() for value in values):
                            telegram.send(chat, "Формат: /playlist_remove номер_плейлиста номер_трека")
                            continue
                        playlist_index, track_index = map(int, values)
                        if not 1 <= playlist_index <= len(playlists):
                            telegram.send(chat, "Не нашёл такой плейлист. Проверь /playlists.")
                            continue
                        playlist_id = playlists[playlist_index - 1][0]
                        tracks = library.list_playlist_tracks(user, playlist_id)
                        if not 1 <= track_index <= len(tracks):
                            telegram.send(chat, "Не нашёл такой трек в плейлисте. Проверь /playlist_show номер.")
                            continue
                        removed = library.remove_from_playlist(user, playlist_id, tracks[track_index - 1][0])
                        telegram.send(chat, "Убрал трек из плейлиста." if removed else "Трек уже убран.")
                    elif command == "/playlist_delete":
                        playlists = library.list_playlists(user)
                        if not argument.isdigit() or not 1 <= int(argument) <= len(playlists):
                            telegram.send(chat, "Укажи номер плейлиста из /playlists: /playlist_delete 1")
                            continue
                        _playlist_id, name, _count = playlists[int(argument) - 1]
                        library.delete_playlist(user, _playlist_id)
                        telegram.send(chat, f"Удалил плейлист «{name}».")
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

