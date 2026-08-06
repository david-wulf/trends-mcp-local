"""Google-Trends-Schicht auf Basis von trendspy (klassische Trends-Endpunkte).

Zwei Suchmodi:
  Suche 1 – topic_across_properties: mit einem Thema reingehen, über die
            verschiedenen Google-Suchen hinweg (Web, YouTube, News, Bilder,
            Shopping), inkl. Prozentwerten und Ausreißern.
  Suche 2 – klassisches Entdecken: Kategorie-Baum + Trends je (Unter-)Kategorie
            und aktuell trendende Suchen (mit Wachstum).

Alle Netz-Aufrufe laufen über cache.cached_call: frischer Treffer kommt aus
SQLite, bei erschöpftem Google-Kontingent wird ein abgelaufener Eintrag als
'stale' zurückgegeben statt zu scheitern. Siehe cache.py.
"""
from __future__ import annotations

import time
from typing import Any

from trendspy import Trends

from . import cache
from .utils import detect_outliers, df_records, series_from_df

# Googles eigene Quota-Ausnahme - wenn vorhanden, per Typ fangen statt per
# String zu raten. Importgeschützt, falls trendspy sie mal umbenennt.
try:  # pragma: no cover - haengt an der trendspy-Version
    from trendspy.client import TrendsQuotaExceededError as _TrendsQuotaExceeded
except Exception:  # noqa: BLE001
    _TrendsQuotaExceeded = ()  # type: ignore[assignment]

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


def client() -> Trends:
    global _client
    if _client is None:
        # request_delay: trendspy-Empfehlung gegen Googles 429-Rate-Limit
        _client = Trends(request_delay=1.5)
    return _client


class TrendsQuotaError(RuntimeError):
    """Google hat das (kostenlose) Kontingent temporaer erschoepft."""


class UnknownPropertyError(ValueError):
    """Ein gprop-/Property-Name, den Google Trends nicht kennt."""


def _gprop(name: str | None) -> str:
    """Property-Name -> Google-gprop-Code. Unbekannte Namen werden abgewiesen.

    Frueher lief hier ein stilles PROPERTIES.get(name, "") - ein Tippfehler
    lieferte dann Web-Daten, die anschliessend als Shopping- oder YouTube-Daten
    gelesen wurden. Lieber ein klarer Fehler als eine falsche Zahl.
    """
    key = (name or "web").strip().lower()
    if key not in PROPERTIES:
        raise UnknownPropertyError(
            f"unbekannte Property '{name}'. Gueltig: " + ", ".join(PROPERTIES)
        )
    return PROPERTIES[key]


def _property_error(e: UnknownPropertyError) -> dict[str, Any]:
    return {"error": str(e), "gueltige_properties": list(PROPERTIES)}


def _pacer(delay: float = 1.0):
    """Gibt ein before_network-Callback zurueck, das ab dem zweiten echten
    Request 'delay' Sekunden pausiert (sanftes Rate-Limiting). Cache-Treffer
    loesen es nicht aus - wer nichts anfragt, muss auch nicht warten."""
    state = {"calls": 0}

    def _pace() -> None:
        if state["calls"]:
            time.sleep(delay)
        state["calls"] += 1

    return _pace


def _retry(fn, *args, tries: int = 3, base_delay: float = 2.0, **kwargs):
    """Ruft eine trendspy-Methode mit Backoff bei Netzfehlern auf.

    Bei erschoepftem Google-Kontingent wird sofort eine klar lesbare
    TrendsQuotaError geworfen (statt eines rohen trendspy-Fehlers). Das Warten
    und der eine Wiederholungsversuch passieren eine Ebene hoeher in
    cache.cached_call - dort ist auch der Stale-Cache greifbar.
    """
    last = None
    for attempt in range(tries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - trendspy wirft diverse Typen
            last = e
            msg = str(e).lower()
            is_quota = (
                isinstance(e, _TrendsQuotaExceeded)
                or "quota" in msg
                or "429" in msg
                or "too many" in msg
            )
            if is_quota:
                raise TrendsQuotaError(
                    "Google-Trends-Kontingent temporaer erschoepft (haeufig bei "
                    "related_queries/related_topics). Bitte 1-2 Minuten warten und "
                    "erneut versuchen, oder weniger Abfragen kurz hintereinander."
                ) from e
            # Nur transiente Netzfehler mit exponentiellem Backoff wiederholen.
            if ("timeout" in msg or "connection" in msg) and attempt < tries - 1:
                time.sleep(base_delay * (2 ** attempt))
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
    Ergebnisse werden 30 Tage gecacht (Themen-mids sind praktisch statisch).
    """
    params = {"thema": thema.strip().lower(), "language": language}

    def _produce() -> dict[str, Any]:
        df = _retry(client().suggestions, thema, language=language)
        recs = df_records(df)
        topic = next((r for r in recs if r.get("type") == "Topic"), recs[0] if recs else None)
        return {
            "query": thema,
            "best_topic": topic,
            "candidates": recs[:8],
            "hinweis": "Nutze best_topic['mid'] als 'topic' fuer stabile Themen-Analysen.",
        }

    return cache.cached_call("suggestions", params, _produce, quota_exc=TrendsQuotaError)


# ---------------------------------------------------------------------------
# Suche 1 – Thema über die verschiedenen Suchen hinweg
# ---------------------------------------------------------------------------
def _iot_entry(payload: dict[str, Any], include_series: bool) -> dict[str, Any]:
    """Gecachte Zeitreihe -> Antwort-Eintrag (Analyse immer, Serie optional).

    include_series ist bewusst NICHT Teil des Cache-Keys: gespeichert wird immer
    die volle Serie, gekuerzt wird erst bei der Ausgabe.
    """
    series = payload.get("series") or []
    entry: dict[str, Any] = {"analyse": detect_outliers(series)}
    if include_series:
        entry["series"] = series
    if payload.get("cache"):
        entry["cache"] = payload["cache"]
    return entry


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
    Jede Property wird einzeln gecacht - ein Quota-Treffer bei Property 4
    entwertet die ersten drei nicht.
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
    pace = _pacer()

    for name in props:
        try:
            gprop = _gprop(name)
        except UnknownPropertyError as e:
            result["properties"][name] = _property_error(e)
            continue

        params = {
            "topic": topic,
            "geo": geo,
            "timeframe": timeframe,
            "category": category,
            "gprop": gprop,
        }

        if quota_hit:
            # Nach einem Quota-Fehler nicht weiter hammern - aber der Cache ist
            # gratis, also erst dort nachsehen (auch abgelaufene Eintraege).
            hit = cache.get("iot", params) or cache.get_stale("iot", params)
            if hit is not None:
                payload, age = hit
                stale = age > cache.ttl_for("iot")
                entry = _iot_entry(payload, include_series)
                entry["cache"] = {
                    "hit": True,
                    "stale": stale,
                    "age_min": round(age / 60, 1),
                    "hinweis": "Kontingent erschoepft - Wert aus dem Cache.",
                }
                result["properties"][name] = entry
            else:
                result["properties"][name] = {
                    "skipped": "uebersprungen (Google-Trends-Kontingent erschoepft, nichts im Cache)"
                }
            continue

        def _produce(_gp: str = gprop) -> dict[str, Any]:
            df = _retry(
                client().interest_over_time,
                [topic],
                timeframe=timeframe,
                geo=geo,
                cat=category,
                gprop=_gp,
            )
            return {"series": series_from_df(df, keyword=topic)}

        try:
            payload = cache.cached_call(
                "iot", params, _produce, quota_exc=TrendsQuotaError, before_network=pace
            )
            result["properties"][name] = _iot_entry(payload, include_series)
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
    try:
        gp = _gprop(gprop)
    except UnknownPropertyError as e:
        return _property_error(e)

    params = {
        "keywords": kws,
        "geo": geo,
        "timeframe": timeframe,
        "category": category,
        "gprop": gp,
    }

    def _produce() -> dict[str, Any]:
        df = _retry(
            client().interest_over_time,
            kws,
            timeframe=timeframe,
            geo=geo,
            cat=category,
            gprop=gp,
        )
        results: dict[str, Any] = {}
        ranking: list[dict[str, Any]] = []
        for kw in kws:
            series = series_from_df(df, keyword=kw)
            analyse = detect_outliers(series)
            results[kw] = {"analyse": analyse, "series": series}
            ranking.append(
                {
                    "keyword": kw,
                    "current": (analyse.get("current") or {}).get("value"),
                    "peak": (analyse.get("peak") or {}).get("value"),
                }
            )
        ranking.sort(key=lambda r: (r["current"] or 0, r["peak"] or 0), reverse=True)
        return {"results": results, "ranking": ranking}

    data = cache.cached_call("compare", params, _produce, quota_exc=TrendsQuotaError)
    results = data.get("results", {})
    if not include_series:
        results = {k: {"analyse": v.get("analyse")} for k, v in results.items()}
    out: dict[str, Any] = {
        "keywords": kws,
        "geo": geo,
        "timeframe": timeframe,
        "gprop": gprop,
        "results": results,
        "ranking": data.get("ranking", []),
    }
    if data.get("cache"):
        out["cache"] = data["cache"]
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
    try:
        gp = _gprop(gprop)
    except UnknownPropertyError as e:
        return _property_error(e)

    params = {
        "topic": topic,
        "geo": geo,
        "timeframe": timeframe,
        "category": category,
        "gprop": gp,
    }

    def _value(rec: dict[str, Any]) -> float:
        for v in rec.values():
            if isinstance(v, (int, float)):
                return v
        return 0

    def _produce() -> dict[str, Any]:
        df = _retry(
            client().interest_by_region,
            [topic],
            timeframe=timeframe,
            geo=geo,
            cat=category,
            gprop=gp,
        )
        # Regionsnamen liegen ggf. im Index -> als Spalte uebernehmen, sonst gehen sie verloren
        try:
            if df is not None and not getattr(df, "empty", True) and df.index.name:
                df = df.reset_index()
        except Exception:  # noqa: BLE001
            pass
        recs = [r for r in df_records(df) if _value(r) > 0]
        recs.sort(key=_value, reverse=True)
        return {"regions": recs}

    data = cache.cached_call("ibr", params, _produce, quota_exc=TrendsQuotaError)
    # limit ist reine Ausgabe-Kuerzung und deshalb nicht Teil des Cache-Keys
    recs = data.get("regions", [])
    out: dict[str, Any] = {
        "topic": topic,
        "geo": geo,
        "gprop": gprop,
        "regions_nonzero": len(recs),
        "regions": recs[: max(1, limit)],
    }
    if data.get("cache"):
        out["cache"] = data["cache"]
    return out


# ---------------------------------------------------------------------------
# Suche 2 – Klassisches Entdecken (Kategorien/Unterkategorien)
# ---------------------------------------------------------------------------
def _category_tree(language: str) -> list[dict[str, Any]]:
    """Kategorie-Baum einer Sprache (30 Tage gecacht, ueberlebt Neustarts)."""

    def _produce() -> dict[str, Any]:
        return {"tree": _retry(client().categories, find=None, language=language) or []}

    data = cache.cached_call(
        "categories", {"language": language}, _produce, quota_exc=TrendsQuotaError
    )
    return data.get("tree") or []


def _cat_name(entry: Any) -> str:
    return str(entry.get("name", "")) if isinstance(entry, dict) else str(entry)


def _cat_id(entry: Any) -> Any:
    return entry.get("id") if isinstance(entry, dict) else None


def categories(find: str | None = None, language: str = "de", limit: int = 100) -> dict[str, Any]:
    """Kategorie-Baum von Google Trends (Kategorien + Unterkategorien).

    'find' matcht gegen den Baum in 'language' UND gegen den englischen Baum.
    Das ist kein Luxus: die Trends-Topics selbst kommen englisch zurueck
    ('Home energy storage'), waehrend der Baum bei language='de' nur deutsche
    Namen enthaelt - eine englische Suche fand deshalb frueher nichts und man
    landete faelschlich bei category=0. Die ids sind sprachunabhaengig
    (Energy & Utilities = Energie- und Versorgungsunternehmen = 233).

    Ohne Filter wird auf 'limit' gekuerzt (der volle Baum hat ~1133 Eintraege).
    """
    tree = _category_tree(language)
    en_tree = tree if language == "en" else []
    if language != "en":
        try:
            en_tree = _category_tree("en")
        except Exception:  # noqa: BLE001 - einsprachig weitermachen ist besser als scheitern
            en_tree = []

    en_by_id = {str(_cat_id(c)): _cat_name(c) for c in en_tree if _cat_id(c) is not None}
    bilingual = language != "en" and bool(en_by_id)

    def _record(entry: Any, name_en: str, via: str | None) -> dict[str, Any]:
        rec: dict[str, Any] = {"id": _cat_id(entry), "name": _cat_name(entry)}
        if bilingual:
            rec["name_en"] = name_en or None
            if via:
                rec["matched_via"] = via
        return rec

    if not find:
        cats = [_record(c, en_by_id.get(str(_cat_id(c)), ""), None) for c in tree]
        total = len(cats)
        cats = cats[: max(1, limit)]
        out: dict[str, Any] = {
            "find": None,
            "language": language,
            "total_matches": total,
            "count": len(cats),
            "categories": cats,
        }
        if total > len(cats):
            out["hinweis"] = (
                f"{total - len(cats)} weitere Eintraege abgeschnitten - mit 'find' eingrenzen "
                "oder 'limit' erhoehen."
            )
        return out

    needle = find.lower()
    hits: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    only_en = 0

    for c in tree:
        cid = str(_cat_id(c))
        name_en = en_by_id.get(cid, "")
        m_local = needle in _cat_name(c).lower()
        m_en = bool(name_en) and needle in name_en.lower()
        if not (m_local or m_en):
            continue
        via = "beide" if (m_local and m_en) else ("de" if m_local else "en")
        if not m_local:
            only_en += 1
        hits.append(_record(c, name_en, via if bilingual else None))
        seen_ids.add(cid)

    # Sicherheitsnetz: Treffer, die es nur im englischen Baum gibt
    for c in en_tree:
        cid = str(_cat_id(c))
        if cid in seen_ids or not (needle in _cat_name(c).lower()):
            continue
        only_en += 1
        hits.append(_record(c, _cat_name(c), "en" if bilingual else None))
        seen_ids.add(cid)

    total = len(hits)
    hits = hits[: max(1, limit)]
    out = {
        "find": find,
        "language": language,
        "total_matches": total,
        "count": len(hits),
        "categories": hits,
    }

    hinweise: list[str] = []
    if total and only_en == total:
        hinweise.append(
            f"Alle Treffer kamen ueber den englischen Baum - die Namen oben stehen in "
            f"language='{language}'. Die id ist sprachunabhaengig und direkt verwendbar."
        )
    if not total:
        hinweise.append(
            f"Keine Kategorie enthaelt '{find}' - weder im {language}- noch im englischen Baum. "
            "Kuerzeres Stichwort probieren (z. B. 'Solar' statt 'Solaranlage')."
        )
    if total > len(hits):
        hinweise.append(
            f"{total - len(hits)} weitere Treffer abgeschnitten - 'limit' erhoehen."
        )
    if hinweise:
        out["hinweis"] = " ".join(hinweise)
    return out


def discover_category(
    category: int,
    geo: str = "DE",
    timeframe: str = "today 3-m",
    seed: str = "",
    language: str = "de",
    gprop: str = "web",
) -> dict[str, Any]:
    """Suche 2: Klassisches Entdecken innerhalb einer (Unter-)Kategorie.

    Mit seed='' laeuft die reine Kategorie-Entdeckung (wie 'nur Kategorie
    gewaehlt' in der Google-Trends-Oberflaeche). Optional ein Seed-Keyword
    setzen, um innerhalb der Kategorie zu fokussieren. 'gprop' waehlt die Art
    der Suche - damit gibt es auch fuer Shopping und YouTube echte Queries
    statt nur Interessekurven. Liefert aufsteigende (rising) und Top-Themen
    sowie -Suchanfragen.
    """
    try:
        gp = _gprop(gprop)
    except UnknownPropertyError as e:
        return _property_error(e)

    out: dict[str, Any] = {
        "category": category,
        "geo": geo,
        "timeframe": timeframe,
        "seed": seed,
        "gprop": gprop,
    }
    params = {
        "seed": seed,
        "geo": geo,
        "timeframe": timeframe,
        "category": category,
        "gprop": gp,
    }
    pace = _pacer()

    # Queries und Topics getrennt cachen: ein Quota-Treffer bei den Topics
    # soll die bereits geholten Queries nicht mitreissen.
    quota_hit = False
    for out_key, namespace, method_name in (
        ("queries", "related_queries", "related_queries"),
        ("topics", "related_topics", "related_topics"),
    ):
        if quota_hit:
            # Das Kontingent ist gerade erschoepft - kein zweites Mal 30 s warten.
            # Nur noch nachsehen, ob etwas im Cache liegt.
            hit = cache.get(namespace, params) or cache.get_stale(namespace, params)
            if hit is not None:
                cached, age = hit
                out[out_key] = {
                    **cached,
                    "cache": {
                        "hit": True,
                        "stale": age > cache.ttl_for(namespace),
                        "age_min": round(age / 60, 1),
                        "hinweis": "Kontingent erschoepft - Stand aus dem Cache.",
                    },
                }
            else:
                out[out_key] = {
                    "skipped": "uebersprungen (Google-Trends-Kontingent erschoepft, nichts im Cache)"
                }
            continue

        def _produce(_m: str = method_name) -> dict[str, Any]:
            r = _retry(
                getattr(client(), _m), seed, geo=geo, timeframe=timeframe, cat=category, gprop=gp
            )
            return {
                "rising": df_records(r.get("rising")) if isinstance(r, dict) else [],
                "top": df_records(r.get("top")) if isinstance(r, dict) else [],
            }

        try:
            out[out_key] = cache.cached_call(
                namespace, params, _produce, quota_exc=TrendsQuotaError, before_network=pace
            )
        except TrendsQuotaError as e:
            quota_hit = True
            out[out_key] = {"error": str(e)}
        except Exception as e:  # noqa: BLE001
            out[out_key] = {"error": f"{type(e).__name__}: {e}"}

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
    inkl. Kategorienamen. Optional per 'category' (Name, z. B. 'Sports',
    'Entertainment') gefiltert. 30 Minuten gecacht.
    """
    params = {"geo": geo, "language": language, "hours": hours}

    def _produce() -> dict[str, Any]:
        items = _retry(client().trending_now, geo=geo, language=language, hours=hours)
        rows: list[dict[str, Any]] = []
        for it in items:
            try:
                names = list(it.topic_names) if getattr(it, "topic_names", None) else []
            except Exception:  # noqa: BLE001
                names = []
            # hours_since_started ist eine METHODE, keine Property - ohne Aufruf
            # landet hier der repr des gebundenen Objekts inkl. rohem
            # TrendKeyword-Tupel. callable()-Test, falls trendspy das spaeter
            # zur @property macht (wie bereits bei is_trend_finished).
            hrs = getattr(it, "hours_since_started", None)
            if callable(hrs):
                try:
                    hrs = hrs()
                except Exception:  # noqa: BLE001
                    hrs = None
            rows.append(
                {
                    "keyword": getattr(it, "keyword", None),
                    "volume": getattr(it, "volume", None),
                    "volume_growth_pct": getattr(it, "volume_growth_pct", None),
                    "categories": names,
                    "related": (getattr(it, "trend_keywords", None) or [])[:6],
                    "hours_since_started": round(hrs, 1) if isinstance(hrs, (int, float)) else None,
                }
            )
        # Erst ueber ALLE Treffer sortieren, dann erst filtern/kuerzen, damit die
        # wachstumsstaerksten Trends nicht durch ein fruehes Abschneiden verloren gehen.
        rows.sort(key=lambda r: (r["volume_growth_pct"] or 0, r["volume"] or 0), reverse=True)
        return {"rows": rows}

    data = cache.cached_call("trending_now", params, _produce, quota_exc=TrendsQuotaError)
    rows: list[dict[str, Any]] = data.get("rows", [])
    seen_categories = sorted({n for r in rows for n in (r.get("categories") or [])})

    if category:
        needle = category.lower()
        rows = [r for r in rows if any(needle in n.lower() for n in (r.get("categories") or []))]
    rows = rows[: max(1, limit)]

    out: dict[str, Any] = {
        "geo": geo,
        "hours": hours,
        "filter": category,
        "count": len(rows),
        "trends": rows,
    }
    if data.get("cache"):
        out["cache"] = data["cache"]
    if category and not rows:
        # Google liefert diese Kategorienamen englisch aus, auch bei language='de'.
        # Statt still 0 Treffer zu melden: die tatsaechlich vorhandenen Namen zeigen.
        out["hinweis"] = (
            f"Keine Treffer fuer Kategorie '{category}'. Die Namen kommen von Google in der "
            "Regel englisch ('Sports', 'Politics', 'Other'). Verfuegbare Kategorien in dieser "
            "Antwort: " + (", ".join(seen_categories) or "keine")
        )
    return out
