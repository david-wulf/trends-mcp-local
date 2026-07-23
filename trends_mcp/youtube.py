"""YouTube-Data-API-v3-Schicht (offiziell, Gratis-Quota 10.000 Einheiten/Tag).

Kosten je Aufruf: search.list = 100 Einheiten, videos.list / videoCategories
= 1 Einheit. Fuer normale Recherche reicht das Gratis-Kontingent locker.
Der API-Key wird aus der Umgebungsvariable YOUTUBE_API_KEY gelesen.
"""
from __future__ import annotations

import os
from typing import Any

from googleapiclient.discovery import build

_service = None


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
    resp = _svc().search().list(**params).execute()

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
    resp = _svc().videos().list(**params).execute()
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


def _stats_map(video_ids: list[str]) -> dict[str, dict[str, Any]]:
    resp = _svc().videos().list(part="statistics", id=",".join(video_ids[:50])).execute()
    out: dict[str, dict[str, Any]] = {}
    for it in resp.get("items", []):
        st = it.get("statistics", {})
        out[it["id"]] = {
            "views": int(st["viewCount"]) if "viewCount" in st else None,
            "likes": int(st["likeCount"]) if "likeCount" in st else None,
            "comments": int(st["commentCount"]) if "commentCount" in st else None,
        }
    return out


def video_stats(video_ids: list[str]) -> dict[str, Any]:
    """Statistiken zu konkreten Video-IDs (videos.list, 1 Quota-Einheit)."""
    resp = _svc().videos().list(part="snippet,statistics", id=",".join(video_ids[:50])).execute()
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
                "views": int(st["viewCount"]) if "viewCount" in st else None,
                "likes": int(st["likeCount"]) if "likeCount" in st else None,
                "comments": int(st["commentCount"]) if "commentCount" in st else None,
                "url": f"https://www.youtube.com/watch?v={it.get('id')}",
            }
        )
    return {"count": len(videos), "videos": videos}


def categories(region: str = "DE") -> dict[str, Any]:
    """YouTube-Videokategorien fuer eine Region (videoCategories.list)."""
    resp = _svc().videoCategories().list(part="snippet", regionCode=region).execute()
    cats = [
        {"id": it["id"], "name": it["snippet"]["title"]}
        for it in resp.get("items", [])
        if it.get("snippet", {}).get("assignable", True)
    ]
    return {"region": region, "count": len(cats), "categories": cats}
