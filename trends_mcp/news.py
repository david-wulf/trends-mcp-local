"""Google-News-Schicht ueber die oeffentlichen RSS-Feeds (kein API-Key noetig).

Liefert aktuelle Schlagzeilen zu einem Thema als Content-Ideen (Titel), inkl.
Quelle, Datum und Link. Nutzt die offiziellen news.google.com/rss-Endpunkte.
"""
from __future__ import annotations

import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from typing import Any

_UA = "Mozilla/5.0 (compatible; trends-mcp-local/1.0)"
_STOP = {
    "der", "die", "das", "und", "oder", "mit", "fuer", "für", "von", "im", "in", "auf",
    "ist", "im", "zum", "zur", "den", "dem", "ein", "eine", "einer", "so", "jetzt", "bei",
    "the", "and", "for", "with", "you", "your", "this", "that", "was", "wie", "nach",
}


def _fetch(url: str) -> list[dict[str, Any]]:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    data = urllib.request.urlopen(req, timeout=25).read()
    root = ET.fromstring(data)
    out: list[dict[str, Any]] = []
    for it in root.findall(".//item"):
        raw_title = it.findtext("title") or ""
        src_el = it.find("{*}source")
        source = src_el.text if src_el is not None else None
        # Google haengt " - Quelle" an den Titel; fuer saubere Ideen abtrennen
        clean = raw_title
        if source and clean.endswith(f" - {source}"):
            clean = clean[: -(len(source) + 3)]
        elif " - " in clean:
            clean = clean.rsplit(" - ", 1)[0]
        out.append(
            {
                "title": clean.strip(),
                "source": source,
                "published": it.findtext("pubDate"),
                "link": it.findtext("link"),
            }
        )
    return out


def _keyword_ideas(titles: list[str], top_n: int = 15) -> list[dict[str, Any]]:
    """Haeufige Begriffe aus den Schlagzeilen als grobe Themen-Signale."""
    counter: Counter[str] = Counter()
    for t in titles:
        for w in "".join(c.lower() if c.isalnum() or c.isspace() else " " for c in t).split():
            if len(w) >= 4 and w not in _STOP and not w.isdigit():
                counter[w] += 1
    return [{"begriff": w, "vorkommen": n} for w, n in counter.most_common(top_n) if n > 1]


def search(
    thema: str,
    language: str = "de",
    country: str = "DE",
    when: str | None = None,
    max_results: int = 25,
) -> dict[str, Any]:
    """Google-News-Suche zu einem Thema -> Schlagzeilen als Content-Ideen.

    when: optionaler Recency-Filter im Google-News-Stil, z. B. '1d', '7d', '1h'.
    Gibt saubere Titel (Quelle abgetrennt), Quelle, Datum, Link zurueck plus
    haeufige Begriffe (idea_keywords) als grobe Content-Signale.
    """
    q = f"{thema} when:{when}" if when else thema
    ceid = f"{country}:{language}"
    url = (
        "https://news.google.com/rss/search?"
        + urllib.parse.urlencode({"q": q, "hl": language, "gl": country, "ceid": ceid})
    )
    items = _fetch(url)[: max(1, min(max_results, 100))]
    return {
        "thema": thema,
        "country": country,
        "language": language,
        "when": when,
        "count": len(items),
        "content_ideas": items,
        "idea_keywords": _keyword_ideas([i["title"] for i in items]),
    }


def headlines(topic: str = "", language: str = "de", country: str = "DE", max_results: int = 25) -> dict[str, Any]:
    """Aktuelle Top-Schlagzeilen (topic='' = Startseite) oder eines News-Topics.

    topic-Kuerzel z. B. 'TECHNOLOGY', 'BUSINESS', 'ENTERTAINMENT', 'SPORTS',
    'SCIENCE', 'HEALTH'. Leer = allgemeine Top-News des Landes.
    """
    base = "https://news.google.com/rss"
    if topic:
        url = f"{base}/headlines/section/topic/{topic}?" + urllib.parse.urlencode(
            {"hl": language, "gl": country, "ceid": f"{country}:{language}"}
        )
    else:
        url = f"{base}?" + urllib.parse.urlencode(
            {"hl": language, "gl": country, "ceid": f"{country}:{language}"}
        )
    items = _fetch(url)[: max(1, min(max_results, 100))]
    return {"topic": topic or "TOP", "country": country, "count": len(items), "headlines": items}
