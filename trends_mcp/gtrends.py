"""Google-Trends-Schicht auf Basis von trendspy (klassische Trends-Endpunkte).

Zwei Suchmodi:
  Suche 1 – topic_across_properties: mit einem Thema reingehen, über die
            verschiedenen Google-Suchen hinweg (Web, YouTube, News, Bilder,
            Shopping), inkl. Prozentwerten und Ausreißern.
  Suche 2 – klassisches Entdecken: Kategorie-Baum + Trends je (Unter-)Kategorie
            und aktuell trendende Suchen (mit Wachstum).
"""
from __future__ import annotations

import time
from typing import Any

from trendspy import Trends

from .utils import detect_outliers, df_records, series_from_df

# Google-Property-Codes (gprop) -> lesbarer Name
PROPERTIES: dict[str, str] = {
    "web": "",
    "youtube": "youtube",
    "news": "news",
    "images": "images",
    "shopping": "froogle",
}
# Fuer Discover-Recherche sind Web + News die tragenden Signale; die volle
# Liste (plus youtube/images/shopping) ist per 'properties' explizit waehlbar.
DEFAULT_PROPERTIES = ["web", "news"]
ALL_PROPERTIES = ["web", "youtube", "news", "images", "shopping"]

_client: Trends | None = None

# In-Memory-Caches fuer stabile Ergebnisse (sparen Requests und 429-Risiko)
_categories_cache: dict[str, list] = {}
_suggestions_cache: dict[tuple[str, str], dict] = {}


def client() -> Trends:
    global _client
    if _client is None:
        # request_delay: trendspy-Empfehlung gegen Googles 429-Rate-Limit
        _client = Trends(request_delay=1.5)
    return _client


class TrendsQuotaError(RuntimeError):
    """Google hat das (kostenlose) Kontingent temporaer erschoepft."""


def _retry(fn, *args, tries: int = 3, base_delay: float = 2.0, **kwargs):
    """Ruft eine trendspy-Methode mit Backoff bei 429/Quota/Netzfehlern auf.

    Bei erschoepftem Google-Kontingent wird eine klar lesbare TrendsQuotaError
    geworfen (statt eines rohen trendspy-Fehlers).
    """
    last = None
    for attempt in range(tries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - trendspy wirft diverse Typen
            last = e
            msg = str(e).lower()
            is_quota = "quota" in msg or "429" in msg or "too many" in msg
            is_net = "timeout" in msg or "connection" in msg
            # Quota erholt sich nicht in Sekunden -> nicht retryen, sofort klar melden.
            if is_quota:
                raise TrendsQuotaError(
                    "Google-Trends-Kontingent temporaer erschoepft (haeufig bei "
                    "related_queries/related_topics). Bitte 1-2 Minuten warten und "
                    "erneut versuchen, oder weniger Abfragen kurz hintereinander."
                ) from e
            # Nur transiente Netzfehler mit Backoff wiederholen.
            if is_net and attempt < tries - 1:
                time.sleep(base_delay * (attempt + 1))
                continue
            raise
    raise last


# ---------------------------------------------------------------------------
# Thema-Auflösung
# ---------------------------------------------------------------------------
def resolve_topic(thema: str, language: str = "de") -> dict[str, Any]:
    """Löst einen Begriff in Google-Trends-Themen/Entitäten auf.

    Gibt Kandidaten mit mid (z. B. '/m/0bwlch6'), Titel und Typ zurück.
    Der erste Eintrag vom Typ 'Topic' ist meist der beste Themen-Einstieg.
    Ergebnisse werden pro (Begriff, Sprache) im Prozess gecacht.
    """
    key = (thema.strip().lower(), language)
    if key in _suggestions_cache:
        return _suggestions_cache[key]
    df = _retry(client().suggestions, thema, language=language)
    recs = df_records(df)
    topic = next((r for r in recs if r.get("type") == "Topic"), recs[0] if recs else None)
    result = {
        "query": thema,
        "best_topic": topic,
        "candidates": recs[:8],
        "hinweis": "Nutze best_topic['mid'] als 'topic' fuer stabile Themen-Analysen.",
    }
    _suggestions_cache[key] = result
    return result


# ---------------------------------------------------------------------------
# Suche 1 – Thema über die verschiedenen Suchen hinweg
# ---------------------------------------------------------------------------
def topic_across_properties(
    topic: str,
    geo: str = "DE",
    timeframe: str = "today 12-m",
    category: int = 0,
    properties: list[str] | None = None,
    include_series: bool = True,
) -> dict[str, Any]:
    """Suche 1: Interesse eines Themas über mehrere Google-Properties hinweg.

    'topic' kann eine Themen-mid ('/m/...') ODER ein Keyword sein. Empfohlen
    ist die mid aus resolve_topic, damit immer 'ueber das Thema' gemessen wird.
    Liefert je Property die 0-100-Zeitreihe plus Ausreisser/Spikes.

    Default-Properties sind ['web', 'news'] (Discover-relevant); volle Liste
    per properties=['web','youtube','news','images','shopping']. Mit
    include_series=False kommt nur die Analyse (kompakt fuer Screenings).
    """
    props = properties or DEFAULT_PROPERTIES
    result: dict[str, Any] = {
        "topic": topic,
        "geo": geo,
        "timeframe": timeframe,
        "category": category,
        "properties": {},
    }
    quota_hit = False
    for i, name in enumerate(props):
        gprop = PROPERTIES.get(name)
        if gprop is None:
            result["properties"][name] = {"error": f"unbekannte Property '{name}'"}
            continue
        if quota_hit:
            # Nach Quota-Fehler nicht weiter hammern - restliche Properties ueberspringen.
            result["properties"][name] = {"skipped": "uebersprungen (Google-Trends-Kontingent erschoepft)"}
            continue
        if i > 0:
            time.sleep(1.0)  # sanftes Rate-Limiting zwischen den Requests
        try:
            df = _retry(
                client().interest_over_time,
                [topic],
                timeframe=timeframe,
                geo=geo,
                cat=category,
                gprop=gprop,
            )
            series = series_from_df(df, keyword=topic)
            entry: dict[str, Any] = {"analyse": detect_outliers(series)}
            if include_series:
                entry["series"] = series
            result["properties"][name] = entry
        except TrendsQuotaError as e:
            quota_hit = True
            result["properties"][name] = {"error": str(e)}
        except Exception as e:  # noqa: BLE001
            result["properties"][name] = {"error": f"{type(e).__name__}: {e}"}
    return result


def compare(
    keywords: list[str],
    geo: str = "DE",
    timeframe: str = "today 3-m",
    category: int = 0,
    gprop: str = "web",
    include_series: bool = True,
) -> dict[str, Any]:
    """Bis zu 5 Keywords/Themen-mids in EINEM Request direkt vergleichen.

    Die 0-100-Skala ist ueber alle Keywords gemeinsam normiert - damit ist
    direkt ablesbar, welcher Themen-Kandidat gerade am staerksten zieht
    (Priorisierung fuer Discover-Artikel). Liefert je Keyword Zeitreihe +
    Analyse plus ein Ranking nach aktuellem Wert.
    """
    kws = [k for k in keywords if k and k.strip()][:5]
    if not kws:
        return {"error": "keine Keywords uebergeben"}
    df = _retry(
        client().interest_over_time,
        kws,
        timeframe=timeframe,
        geo=geo,
        cat=category,
        gprop=PROPERTIES.get(gprop, ""),
    )
    out: dict[str, Any] = {"keywords": kws, "geo": geo, "timeframe": timeframe, "gprop": gprop, "results": {}}
    ranking: list[dict[str, Any]] = []
    for kw in kws:
        series = series_from_df(df, keyword=kw)
        analyse = detect_outliers(series)
        entry: dict[str, Any] = {"analyse": analyse}
        if include_series:
            entry["series"] = series
        out["results"][kw] = entry
        cur = (analyse.get("current") or {}).get("value")
        peak = (analyse.get("peak") or {}).get("value")
        ranking.append({"keyword": kw, "current": cur, "peak": peak})
    ranking.sort(key=lambda r: (r["current"] or 0, r["peak"] or 0), reverse=True)
    out["ranking"] = ranking
    return out


def interest_by_region(
    topic: str,
    geo: str = "DE",
    timeframe: str = "today 12-m",
    category: int = 0,
    gprop: str = "web",
    limit: int = 25,
) -> dict[str, Any]:
    """Regionale Verteilung des Interesses (Ausreisser-Regionen sichtbar).

    Null-Regionen werden entfernt, absteigend nach Wert sortiert, auf
    'limit' gekuerzt - damit steht die Antwort auf 'wo ist das Thema am
    staerksten?' direkt oben.
    """
    df = _retry(
        client().interest_by_region,
        [topic],
        timeframe=timeframe,
        geo=geo,
        cat=category,
        gprop=PROPERTIES.get(gprop, ""),
    )
    # Regionsnamen liegen ggf. im Index -> als Spalte uebernehmen, sonst gehen sie verloren
    try:
        if df is not None and not getattr(df, "empty", True) and df.index.name:
            df = df.reset_index()
    except Exception:
        pass
    recs = df_records(df)

    def _value(rec: dict[str, Any]) -> float:
        for k, v in rec.items():
            if isinstance(v, (int, float)):
                return v
        return 0

    recs = [r for r in recs if _value(r) > 0]
    recs.sort(key=_value, reverse=True)
    total = len(recs)
    recs = recs[: max(1, limit)]
    return {"topic": topic, "geo": geo, "gprop": gprop, "regions_nonzero": total, "regions": recs}


# ---------------------------------------------------------------------------
# Suche 2 – Klassisches Entdecken (Kategorien/Unterkategorien)
# ---------------------------------------------------------------------------
def categories(find: str | None = None, language: str = "de", limit: int = 100) -> dict[str, Any]:
    """Kategorie-Baum von Google Trends (Kategorien + Unterkategorien).

    Mit 'find' nach Namen filtern (z. B. 'Home', 'Auto'). Ohne Filter wird
    auf 'limit' gekuerzt (der volle Baum hat ~1133 Eintraege - zu viel fuer
    eine Antwort; lieber gezielt mit 'find' suchen). Der Baum ist statisch
    und wird pro Sprache im Prozess gecacht. Die id ist der 'category'-Wert
    fuer die anderen Tools.
    """
    if language in _categories_cache:
        all_cats = _categories_cache[language]
    else:
        all_cats = _retry(client().categories, find=None, language=language) or []
        if all_cats:
            _categories_cache[language] = all_cats
    if find:
        needle = find.lower()
        cats = [
            c for c in all_cats
            if needle in (str(c.get("name", "")) if isinstance(c, dict) else str(c)).lower()
        ]
    else:
        cats = all_cats
    total = len(cats)
    cats = cats[: max(1, limit)]
    out: dict[str, Any] = {"find": find, "total_matches": total, "count": len(cats), "categories": cats}
    if total > len(cats):
        out["hinweis"] = f"{total - len(cats)} weitere Treffer abgeschnitten - mit 'find' eingrenzen oder 'limit' erhoehen."
    return out


def discover_category(
    category: int,
    geo: str = "DE",
    timeframe: str = "today 3-m",
    seed: str = "",
    language: str = "de",
) -> dict[str, Any]:
    """Suche 2: Klassisches Entdecken innerhalb einer (Unter-)Kategorie.

    Mit seed='' laeuft die reine Kategorie-Entdeckung (wie 'nur Kategorie
    gewaehlt' in der Google-Trends-Oberflaeche). Optional ein Seed-Keyword
    setzen, um innerhalb der Kategorie zu fokussieren. Liefert aufsteigende
    (rising) und Top-Themen sowie -Suchanfragen.
    """
    out: dict[str, Any] = {"category": category, "geo": geo, "timeframe": timeframe, "seed": seed}
    try:
        rq = _retry(client().related_queries, seed, geo=geo, timeframe=timeframe, cat=category)
        out["queries"] = {
            "rising": df_records(rq.get("rising")) if isinstance(rq, dict) else [],
            "top": df_records(rq.get("top")) if isinstance(rq, dict) else [],
        }
    except Exception as e:  # noqa: BLE001
        out["queries"] = {"error": f"{type(e).__name__}: {e}"}
    time.sleep(1.0)
    try:
        rt = _retry(client().related_topics, seed, geo=geo, timeframe=timeframe, cat=category)
        out["topics"] = {
            "rising": df_records(rt.get("rising")) if isinstance(rt, dict) else [],
            "top": df_records(rt.get("top")) if isinstance(rt, dict) else [],
        }
    except Exception as e:  # noqa: BLE001
        out["topics"] = {"error": f"{type(e).__name__}: {e}"}
    return out


def trending_now(
    geo: str = "DE",
    language: str = "de",
    hours: int = 24,
    category: str | None = None,
    limit: int = 40,
) -> dict[str, Any]:
    """Aktuell trendende Suchen (klassisches 'Trends entdecken').

    Liefert Suchbegriffe mit geschaetztem Volumen und Wachstum in Prozent,
    inkl. lesbarer Kategorienamen. Optional per 'category' (Name, z. B.
    'Sport', 'Entertainment') gefiltert.
    """
    items = _retry(client().trending_now, geo=geo, language=language, hours=hours)
    rows: list[dict[str, Any]] = []
    seen_categories: set[str] = set()
    for it in items:
        try:
            names = list(it.topic_names) if getattr(it, "topic_names", None) else []
        except Exception:
            names = []
        seen_categories.update(names)
        if category and not any(category.lower() in n.lower() for n in names):
            continue
        rows.append(
            {
                "keyword": getattr(it, "keyword", None),
                "volume": getattr(it, "volume", None),
                "volume_growth_pct": getattr(it, "volume_growth_pct", None),
                "categories": names,
                "related": (getattr(it, "trend_keywords", None) or [])[:6],
                "hours_since_started": getattr(it, "hours_since_started", None),
            }
        )
    # Erst ueber ALLE (gefilterten) Treffer sortieren, dann auf limit kuerzen,
    # damit die wachstumsstaerksten Trends nicht durch ein fruehes Abschneiden verloren gehen.
    rows.sort(key=lambda r: (r["volume_growth_pct"] or 0, r["volume"] or 0), reverse=True)
    rows = rows[: max(1, limit)]
    out: dict[str, Any] = {"geo": geo, "hours": hours, "filter": category, "count": len(rows), "trends": rows}
    if category and not rows:
        # Kategorienamen sind sprachabhaengig (language='de' -> deutsche Namen).
        # Statt still 0 Treffer zu liefern: verfuegbare Namen mitgeben.
        out["hinweis"] = (
            f"Keine Treffer fuer Kategorie '{category}'. Die Namen sind sprachabhaengig "
            f"(language='{language}'). Verfuegbare Kategorien in dieser Antwort: "
            + (", ".join(sorted(seen_categories)) or "keine")
        )
    return out
