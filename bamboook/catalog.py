"""Search multiple music catalogs and rank matching tracks."""

from __future__ import annotations

import base64
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

_spotify_token: str | None = None
_spotify_token_expires = 0.0


def _duration(milliseconds: int | float | None) -> str:
    if not milliseconds:
        return ""
    seconds = max(0, int(milliseconds) // 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _json_request(url: str, headers: dict[str, str] | None = None, data: bytes | None = None) -> dict:
    request = Request(url, headers=headers or {}, data=data)
    with urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def _jamendo_search(query: str, limit: int) -> list[dict[str, str]]:
    """Find tracks Jamendo explicitly allows apps to download.

    Only CC0 tracks are eligible, avoiding non-commercial, no-derivatives,
    and attribution-link conditions for redistributed converted audio.
    """
    client_id = os.environ.get("JAMENDO_CLIENT_ID", "").strip()
    if not client_id:
        return []
    params = urlencode({
        "client_id": client_id,
        "format": "json",
        "search": query,
        "limit": max(1, min(limit * 3, 30)),
        "type": "single albumtrack",
        "order": "relevance",
        "audioformat": "mp32",
        "audiodlformat": "mp32",
        "include": "licenses",
        "ccnc": "false",
        "ccnd": "false",
    })
    payload = _json_request(
        f"https://api.jamendo.com/v3.0/tracks/?{params}",
        {"User-Agent": "BamBook/0.1"},
    )
    tracks = []
    for item in payload.get("results", []):
        license_url = (item.get("license_ccurl") or "").lower()
        cc0 = "creativecommons.org/publicdomain/zero/" in license_url
        if not item.get("audiodownload_allowed") or not item.get("audiodownload") or not cc0:
            continue
        tracks.append({
            "title": item.get("name", "Unknown track"),
            "artist": item.get("artist_name", "Unknown artist"),
            "album": item.get("album_name", ""),
            "url": item.get("shareurl", ""),
            "source": "Jamendo",
            "artwork": item.get("image", ""),
            "duration": _duration((item.get("duration") or 0) * 1000),
            "download_url": item["audiodownload"],
            "jamendo_id": str(item.get("id", "")),
            "license": "CC0",
        })
    return tracks


def _get_spotify_token() -> str | None:
    global _spotify_token, _spotify_token_expires
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None
    if _spotify_token and time.time() < _spotify_token_expires:
        return _spotify_token

    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    body = urlencode({"grant_type": "client_credentials"}).encode()
    payload = _json_request(
        "https://accounts.spotify.com/api/token",
        {"Authorization": f"Basic {credentials}", "Content-Type": "application/x-www-form-urlencoded"},
        body,
    )
    _spotify_token = payload["access_token"]
    _spotify_token_expires = time.time() + max(60, int(payload.get("expires_in", 3600)) - 60)
    return _spotify_token


def _spotify_search(query: str, limit: int) -> list[dict[str, str]]:
    token = _get_spotify_token()
    if not token:
        return []
    params = urlencode({"q": query, "type": "track", "limit": limit, "market": "US"})
    payload = _json_request(
        f"https://api.spotify.com/v1/search?{params}",
        {"Authorization": f"Bearer {token}"},
    )
    tracks = payload.get("tracks", {}).get("items", [])
    return [
        {
            "title": item.get("name", "Unknown track"),
            "artist": ", ".join(a.get("name", "") for a in item.get("artists", [])),
            "album": item.get("album", {}).get("name", ""),
            "url": item.get("external_urls", {}).get("spotify", ""),
            "source": "Spotify",
            "spotify_id": item.get("id", ""),
            "artwork": (item.get("album", {}).get("images") or [{}])[0].get("url", ""),
            "duration": _duration(item.get("duration_ms")),
        }
        for item in tracks
        if item.get("name") and item.get("external_urls", {}).get("spotify")
    ]


def _youtube_search(query: str, limit: int) -> list[dict[str, str]]:
    """Search regular YouTube video metadata; never download media."""
    import yt_dlp

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playlistend": limit,
        "noplaylist": True,
        "socket_timeout": 10,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        payload = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    return [
        {
            "title": item.get("title", "Unknown track"),
            "artist": item.get("artist") or item.get("uploader") or item.get("channel") or "YouTube creator",
            "album": "",
            "url": item.get("webpage_url") or f"https://www.youtube.com/watch?v={item['id']}",
            "source": "YouTube",
            "youtube_id": item["id"],
            "artwork": item.get("thumbnail", ""),
            "duration": _duration((item.get("duration") or 0) * 1000),
        }
        for item in (payload or {}).get("entries", [])
        if item and item.get("id") and item.get("title")
    ]


def _youtube_music_search(query: str, limit: int) -> list[dict[str, str]]:
    """Search YouTube Music's songs section via yt-dlp metadata only."""
    import yt_dlp

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playlistend": limit,
        "noplaylist": True,
        "socket_timeout": 10,
    }
    target = "https://music.youtube.com/search?" + urlencode({"q": query}) + "#songs"
    with yt_dlp.YoutubeDL(options) as ydl:
        payload = ydl.extract_info(target, download=False)
    tracks = []
    for item in (payload or {}).get("entries", []):
        if not item or not item.get("id") or not item.get("title"):
            continue
        tracks.append({
            "title": item.get("title", "Unknown track"),
            "artist": item.get("artist") or item.get("uploader") or item.get("channel") or "YouTube Music",
            "album": item.get("album") or "",
            "url": item.get("webpage_url") or f"https://music.youtube.com/watch?v={item['id']}",
            "source": "YouTube Music",
            "youtube_id": item["id"],
            "artwork": item.get("thumbnail", ""),
            "duration": _duration((item.get("duration") or 0) * 1000),
        })
    return tracks


def _normalize(value: str) -> str:
    value = re.sub(r"\([^)]*(official|lyrics|audio|video|remaster)[^)]*\)", " ", value, flags=re.I)
    return " ".join(re.findall(r"[\w]+", value.casefold()))


def _relevance(query: str, track: dict[str, str]) -> float:
    query_text = _normalize(query)
    track_text = _normalize(f"{track['artist']} {track['title']}")
    query_tokens = set(query_text.split())
    track_tokens = set(track_text.split())
    overlap = len(query_tokens & track_tokens) / max(1, len(query_tokens))
    phrase_match = SequenceMatcher(None, query_text, track_text).ratio()
    return overlap * 0.75 + phrase_match * 0.25


def _same_recording(left: dict[str, str], right: dict[str, str]) -> bool:
    title_score = SequenceMatcher(None, _normalize(left["title"]), _normalize(right["title"])).ratio()
    artist_score = SequenceMatcher(None, _normalize(left["artist"]), _normalize(right["artist"])).ratio()
    return title_score >= 0.93 and artist_score >= 0.55 or title_score >= 0.82 and artist_score >= 0.78


def search_tracks(query: str, limit: int = 5) -> list[dict[str, str]]:
    """Search every configured catalog concurrently and return ranked results."""
    query = query.strip()
    if not query:
        return []

    providers = (_spotify_search, _youtube_search, _youtube_music_search, _jamendo_search)
    results: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=len(providers)) as executor:
        futures = {executor.submit(provider, query, limit): provider for provider in providers}
        for future in as_completed(futures):
            try:
                results.extend(future.result())
            except Exception as error:
                # One catalog being unavailable must not hide results from the others.
                import logging

                logging.getLogger("bambook.catalog").warning(
                    "Music catalog %s failed (%s, HTTP %s)",
                    futures[future].__name__, type(error).__name__, getattr(error, "code", "n/a"),
                )

    unique: dict[str, dict] = {}
    for track in results:
        key = f"{_normalize(track['artist'])}|{_normalize(track['title'])}"
        link = {"source": track["source"], "url": track["url"]}
        matching_key = next((existing_key for existing_key, existing in unique.items() if _same_recording(track, existing)), key)
        if matching_key not in unique:
            unique[matching_key] = {**track, "sources": [track["source"]], "links": [link]}
        else:
            combined = unique[matching_key]
            if track["source"] not in combined["sources"]:
                combined["sources"].append(track["source"])
            if not any(existing["url"] == link["url"] for existing in combined["links"]):
                combined["links"].append(link)
            if _relevance(query, track) > _relevance(query, combined):
                links = combined["links"]
                sources = combined["sources"]
                combined.update(track)
                combined["links"] = links
                combined["sources"] = sources
            else:
                for field in ("youtube_id", "spotify_id", "download_url", "jamendo_id", "license"):
                    if not combined.get(field) and track.get(field):
                        combined[field] = track[field]
    ranked = sorted(unique.values(), key=lambda track: _relevance(query, track), reverse=True)
    return ranked[: limit * 2]

