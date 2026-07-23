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
DEFAULT_PROPERTIES = ["web", "youtube", "news", "images", "shopping"]

_client: Trends | None = None


def client() -> Trends:
    global _client
    if _client is None:
        _client = Trends()
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
    """
    df = _retry(client().suggestions, thema, language=language)
    recs = df_records(df)
    topic = next((r for r in recs if r.get("type") == "Topic"), recs[0] if recs else None)
    return {
        "query": thema,
        "best_topic": topic,
        "candidates": recs[:8],
        "hinweis": "Nutze best_topic['mid'] als 'topic' fuer stabile Themen-Analysen.",
    }


# ---------------------------------------------------------------------------
# Suche 1 – Thema über die verschiedenen Suchen hinweg
# ---------------------------------------------------------------------------
def topic_across_properties(
    topic: str,
    geo: str = "DE",
    timeframe: str = "today 12-m",
    category: int = 0,
    properties: list[str] | None = None,
) -> dict[str, Any]:
    """Suche 1: Interesse eines Themas über mehrere Google-Properties hinweg.

    'topic' kann eine Themen-mid ('/m/...') ODER ein Keyword sein. Empfohlen
    ist die mid aus resolve_topic, damit immer 'ueber das Thema' gemessen wird.
    Liefert je Property die 0-100-Zeitreihe plus Ausreisser/Spikes.
    """
    props = properties or DEFAULT_PROPERTIES
    result: dict[str, Any] = {
        "topic": topic,
        "geo": geo,
        "timeframe": timeframe,
        "category": category,
        "properties": {},
    }
    for name in props:
        gprop = PROPERTIES.get(name)
        if gprop is None:
            result["properties"][name] = {"error": f"unbekannte Property '{name}'"}
            continue
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
            result["properties"][name] = {
                "series": series,
                "analyse": detect_outliers(series),
            }
        except Exception as e:  # noqa: BLE001
            result["properties"][name] = {"error": f"{type(e).__name__}: {e}"}
        time.sleep(1.0)  # sanftes Rate-Limiting zwischen den Properties
    return result


def interest_by_region(
    topic: str, geo: str = "DE", timeframe: str = "today 12-m", category: int = 0, gprop: str = "web"
) -> dict[str, Any]:
    """Regionale Verteilung des Interesses (Ausreisser-Regionen sichtbar)."""
    df = _retry(
        client().interest_by_region,
        [topic],
        timeframe=timeframe,
        geo=geo,
        cat=category,
        gprop=PROPERTIES.get(gprop, ""),
    )
    recs = df_records(df)
    return {"topic": topic, "geo": geo, "gprop": gprop, "regions": recs}


# ---------------------------------------------------------------------------
# Suche 2 – Klassisches Entdecken (Kategorien/Unterkategorien)
# ---------------------------------------------------------------------------
def categories(find: str | None = None, language: str = "de") -> dict[str, Any]:
    """Kategorie-Baum von Google Trends (Kategorien + Unterkategorien).

    Mit 'find' nach Namen filtern (z. B. 'Home', 'Auto'). Ohne Filter kommt
    der komplette Baum. Die id ist der 'category'-Wert fuer die anderen Tools.
    """
    cats = _retry(client().categories, find=find, language=language) or []
    return {"find": find, "count": len(cats), "categories": cats}


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
    for it in items:
        try:
            names = list(it.topic_names) if getattr(it, "topic_names", None) else []
        except Exception:
            names = []
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
    return {"geo": geo, "hours": hours, "filter": category, "count": len(rows), "trends": rows}
