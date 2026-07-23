"""YouTube-Data-API-v3-Schicht (offiziell, Gratis-Quota 10.000 Einheiten/Tag).

Kosten je Aufruf: search.list = 100 Einheiten, videos.list / videoCategories
= 1 Einheit. Fuer normale Recherche reicht das Gratis-Kontingent locker.
Der API-Key wird aus der Umgebungsvariable YOUTUBE_API_KEY gelesen.
"""
from __future__ import annotations

import json
import os
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

_service = None


class YouTubeApiError(RuntimeError):
    """Klar lesbarer Fehler statt eines rohen googleapiclient-Tracebacks."""


def _svc():
    global _service
    if _service is None:
        key = os.environ.get("YOUTUBE_API_KEY")
        if not key:
            raise RuntimeError(
                "YOUTUBE_API_KEY nicht gesetzt. Key in der .env hinterlegen "
                "(Google Cloud Console -> YouTube Data API v3 aktivieren -> API-Key)."
            )
        _service = build("youtube", "v3", developerKey=key, cache_discovery=False)
    return _service


def _execute(request) -> dict:
    """Fuehrt einen API-Request aus und uebersetzt HttpError in klare Meldungen."""
    try:
        return request.execute()
    except HttpError as e:
        status = getattr(getattr(e, "resp", None), "status", None)
        reason, detail = None, None
        try:
            body = json.loads(e.content.decode("utf-8"))
            err = body.get("error", {})
            detail = err.get("message")
            errs = err.get("errors") or []
            reason = errs[0].get("reason") if errs else None
        except Exception:
            pass
        if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
            raise YouTubeApiError(
                "YouTube-API-Kontingent erschoepft (10.000 Einheiten/Tag; search.list "
                "kostet 100). Morgen erneut versuchen oder Kontingent erhoehen."
            ) from e
        if reason in ("keyInvalid", "badRequest") or status == 400:
            raise YouTubeApiError(
                f"YouTube-API lehnt die Anfrage ab (Grund: {reason or 'badRequest'}). "
                "Pruefe YOUTUBE_API_KEY und ob 'YouTube Data API v3' im Projekt aktiv ist. "
                f"Details: {detail}"
            ) from e
        if reason in ("accessNotConfigured", "forbidden") or status == 403:
            raise YouTubeApiError(
                "YouTube Data API v3 ist fuer diesen Key/dieses Projekt nicht freigeschaltet "
                f"(Grund: {reason}). In der Google Cloud Console aktivieren. Details: {detail}"
            ) from e
        raise YouTubeApiError(f"YouTube-API-Fehler (HTTP {status}, {reason}): {detail}") from e


def _thumb(sn: dict) -> str | None:
    t = (sn or {}).get("thumbnails", {})
    for size in ("medium", "high", "default"):
        if size in t:
            return t[size].get("url")
    return None


def search(
    query: str,
    region: str = "DE",
    language: str = "de",
    max_results: int = 25,
    order: str = "relevance",
    published_after: str | None = None,
) -> dict[str, Any]:
    """Videos zu einem Keyword suchen (search.list, 100 Quota-Einheiten).

    order: 'relevance' | 'date' | 'viewCount' | 'rating' | 'title'.
    published_after: ISO 8601, z. B. '2026-01-01T00:00:00Z'.
    """
    params: dict[str, Any] = {
        "q": query,
        "part": "snippet",
        "type": "video",
        "maxResults": min(max(max_results, 1), 50),
        "order": order,
        "regionCode": region,
        "relevanceLanguage": language,
    }
    if published_after:
        params["publishedAfter"] = published_after
    resp = _execute(_svc().search().list(**params))

    ids = [it["id"]["videoId"] for it in resp.get("items", []) if it.get("id", {}).get("videoId")]
    stats = _stats_map(ids) if ids else {}
    videos = []
    for it in resp.get("items", []):
        vid = it.get("id", {}).get("videoId")
        sn = it.get("snippet", {})
        st = stats.get(vid, {})
        videos.append(
            {
                "video_id": vid,
                "title": sn.get("title"),
                "channel": sn.get("channelTitle"),
                "published_at": sn.get("publishedAt"),
                "views": st.get("views"),
                "likes": st.get("likes"),
                "comments": st.get("comments"),
                "url": f"https://www.youtube.com/watch?v={vid}",
                "thumbnail": _thumb(sn),
            }
        )
    return {"query": query, "region": region, "order": order, "count": len(videos), "videos": videos}


def trending(region: str = "DE", category_id: str | None = None, max_results: int = 25) -> dict[str, Any]:
    """Aktuell populaere Videos (videos.list chart=mostPopular, 1 Quota-Einheit).

    category_id optional (siehe categories()). Liefert echte View-/Like-Zahlen.
    """
    params: dict[str, Any] = {
        "part": "snippet,statistics",
        "chart": "mostPopular",
        "regionCode": region,
        "maxResults": min(max(max_results, 1), 50),
    }
    if category_id:
        params["videoCategoryId"] = str(category_id)
    resp = _execute(_svc().videos().list(**params))
    videos = []
    for it in resp.get("items", []):
        sn = it.get("snippet", {})
        st = it.get("statistics", {})
        videos.append(
            {
                "video_id": it.get("id"),
                "title": sn.get("title"),
                "channel": sn.get("channelTitle"),
                "published_at": sn.get("publishedAt"),
                "category_id": sn.get("categoryId"),
                "views": int(st["viewCount"]) if "viewCount" in st else None,
                "likes": int(st["likeCount"]) if "likeCount" in st else None,
                "comments": int(st["commentCount"]) if "commentCount" in st else None,
                "url": f"https://www.youtube.com/watch?v={it.get('id')}",
                "thumbnail": _thumb(sn),
            }
        )
    return {"region": region, "category_id": category_id, "count": len(videos), "videos": videos}


def _chunks(seq: list[str], size: int = 50):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _stats_map(video_ids: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for chunk in _chunks(video_ids, 50):
        resp = _execute(_svc().videos().list(part="statistics", id=",".join(chunk)))
        for it in resp.get("items", []):
            st = it.get("statistics", {})
            out[it["id"]] = {
                "views": int(st["viewCount"]) if "viewCount" in st else None,
                "likes": int(st["likeCount"]) if "likeCount" in st else None,
                "comments": int(st["commentCount"]) if "commentCount" in st else None,
            }
    return out


def video_stats(video_ids: list[str]) -> dict[str, Any]:
    """Statistiken zu konkreten Video-IDs (videos.list, 1 Quota-Einheit/50 IDs).

    Beliebig viele IDs moeglich - wird automatisch in 50er-Batches abgefragt.
    """
    videos = []
    for chunk in _chunks(video_ids, 50):
        resp = _execute(_svc().videos().list(part="snippet,statistics", id=",".join(chunk)))
        for it in resp.get("items", []):
            sn = it.get("snippet", {})
            st = it.get("statistics", {})
            videos.append(
                {
                    "video_id": it.get("id"),
                    "title": sn.get("title"),
                    "channel": sn.get("channelTitle"),
                    "published_at": sn.get("publishedAt"),
                    "views": int(st["viewCount"]) if "viewCount" in st else None,
                    "likes": int(st["likeCount"]) if "likeCount" in st else None,
                    "comments": int(st["commentCount"]) if "commentCount" in st else None,
                    "url": f"https://www.youtube.com/watch?v={it.get('id')}",
                }
            )
    return {"count": len(videos), "videos": videos}


def categories(region: str = "DE") -> dict[str, Any]:
    """YouTube-Videokategorien fuer eine Region (videoCategories.list)."""
    resp = _execute(_svc().videoCategories().list(part="snippet", regionCode=region))
    cats = [
        {"id": it["id"], "name": it["snippet"]["title"]}
        for it in resp.get("items", [])
        if it.get("snippet", {}).get("assignable", True)
    ]
    return {"region": region, "count": len(cats), "categories": cats}
