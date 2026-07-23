# Trends-MCP-Local

Lokaler MCP-Server für Trend-Recherche aus **Google Trends + YouTube + Reddit** —
gratis, ohne Fremd-Dienst, Daten laufen direkt von deinem Rechner zu den Quellen.

- **Google Trends** — via `trendspy` (klassische Trends-Endpunkte), **kein Key nötig**
- **Google News** — öffentliche RSS-Feeds für Schlagzeilen/Content-Ideen, **kein Key nötig**
- **YouTube** — offizielle YouTube Data API v3 (Gratis-Quota 10.000 Einheiten/Tag)
- **Reddit** — offizielle Reddit-API via PRAW (kostenlose script-App, 100 QPM)

TikTok/Instagram/Facebook sind bewusst **nicht** enthalten (kein seriöser Gratis-Zugang).

---

## Die zwei Google-Trends-Suchmodi

### Suche 1 — Thema über die verschiedenen Suchen hinweg
1. `gt_resolve_topic("Saugroboter")` → liefert die Themen-ID (`mid`, z. B. `/m/0bwlch6`).
2. `gt_topic_across_properties("/m/0bwlch6", geo="DE")` → misst das Thema in
   **Web- und News-Suche** (Default, Discover-relevant) und gibt je Property die
   0-100-Prozentkurve **plus Ausreißer/Spikes** (Peak, aktueller Wert, Z-Wert) zurück.
   Volle Liste per `properties=["web","youtube","news","images","shopping"]`;
   `include_series=False` liefert nur die kompakte Analyse (für Massen-Screenings).

Immer über die Themen-`mid` reingehen → misst die Entität, nicht einen mehrdeutigen String.

### Themen-Kandidaten priorisieren
- `gt_compare(["Balkonkraftwerk", "Wärmepumpe", "Saugroboter"])` → bis zu 5 Keywords
  in **einem** Request, gemeinsam normierte 0-100-Skala + Ranking nach aktuellem
  Interesse. Ideal um zu entscheiden, welcher Artikel-Kandidat zuerst dran ist.

### Suche 2 — Klassisches Entdecken (Kategorien/Unterkategorien)
- `gt_categories(find="Home")` → Kategorie-Baum mit IDs (Kategorien + Unterkategorien).
- `gt_discover_category(category=11, seed="")` → rising/top Themen & Suchanfragen einer
  (Unter-)Kategorie. `seed=""` = reine Kategorie-Entdeckung wie in der Oberfläche.
- `gt_trending_now(geo="DE", category="Shopping")` → aktuell trendende Suchen mit
  Volumen und Wachstum in %, optional nach Kategorie gefiltert (robustester Einstieg).

> ⚠️ `gt_discover_category` nutzt Googles `related_queries/related_topics` — die haben ein
> **striktes Kontingent**. Bei Quota-Meldung 1–2 Minuten warten. `gt_trending_now`,
> `gt_topic_across_properties`, `gt_categories` sind robust.

---

## Klassische-Ansicht-Detailgrad (alle 4 Dimensionen)

`gt_topic_across_properties` bildet die klassische Google-Trends-Oberfläche voll ab:

| Dimension | Parameter | Werte |
|---|---|---|
| **Land** | `geo` | `DE`, `US`, `AT`, `DE-BY` (Bundesland) … |
| **Zeitraum** | `timeframe` | `today 12-m`, `today 5-y`, `now 7-d`, `2024-01-01 2024-12-31` … |
| **Kategorie + Subkategorie** | `category` | jede ID aus `gt_categories` (Haupt- **und** Unterkategorie) |
| **Art der Suche** | `properties` | `web`, `youtube`, `news`, `images`, `shopping` |

## Alle Tools

**Google Trends:** `gt_resolve_topic`, `gt_topic_across_properties`, `gt_compare`,
`gt_interest_by_region`, `gt_categories`, `gt_discover_category`, `gt_trending_now`
**Google News:** `news_search` (Content-Ideen zu einem Thema), `news_headlines` (Top/Ressort)
**YouTube:** `yt_search`, `yt_trending`, `yt_video_stats`, `yt_categories`
**Reddit:** `reddit_search`, `reddit_rising`, `reddit_hot`, `reddit_top`, `reddit_find_subreddits`

---

## Einrichtung

Die virtuelle Umgebung (`.venv`) mit allen Abhängigkeiten ist bereits angelegt, und der
Server ist in Claude Code user-weit als **`trends-local`** registriert.

### Keys hinterlegen (nur für YouTube + Reddit)
Google Trends funktioniert sofort. Für YouTube/Reddit `.env.example` nach `.env` kopieren
und ausfüllen:

```bash
cp .env.example .env
```

- **YOUTUBE_API_KEY** — [Google Cloud Console](https://console.cloud.google.com/) → Projekt
  anlegen → „YouTube Data API v3" aktivieren → Anmeldedaten → API-Schlüssel.
- **REDDIT_CLIENT_ID / _SECRET** — https://www.reddit.com/prefs/apps → „create app" →
  Typ **script**. `client_id` steht unter dem App-Namen, `secret` im Secret-Feld.

Nach dem Ausfüllen Claude Code neu starten (Server liest die `.env` beim Start).

### `.env` außerhalb des Repos ablegen (empfohlen für Secrets)
Der Ort der `.env` ist frei wählbar — Priorität:
1. Start-Argument `--env`, z. B. in der MCP-Registrierung:
   ```bash
   claude mcp add trends-local --scope user -- "<pfad>\.venv\Scripts\python.exe" "<pfad>\server.py" --env "C:\Users\david\secrets\trends.env"
   ```
2. Umgebungsvariable `TRENDS_MCP_ENV=C:\Users\david\secrets\trends.env` (Datei **oder** Ordner)
3. Fallback: `.env` im Projektordner

So liegen die Keys nie im Git-Repo.

### Neu einrichten auf einem anderen Rechner
```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
claude mcp add trends-local --scope user -- "<pfad>\.venv\Scripts\python.exe" "<pfad>\server.py"
```

---

## Grenzen (ehrlich)
- Google Trends liefert **relative** 0-100-Werte, kein absolutes Suchvolumen (dafür Ahrefs).
- `trendspy` nutzt inoffizielle Endpunkte — kann bei Google-Änderungen mal brechen (Update abwarten).
- `related_queries/related_topics` sind kontingentiert (siehe oben).
- YouTube `search.list` kostet 100 Quota-Einheiten → ~100 Suchen/Tag im Gratis-Rahmen.
