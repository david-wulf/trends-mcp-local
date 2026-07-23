"""Reddit-Schicht (offizielle API via PRAW, read-only).

Credentials aus der Umgebung: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET,
REDDIT_USER_AGENT. Kostenloser 'script'-App-Zugang reicht (100 QPM).
Registrierung: https://www.reddit.com/prefs/apps  (Typ: 'script').
"""
from __future__ import annotations

import functools
import os
from typing import Any

import praw
import prawcore

_reddit = None


class RedditApiError(RuntimeError):
    """Klar lesbarer Fehler statt eines rohen PRAW/prawcore-Tracebacks."""


def _friendly_errors(fn):
    """Uebersetzt gaengige prawcore-Fehler (404/403/401/Redirect) in Klartext."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (prawcore.exceptions.NotFound, prawcore.exceptions.Redirect) as e:
            raise RedditApiError(
                "Subreddit nicht gefunden - Schreibweise pruefen (z. B. 'smarthome' statt URL/Anzeigename)."
            ) from e
        except prawcore.exceptions.Forbidden as e:
            raise RedditApiError("Zugriff verweigert (privates/quarantiniertes Subreddit).") from e
        except prawcore.exceptions.ResponseException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status == 401:
                raise RedditApiError(
                    "Reddit-Authentifizierung fehlgeschlagen - REDDIT_CLIENT_ID/SECRET in der .env pruefen."
                ) from e
            if status == 429:
                raise RedditApiError("Reddit-Rate-Limit erreicht (100 QPM) - kurz warten und erneut versuchen.") from e
            raise RedditApiError(f"Reddit-API-Fehler (HTTP {status}).") from e
        except prawcore.exceptions.RequestException as e:
            raise RedditApiError(f"Reddit nicht erreichbar (Netzwerkfehler: {e}).") from e

    return wrapper


def _client():
    global _reddit
    if _reddit is None:
        cid = os.environ.get("REDDIT_CLIENT_ID")
        secret = os.environ.get("REDDIT_CLIENT_SECRET")
        ua = os.environ.get("REDDIT_USER_AGENT", "trends-mcp-local/1.0")
        if not cid or not secret:
            raise RuntimeError(
                "REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET nicht gesetzt. Unter "
                "https://www.reddit.com/prefs/apps eine 'script'-App anlegen und "
                "Werte in der .env hinterlegen."
            )
        _reddit = praw.Reddit(
            client_id=cid,
            client_secret=secret,
            user_agent=ua,
            check_for_async=False,
        )
        _reddit.read_only = True
    return _reddit


def _post(p) -> dict[str, Any]:
    return {
        "title": p.title,
        "subreddit": str(p.subreddit),
        "score": p.score,
        "upvote_ratio": getattr(p, "upvote_ratio", None),
        "num_comments": p.num_comments,
        "created_utc": int(p.created_utc),
        "url": f"https://www.reddit.com{p.permalink}",
        "link": p.url,
        "author": str(p.author) if p.author else None,
        "flair": getattr(p, "link_flair_text", None),
    }


@_friendly_errors
def search(
    query: str,
    subreddit: str = "all",
    sort: str = "relevance",
    time_filter: str = "month",
    limit: int = 25,
) -> dict[str, Any]:
    """Reddit-Beitraege durchsuchen.

    sort: 'relevance' | 'hot' | 'top' | 'new' | 'comments'.
    time_filter: 'hour' | 'day' | 'week' | 'month' | 'year' | 'all'.
    """
    sub = _client().subreddit(subreddit)
    posts = [
        _post(p)
        for p in sub.search(query, sort=sort, time_filter=time_filter, limit=min(max(limit, 1), 100))
    ]
    return {"query": query, "subreddit": subreddit, "sort": sort, "count": len(posts), "posts": posts}


@_friendly_errors
def rising(subreddit: str, limit: int = 25) -> dict[str, Any]:
    """Aufsteigende Beitraege eines Subreddits (frueher Trend-Indikator)."""
    posts = [_post(p) for p in _client().subreddit(subreddit).rising(limit=min(max(limit, 1), 100))]
    return {"subreddit": subreddit, "listing": "rising", "count": len(posts), "posts": posts}


@_friendly_errors
def hot(subreddit: str, limit: int = 25) -> dict[str, Any]:
    """Aktuell heisse Beitraege eines Subreddits."""
    posts = [_post(p) for p in _client().subreddit(subreddit).hot(limit=min(max(limit, 1), 100))]
    return {"subreddit": subreddit, "listing": "hot", "count": len(posts), "posts": posts}


@_friendly_errors
def top(subreddit: str, time_filter: str = "week", limit: int = 25) -> dict[str, Any]:
    """Top-Beitraege eines Subreddits im Zeitfenster."""
    posts = [
        _post(p)
        for p in _client().subreddit(subreddit).top(time_filter=time_filter, limit=min(max(limit, 1), 100))
    ]
    return {"subreddit": subreddit, "listing": f"top/{time_filter}", "count": len(posts), "posts": posts}


@_friendly_errors
def find_subreddits(query: str, limit: int = 15) -> dict[str, Any]:
    """Passende Subreddits zu einem Thema finden (fuer gezielte Recherche)."""
    subs = []
    for s in _client().subreddits.search(query, limit=min(max(limit, 1), 50)):
        subs.append(
            {
                "name": s.display_name,
                "title": getattr(s, "title", None),
                "subscribers": getattr(s, "subscribers", None),
                "url": f"https://www.reddit.com/r/{s.display_name}",
                "description": (getattr(s, "public_description", "") or "")[:200],
            }
        )
    subs.sort(key=lambda x: x["subscribers"] or 0, reverse=True)
    return {"query": query, "count": len(subs), "subreddits": subs}
