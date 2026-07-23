"""Hilfsfunktionen: Zeitreihen-Aufbereitung und Ausreißer-Erkennung."""
from __future__ import annotations

import statistics
from typing import Any


def series_from_df(df, keyword: str | None = None) -> list[dict[str, Any]]:
    """Wandelt einen trendspy interest_over_time-DataFrame in eine Liste um.

    Erwartet einen DataFrame mit Zeit-Index und einer Wertspalte
    (Keyword/Topic-mid) plus optional 'isPartial'.
    """
    if df is None or getattr(df, "empty", True):
        return []
    value_cols = [c for c in df.columns if c != "isPartial"]
    col = keyword if keyword in value_cols else (value_cols[0] if value_cols else None)
    if col is None:
        return []
    partial_col = "isPartial" if "isPartial" in df.columns else None
    out: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        try:
            date = idx.strftime("%Y-%m-%d")
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


def df_records(df, limit: int | None = None) -> list[dict[str, Any]]:
    """DataFrame -> Liste von dicts (für related_queries/topics etc.)."""
    if df is None or getattr(df, "empty", True):
        return []
    recs = df.to_dict(orient="records")
    return recs[:limit] if limit else recs
