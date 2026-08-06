"""SQLite-Cache fuer Google-Trends-Antworten.

Warum: Googles Trends-Endpunkte sind kontingentiert, die Daten aendern sich aber
je nach Endpunkt stunden- bis wochenlang nicht. Rising Queries ueber drei Monate
sind eine Stunde spaeter dieselben - sie trotzdem neu anzufragen kostet nur
Wartezeit und Kontingent.

Gecacht werden die **bereits aufbereiteten** Rueckgabewerte aus gtrends.py, nicht
die rohen trendspy-Objekte: DataFrames und TrendKeyword sind nicht
JSON-serialisierbar, die Dicts aus utils.df_records/series_from_df dagegen schon.

Der Cache ist absichtlich unkritisch: jeder SQLite-Fehler fuehrt zu einem
stillen Miss, nie zu einem Tool-Fehler. Schlimmstenfalls laeuft alles wie vorher.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from typing import Any, Callable

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DIR = os.path.join(os.path.dirname(_HERE), ".cache")
_DB_NAME = "trends.sqlite"

# Wie lange ein Eintrag als frisch gilt (Sekunden), pro Namespace.
TTL: dict[str, int] = {
    "suggestions": 30 * 86400,      # Themen-mids aendern sich praktisch nie
    "categories": 30 * 86400,       # statischer Kategorie-Baum
    "iot": 6 * 3600,                # interest_over_time, Tagesaufloesung
    "compare": 6 * 3600,
    "ibr": 6 * 3600,                # interest_by_region
    "related_queries": 6 * 3600,    # der kontingentierte Fall
    "related_topics": 6 * 3600,
    "trending_now": 1800,           # einziger wirklich frischer Endpunkt
}
_TTL_FALLBACK = 3600

# Abgelaufene Eintraege bleiben absichtlich liegen - sie sind der Stale-Fallback
# bei erschoepftem Kontingent. Erst jenseits dieser Grenze wird aufgeraeumt.
MAX_AGE = 90 * 86400

# Wartezeit vor dem einen Wiederholungsversuch, wenn kein Cache-Eintrag existiert.
# Kurze Backoffs (2/4/8 s) helfen bei Googles Kontingent nicht - das erholt sich
# eher im Minutenbereich.
QUOTA_RETRY_DELAY = 30.0


# ---------------------------------------------------------------------------
# Speicher
# ---------------------------------------------------------------------------
def db_path() -> str:
    """Ort der Cache-DB. Per TRENDS_MCP_CACHE ueberschreibbar (Datei ODER Ordner)."""
    override = os.environ.get("TRENDS_MCP_CACHE")
    if override:
        # Ordner (existierend oder ohne Dateiendung) -> DB darin anlegen
        is_dir = os.path.isdir(override) or not os.path.splitext(override)[1]
        path = os.path.join(override, _DB_NAME) if is_dir else override
    else:
        path = os.path.join(_DEFAULT_DIR, _DB_NAME)
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS entries ("
        " key TEXT PRIMARY KEY,"
        " namespace TEXT NOT NULL,"
        " value TEXT NOT NULL,"
        " created REAL NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_ns ON entries(namespace)")
    return conn


def _key(namespace: str, params: dict[str, Any]) -> str:
    raw = namespace + "|" + json.dumps(params, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _lookup(namespace: str, params: dict[str, Any], max_age: float | None):
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT value, created FROM entries WHERE key = ?", (_key(namespace, params),)
            ).fetchone()
    except Exception:  # noqa: BLE001 - Cache darf nie das Tool kippen
        return None
    if not row:
        return None
    value_json, created = row
    age = max(0.0, time.time() - float(created))
    if max_age is not None and age > max_age:
        return None
    try:
        return json.loads(value_json), age
    except Exception:  # noqa: BLE001 - kaputter Eintrag = Miss
        return None


def ttl_for(namespace: str) -> int:
    return TTL.get(namespace, _TTL_FALLBACK)


def get(namespace: str, params: dict[str, Any], ttl: float | None = None):
    """Frischer Treffer -> (wert, alter_in_sekunden); sonst None."""
    return _lookup(namespace, params, ttl_for(namespace) if ttl is None else ttl)


def get_stale(namespace: str, params: dict[str, Any]):
    """Treffer beliebigen Alters -> (wert, alter_in_sekunden); sonst None."""
    return _lookup(namespace, params, None)


def _has_error(obj: Any) -> bool:
    """True, wenn irgendwo im Ergebnis ein Fehler- oder Skip-Marker steckt."""
    if isinstance(obj, dict):
        if "error" in obj or "skipped" in obj:
            return True
        return any(_has_error(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_error(v) for v in obj)
    return False


def put(namespace: str, params: dict[str, Any], value: Any) -> bool:
    """Speichert das Ergebnis. Teilergebnisse mit Fehlern werden verworfen -
    sonst friert ein einzelner Quota-Fehler fuer Stunden im Cache ein."""
    if value is None or _has_error(value):
        return False
    try:
        payload = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return False
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO entries (key, namespace, value, created) VALUES (?,?,?,?)",
                (_key(namespace, params), namespace, payload, time.time()),
            )
            # Nur sehr alte Eintraege raeumen - abgelaufene sind der Stale-Fallback.
            conn.execute("DELETE FROM entries WHERE created < ?", (time.time() - MAX_AGE,))
        return True
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Aufruf-Wrapper
# ---------------------------------------------------------------------------
def _stamp(age: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - age))


def _with_meta(value: Any, *, hit: bool, age: float, stale: bool, quota_note: str | None = None) -> Any:
    """Haengt einen 'cache'-Block an, damit sichtbar ist, wie alt die Zahlen sind."""
    if not isinstance(value, dict):
        return value
    meta: dict[str, Any] = {
        "hit": hit,
        "stale": stale,
        "age_min": round(age / 60, 1),
        "gespeichert": _stamp(age),
    }
    if stale:
        meta["hinweis"] = (
            "Google-Trends-Kontingent erschoepft - diese Antwort stammt aus dem Cache "
            f"vom {_stamp(age)} (Alter {round(age / 60)} Min) und ist NICHT tagesaktuell."
        )
        if quota_note:
            meta["quota_fehler"] = quota_note
    return {**value, "cache": meta}


def cached_call(
    namespace: str,
    params: dict[str, Any],
    producer: Callable[[], Any],
    ttl: float | None = None,
    quota_exc: type[BaseException] | tuple[type[BaseException], ...] = (),
    before_network: Callable[[], None] | None = None,
    retry_delay: float = QUOTA_RETRY_DELAY,
) -> Any:
    """Cache-first Aufruf mit Stale-Fallback bei erschoepftem Kontingent.

    Ablauf:
      1. Frischer Cache-Treffer          -> sofort zurueck
      2. Sonst producer() aufrufen       -> Erfolg: speichern und zurueck
      3. Bei Quota-Fehler:
         a) abgelaufener Cache vorhanden -> den zurueckgeben (0 s Wartezeit),
            markiert als stale
         b) sonst retry_delay warten und genau einmal wiederholen; scheitert das
            auch, fliegt der Quota-Fehler weiter

    'before_network' wird direkt vor einem echten Request aufgerufen (fuer
    Rate-Limiting zwischen mehreren Calls) - bei Cache-Treffern gar nicht.
    """
    hit = get(namespace, params, ttl)
    if hit is not None:
        value, age = hit
        return _with_meta(value, hit=True, age=age, stale=False)

    def _run() -> Any:
        if before_network is not None:
            before_network()
        return producer()

    try:
        value = _run()
    except quota_exc as quota:  # type: ignore[misc]
        stale = get_stale(namespace, params)
        if stale is not None:
            cached, age = stale
            return _with_meta(cached, hit=True, age=age, stale=True, quota_note=str(quota))
        time.sleep(retry_delay)
        value = _run()  # letzter Versuch - scheitert er, faellt der Fehler durch

    put(namespace, params, value)
    return _with_meta(value, hit=False, age=0.0, stale=False)


# ---------------------------------------------------------------------------
# Wartung
# ---------------------------------------------------------------------------
def stats() -> dict[str, Any]:
    """Belegung pro Namespace inkl. Alter des aeltesten/juengsten Eintrags."""
    path = db_path()
    out: dict[str, Any] = {"db": path, "namespaces": []}
    try:
        out["groesse_kb"] = round(os.path.getsize(path) / 1024, 1) if os.path.isfile(path) else 0
        with _connect() as conn:
            rows = conn.execute(
                "SELECT namespace, COUNT(*), MIN(created), MAX(created) FROM entries GROUP BY namespace"
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        return {"db": path, "error": f"{type(e).__name__}: {e}"}
    now = time.time()
    total = 0
    for ns, count, oldest, newest in sorted(rows):
        total += count
        out["namespaces"].append(
            {
                "namespace": ns,
                "eintraege": count,
                "ttl_min": round(ttl_for(ns) / 60),
                "aeltester_min": round((now - float(oldest)) / 60, 1),
                "juengster_min": round((now - float(newest)) / 60, 1),
            }
        )
    out["eintraege_gesamt"] = total
    return out


def clear(namespace: str | None = None) -> dict[str, Any]:
    """Leert den Cache komplett oder nur einen Namespace."""
    try:
        with _connect() as conn:
            if namespace:
                cur = conn.execute("DELETE FROM entries WHERE namespace = ?", (namespace,))
            else:
                cur = conn.execute("DELETE FROM entries")
            deleted = cur.rowcount
        return {"geloescht": deleted, "namespace": namespace or "alle"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
