"""Lokaler Trends-MCP-Server: Google Trends + YouTube Data API + Reddit.

Start (stdio):  python server.py
Konfiguration:  .env im Projektordner (siehe .env.example)
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

# .env aus dem Projektordner laden (unabhaengig vom Arbeitsverzeichnis)
_HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_HERE, ".env"))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from mcp.server.fastmcp import FastMCP

from trends_mcp import gtrends, news, reddit_api, youtube

mcp = FastMCP("trends-local")

# ===========================================================================
# GOOGLE TRENDS  (kein API-Key noetig; inoffizielle Endpunkte via trendspy)
# ===========================================================================

@mcp.tool()
def gt_resolve_topic(thema: str, language: str = "de") -> dict:
    """SUCHE 1 (Schritt 1): Begriff in ein Google-Trends-Thema aufloesen.

    Gibt Kandidaten mit 'mid' (z. B. '/m/0bwlch6'), Titel und Typ zurueck.
    Immer zuerst aufrufen und die 'mid' des besten Topics als 'topic' fuer
    gt_topic_across_properties verwenden, damit ueber das *Thema* (Entitaet)
    statt ueber einen mehrdeutigen Suchbegriff gemessen wird.
    """
    return gtrends.resolve_topic(thema, language=language)


@mcp.tool()
def gt_topic_across_properties(
    topic: str,
    geo: str = "DE",
    timeframe: str = "today 12-m",
    category: int = 0,
    properties: list[str] | None = None,
) -> dict:
    """SUCHE 1: Interesse eines Themas ueber die verschiedenen Google-Suchen hinweg.

    Misst das Thema (mid aus gt_resolve_topic oder Keyword) parallel in
    Web-, YouTube-, News-, Bilder- und Shopping-Suche. Liefert je Property die
    0-100-Zeitreihe plus Ausreisser-/Spike-Analyse (Peak, aktueller Wert,
    Ausreisser mit Z-Wert).

    properties: Teilmenge von ['web','youtube','news','images','shopping'];
    None = alle. timeframe z. B. 'today 12-m', 'today 5-y', 'now 7-d'.
    """
    return gtrends.topic_across_properties(
        topic, geo=geo, timeframe=timeframe, category=category, properties=properties
    )


@mcp.tool()
def gt_interest_by_region(
    topic: str, geo: str = "DE", timeframe: str = "today 12-m", category: int = 0, gprop: str = "web"
) -> dict:
    """Regionale Verteilung des Interesses fuer ein Thema (Ausreisser-Regionen)."""
    return gtrends.interest_by_region(topic, geo=geo, timeframe=timeframe, category=category, gprop=gprop)


@mcp.tool()
def gt_categories(find: str | None = None, language: str = "de") -> dict:
    """SUCHE 2 (Schritt 1): Kategorie-Baum von Google Trends durchsuchen.

    Liefert ~1133 Eintraege - Haupt- UND Unterkategorien (z. B. 'Home & Garden'=11
    und die Unterkategorien 'Home Appliances'=271, 'Home Furnishings'=270). Jede
    'id' - auch die einer Unterkategorie - ist direkt als 'category' in
    gt_topic_across_properties und gt_discover_category verwendbar. Mit 'find'
    nach Namen filtern (z. B. 'Home', 'Auto', 'Garten')."""
    return gtrends.categories(find=find, language=language)


@mcp.tool()
def gt_discover_category(
    category: int,
    geo: str = "DE",
    timeframe: str = "today 3-m",
    seed: str = "",
    language: str = "de",
) -> dict:
    """SUCHE 2: Klassisches Entdecken innerhalb einer (Unter-)Kategorie.

    seed='' = reine Kategorie-Entdeckung (wie 'nur Kategorie gewaehlt' in der
    Google-Trends-Oberflaeche). Optional Seed-Keyword setzen, um innerhalb der
    Kategorie zu fokussieren. Liefert aufsteigende (rising) und Top-Themen sowie
    -Suchanfragen. Hinweis: Diese Endpunkte haben ein striktes Google-Kontingent
    - bei Fehlermeldung 1-2 Minuten warten.
    """
    return gtrends.discover_category(category, geo=geo, timeframe=timeframe, seed=seed, language=language)


@mcp.tool()
def gt_trending_now(
    geo: str = "DE", language: str = "de", hours: int = 24, category: str | None = None, limit: int = 40
) -> dict:
    """SUCHE 2: Aktuell trendende Suchen mit Volumen und Wachstum in Prozent.

    Robuster Einstieg ins 'Trends entdecken'. Optional per 'category' (Name,
    z. B. 'Sport', 'Technology', 'Shopping', 'Entertainment') filtern.
    """
    return gtrends.trending_now(geo=geo, language=language, hours=hours, category=category, limit=limit)


# ===========================================================================
# GOOGLE NEWS  (oeffentliche RSS-Feeds; kein API-Key noetig)
# ===========================================================================

@mcp.tool()
def news_search(
    thema: str,
    language: str = "de",
    country: str = "DE",
    when: str | None = None,
    max_results: int = 25,
) -> dict:
    """Google-News-Suche zu einem Thema -> Schlagzeilen als Content-Ideen (Titel).

    Ergaenzt die Trends-Analyse um konkrete redaktionelle Aufhaenger: liefert
    saubere Titel (Quelle abgetrennt), Quelle, Datum, Link sowie haeufige
    Begriffe (idea_keywords). 'when' als Recency-Filter, z. B. '1d', '7d', '24h'.
    """
    return news.search(thema, language=language, country=country, when=when, max_results=max_results)


@mcp.tool()
def news_headlines(topic: str = "", language: str = "de", country: str = "DE", max_results: int = 25) -> dict:
    """Aktuelle Top-Schlagzeilen eines Landes oder News-Ressorts.

    topic leer = allgemeine Top-News; sonst Ressort-Kuerzel wie 'TECHNOLOGY',
    'BUSINESS', 'SCIENCE', 'HEALTH', 'ENTERTAINMENT', 'SPORTS'.
    """
    return news.headlines(topic=topic, language=language, country=country, max_results=max_results)


# ===========================================================================
# YOUTUBE DATA API v3  (Key aus YOUTUBE_API_KEY; Gratis-Quota 10.000/Tag)
# ===========================================================================

@mcp.tool()
def yt_search(
    query: str,
    region: str = "DE",
    language: str = "de",
    max_results: int = 25,
    order: str = "relevance",
    published_after: str | None = None,
) -> dict:
    """YouTube-Videos zu einem Keyword suchen (mit echten View-/Like-Zahlen).

    order: 'relevance' | 'date' | 'viewCount' | 'rating' | 'title'.
    published_after: ISO 8601 (z. B. '2026-01-01T00:00:00Z'), fuer 'nur neue'.
    Kostet 100 Quota-Einheiten pro Aufruf.
    """
    return youtube.search(
        query, region=region, language=language, max_results=max_results,
        order=order, published_after=published_after,
    )


@mcp.tool()
def yt_trending(region: str = "DE", category_id: str | None = None, max_results: int = 25) -> dict:
    """Aktuell populaere YouTube-Videos einer Region (chart=mostPopular).

    Optional category_id (siehe yt_categories). Kostet 1 Quota-Einheit.
    """
    return youtube.trending(region=region, category_id=category_id, max_results=max_results)


@mcp.tool()
def yt_video_stats(video_ids: list[str]) -> dict:
    """Statistiken (Views/Likes/Kommentare) zu konkreten Video-IDs."""
    return youtube.video_stats(video_ids)


@mcp.tool()
def yt_categories(region: str = "DE") -> dict:
    """YouTube-Videokategorien einer Region (fuer yt_trending category_id)."""
    return youtube.categories(region=region)


# ===========================================================================
# REDDIT  (offizielle API via PRAW; Credentials aus REDDIT_CLIENT_ID/SECRET)
# ===========================================================================

@mcp.tool()
def reddit_search(
    query: str,
    subreddit: str = "all",
    sort: str = "relevance",
    time_filter: str = "month",
    limit: int = 25,
) -> dict:
    """Reddit-Beitraege durchsuchen.

    sort: 'relevance' | 'hot' | 'top' | 'new' | 'comments'.
    time_filter: 'hour' | 'day' | 'week' | 'month' | 'year' | 'all'.
    subreddit='all' sucht global; sonst gezielt (z. B. 'smarthome').
    """
    return reddit_api.search(query, subreddit=subreddit, sort=sort, time_filter=time_filter, limit=limit)


@mcp.tool()
def reddit_rising(subreddit: str, limit: int = 25) -> dict:
    """Aufsteigende Beitraege eines Subreddits (frueher Trend-Indikator)."""
    return reddit_api.rising(subreddit, limit=limit)


@mcp.tool()
def reddit_hot(subreddit: str, limit: int = 25) -> dict:
    """Aktuell heisse Beitraege eines Subreddits."""
    return reddit_api.hot(subreddit, limit=limit)


@mcp.tool()
def reddit_top(subreddit: str, time_filter: str = "week", limit: int = 25) -> dict:
    """Top-Beitraege eines Subreddits im Zeitfenster (day/week/month/year/all)."""
    return reddit_api.top(subreddit, time_filter=time_filter, limit=limit)


@mcp.tool()
def reddit_find_subreddits(query: str, limit: int = 15) -> dict:
    """Passende Subreddits zu einem Thema finden (nach Abonnenten sortiert)."""
    return reddit_api.find_subreddits(query, limit=limit)


if __name__ == "__main__":
    mcp.run()
