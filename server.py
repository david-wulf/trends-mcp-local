"""Lokaler Trends-MCP-Server: Google Trends + YouTube Data API + Reddit.

Start (stdio):  python server.py [--env PFAD]
Konfiguration:  .env; Ort waehlbar via
                1) CLI-Argument   --env <datei-oder-ordner>
                2) Umgebungsvar   TRENDS_MCP_ENV=<datei-oder-ordner>
                3) Fallback       .env im Projektordner
Damit koennen die Secrets ausserhalb des Repos liegen (z. B. in OneDrive/Config).
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_env() -> str | None:
    """Laedt die .env vom ersten gefundenen Ort (CLI > ENV-Var > Projektordner)."""
    override = None
    if "--env" in sys.argv:
        i = sys.argv.index("--env")
        if i + 1 < len(sys.argv):
            override = sys.argv[i + 1]
    override = override or os.environ.get("TRENDS_MCP_ENV")

    candidates: list[str] = []
    if override:
        # Ordner -> darin nach .env suchen; sonst direkter Dateipfad
        candidates.append(os.path.join(override, ".env") if os.path.isdir(override) else override)
    candidates.append(os.path.join(_HERE, ".env"))

    for path in candidates:
        if path and os.path.isfile(path):
            load_dotenv(path)
            return path
    load_dotenv()  # ambient (bereits gesetzte Prozess-Variablen respektieren)
    return None


_load_env()
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from mcp.server.mcpserver import MCPServer

from trends_mcp import cache, gtrends, news, reddit_api, youtube

mcp = MCPServer("trends-local")

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
    include_series: bool = True,
) -> dict:
    """SUCHE 1: Interesse eines Themas ueber die verschiedenen Google-Suchen hinweg.

    Misst das Thema (mid aus gt_resolve_topic oder Keyword) in mehreren
    Google-Properties. Liefert je Property die 0-100-Zeitreihe plus
    Ausreisser-/Spike-Analyse (Peak, aktueller Wert, Ausreisser mit Z-Wert).

    properties: None = ['web','news'] (Discover-relevant, schnell); volle
    Liste: ['web','youtube','news','images','shopping']. include_series=False
    liefert nur die kompakte Analyse (fuer Massen-Screenings). timeframe z. B.
    'today 12-m', 'today 5-y', 'now 7-d' (stuendlich, mit Uhrzeit).
    """
    return gtrends.topic_across_properties(
        topic, geo=geo, timeframe=timeframe, category=category,
        properties=properties, include_series=include_series,
    )


@mcp.tool()
def gt_compare(
    keywords: list[str],
    geo: str = "DE",
    timeframe: str = "today 3-m",
    category: int = 0,
    gprop: str = "web",
    include_series: bool = True,
) -> dict:
    """Bis zu 5 Keywords/Themen-mids in EINEM Request direkt vergleichen.

    Die 0-100-Skala ist ueber alle Keywords gemeinsam normiert - ideal, um
    Themen-Kandidaten fuer Discover-Artikel zu priorisieren ('welches Thema
    zieht gerade am staerksten?'). Liefert je Keyword Zeitreihe + Analyse
    plus ein Ranking nach aktuellem Interesse.
    """
    return gtrends.compare(
        keywords, geo=geo, timeframe=timeframe, category=category,
        gprop=gprop, include_series=include_series,
    )


@mcp.tool()
def gt_interest_by_region(
    topic: str,
    geo: str = "DE",
    timeframe: str = "today 12-m",
    category: int = 0,
    gprop: str = "web",
    limit: int = 25,
) -> dict:
    """Regionale Verteilung des Interesses fuer ein Thema (Ausreisser-Regionen).

    Absteigend nach Interesse sortiert, Null-Regionen entfernt, auf 'limit'
    gekuerzt.
    """
    return gtrends.interest_by_region(
        topic, geo=geo, timeframe=timeframe, category=category, gprop=gprop, limit=limit
    )


@mcp.tool()
def gt_categories(find: str | None = None, language: str = "de", limit: int = 100) -> dict:
    """SUCHE 2 (Schritt 1): Kategorie-Baum von Google Trends durchsuchen.

    Der Baum hat ~1133 Eintraege - Haupt- UND Unterkategorien (z. B.
    'Home & Garden'=11 und die Unterkategorien 'Home Appliances'=271,
    'Home Furnishings'=270). Jede 'id' - auch die einer Unterkategorie - ist
    direkt als 'category' in gt_topic_across_properties und
    gt_discover_category verwendbar.

    WICHTIG zu 'find': Die Kategorienamen stehen in der gewaehlten 'language'
    (bei language='de' also 'Erneuerbare und alternative Energien', nicht
    'Renewable & Alternative Energy'). Damit eine englische Suche trotzdem
    trifft, matcht 'find' gegen BEIDE Baeume - find='Energy' und find='Energie'
    liefern dieselben ids (233, 954, 657). Die ids sind sprachunabhaengig.
    Jeder Treffer enthaelt 'name' (Zielsprache) und 'name_en'.

    Bei 0 Treffern also nicht auf category=0 zurueckfallen, sondern ein
    kuerzeres Stichwort probieren ('Solar' statt 'Solaranlage').
    Der Baum ist 30 Tage gecacht (wiederholte Aufrufe sind gratis)."""
    return gtrends.categories(find=find, language=language, limit=limit)


@mcp.tool()
def gt_discover_category(
    category: int,
    geo: str = "DE",
    timeframe: str = "today 3-m",
    seed: str = "",
    language: str = "de",
    gprop: str = "web",
) -> dict:
    """SUCHE 2: Klassisches Entdecken innerhalb einer (Unter-)Kategorie.

    seed='' = reine Kategorie-Entdeckung (wie 'nur Kategorie gewaehlt' in der
    Google-Trends-Oberflaeche). Optional Seed-Keyword setzen, um innerhalb der
    Kategorie zu fokussieren. Liefert aufsteigende (rising) und Top-Themen sowie
    -Suchanfragen.

    gprop waehlt die Art der Suche: 'web' (Default), 'youtube', 'news',
    'images', 'shopping'. Damit gibt es auch fuer Shopping und YouTube echte
    Queries statt nur Interessekurven. Ein unbekannter Wert wird abgewiesen und
    faellt NICHT still auf 'web' zurueck.

    Hinweis: Diese Endpunkte haben ein striktes Google-Kontingent. Ergebnisse
    werden 6 Stunden gecacht; bei erschoepftem Kontingent kommt automatisch der
    letzte bekannte Stand mit 'cache': {'stale': true, 'age_min': ...} zurueck,
    statt dass der Aufruf scheitert.
    """
    return gtrends.discover_category(
        category, geo=geo, timeframe=timeframe, seed=seed, language=language, gprop=gprop
    )


@mcp.tool()
def gt_trending_now(
    geo: str = "DE", language: str = "de", hours: int = 24, category: str | None = None, limit: int = 40
) -> dict:
    """SUCHE 2: Aktuell trendende Suchen mit Volumen und Wachstum in Prozent.

    Robuster Einstieg ins 'Trends entdecken'. Optional per 'category' (Name)
    filtern - Google liefert diese Namen in der Regel englisch aus, auch bei
    language='de' ('Sports', 'Politics', 'Entertainment', 'Other'). Bei 0
    Treffern listet der 'hinweis' die tatsaechlich vorhandenen Namen auf.
    'hours_since_started' ist das Alter des Trends in Stunden.
    30 Minuten gecacht.
    """
    return gtrends.trending_now(geo=geo, language=language, hours=hours, category=category, limit=limit)


@mcp.tool()
def gt_cache(action: str = "stats", namespace: str | None = None) -> dict:
    """Wartung des Google-Trends-Caches (SQLite, ueberlebt Neustarts).

    action='stats' zeigt Eintraege, TTL und Alter pro Namespace.
    action='clear' leert alles oder - mit 'namespace' - nur einen Bereich
    ('suggestions', 'categories', 'iot', 'compare', 'ibr', 'related_queries',
    'related_topics', 'trending_now'), um frische Daten zu erzwingen.
    """
    act = (action or "stats").strip().lower()
    if act == "stats":
        return cache.stats()
    if act == "clear":
        return cache.clear(namespace)
    return {"error": f"unbekannte action '{action}'. Gueltig: stats, clear"}


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
