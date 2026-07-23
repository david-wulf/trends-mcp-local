"""Hilfsfunktionen: Zeitreihen-Aufbereitung und Ausreißer-Erkennung."""
from __future__ import annotations

import math
import statistics
from typing import Any


def _index_has_time(df) -> bool:
    """True, wenn der Zeit-Index Uhrzeiten traegt (stuendliche Timeframes wie 'now 7-d')."""
    try:
        return any(getattr(idx, "hour", 0) or getattr(idx, "minute", 0) for idx in df.index)
    except Exception:
        return False


def series_from_df(df, keyword: str | None = None) -> list[dict[str, Any]]:
    """Wandelt einen trendspy interest_over_time-DataFrame in eine Liste um.

    Erwartet einen DataFrame mit Zeit-Index und einer Wertspalte
    (Keyword/Topic-mid) plus optional 'isPartial'. Bei stuendlichen Daten
    ('now 7-d' etc.) bleibt die Uhrzeit erhalten, damit Spikes eindeutig sind.
    """
    if df is None or getattr(df, "empty", True):
        return []
    value_cols = [c for c in df.columns if c != "isPartial"]
    col = keyword if keyword in value_cols else (value_cols[0] if value_cols else None)
    if col is None:
        return []
    partial_col = "isPartial" if "isPartial" in df.columns else None
    fmt = "%Y-%m-%d %H:%M" if _index_has_time(df) else "%Y-%m-%d"
    out: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        try:
            date = idx.strftime(fmt)
        except Exception:
            date = str(idx)
        raw = row[col]
        # NaN/None-Zellen ueberspringen (int(nan) wuerde werfen)
        try:
            value = int(raw)
        except (ValueError, TypeError):
            continue
        out.append(
            {
                "date": date,
                "value": value,
                "partial": bool(row[partial_col]) if partial_col else False,
            }
        )
    return out


def detect_outliers(series: list[dict[str, Any]], z_threshold: float = 2.0) -> dict[str, Any]:
    """Erkennt Ausreißer/Spikes in einer 0-100 Zeitreihe.

    Nutzt den robusten modifizierten Z-Score (Median + MAD). Punkte mit
    |modified_z| >= z_threshold gelten als Ausreißer. Zusätzlich wird das
    Maximum markiert. Gibt eine kompakte Zusammenfassung zurück.
    """
    vals = [p["value"] for p in series if not p["partial"]] or [p["value"] for p in series]
    if len(vals) < 3:
        return {"outliers": [], "peak": None, "baseline_median": None, "current": None}

    median = statistics.median(vals)
    abs_dev = [abs(v - median) for v in vals]
    mad = statistics.median(abs_dev)
    # Fallback auf Standardabweichung, falls MAD == 0 (viele identische Werte)
    scale = mad * 1.4826 if mad > 0 else (statistics.pstdev(vals) or 1.0)

    outliers = []
    for p in series:
        mz = (p["value"] - median) / scale if scale else 0.0
        if abs(mz) >= z_threshold:
            outliers.append(
                {
                    "date": p["date"],
                    "value": p["value"],
                    "z": round(mz, 2),
                    "direction": "hoch" if mz > 0 else "runter",
                    "partial": p["partial"],
                }
            )

    peak = max(series, key=lambda p: p["value"])
    non_partial = [p for p in series if not p["partial"]]
    current = non_partial[-1] if non_partial else series[-1]

    return {
        "outliers": outliers,
        "peak": {"date": peak["date"], "value": peak["value"]},
        "baseline_median": round(median, 1),
        "current": {"date": current["date"], "value": current["value"]},
    }


def _to_native(value: Any) -> Any:
    """numpy-Skalare -> native Python-Typen, NaN -> None (JSON-sicher)."""
    if value is None:
        return None
    # numpy-Typen haben .item(); NaN vorher abfangen
    if isinstance(value, float) and math.isnan(value):
        return None
    item = getattr(value, "item", None)
    if callable(item) and not isinstance(value, (str, bytes, dict, list)):
        try:
            value = value.item()
        except Exception:
            return str(value)
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def df_records(df, limit: int | None = None) -> list[dict[str, Any]]:
    """DataFrame -> Liste von dicts (für related_queries/topics etc.).

    Werte werden in native Python-Typen konvertiert (numpy int64/float64 und
    NaN sind nicht JSON-serialisierbar).
    """
    if df is None or getattr(df, "empty", True):
        return []
    recs = [{k: _to_native(v) for k, v in r.items()} for r in df.to_dict(orient="records")]
    return recs[:limit] if limit else recs
