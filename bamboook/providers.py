"""Recognize supported music-service URLs and define import adapters."""

from __future__ import annotations

from urllib.parse import urlparse


SERVICE_DOMAINS = {
    "spotify.com": "Spotify",
    "music.yandex.ru": "Яндекс Музыка",
    "music.youtube.com": "YouTube Music",
    "youtube.com": "YouTube Music",
    "youtu.be": "YouTube Music",
    "soundcloud.com": "SoundCloud",
    "music.apple.com": "Apple Music",
}


def identify_service(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    for domain, service in SERVICE_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return service
    return None


def import_playlist(service: str, url: str) -> list[dict[str, str]]:
    """Return playlist tracks once a service-specific official API is configured."""
    raise NotImplementedError(f"Импорт плейлистов {service} ещё не подключён: {url}")
