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
- `gt_discover_category(category=11, seed="", gprop="web")` → rising/top Themen &
  Suchanfragen einer (Unter-)Kategorie. `seed=""` = reine Kategorie-Entdeckung wie in
  der Oberfläche. `gprop` wählt die Art der Suche (`web`, `youtube`, `news`, `images`,
  `shopping`) — damit gibt es auch für Shopping und YouTube **echte Queries** statt nur
  Interessekurven.
- `gt_trending_now(geo="DE", category="Sports")` → aktuell trendende Suchen mit
  Volumen und Wachstum in %, optional nach Kategorie gefiltert (robustester Einstieg).

> ⚠️ `gt_discover_category` nutzt Googles `related_queries/related_topics` — die haben ein
> **striktes Kontingent**. Der Cache fängt das ab (siehe unten): bei erschöpftem Kontingent
> kommt der letzte bekannte Stand als `stale` zurück, statt dass der Aufruf scheitert.

### `find` sucht in beiden Sprachen
Die Kategorienamen kommen in der gewählten `language` — bei `language="de"` heißt die
Kategorie `Erneuerbare und alternative Energien`, nicht `Renewable & Alternative Energy`.
Die Trends-Topics selbst liefert Google dagegen **englisch** aus („Home energy storage",
„Power inverter"), also sucht man ganz natürlich englisch und fand früher nichts —
und landete fälschlich bei `category=0`.

Deshalb matcht `find` gegen **beide** Bäume. `find="Energy"` und `find="Energie"` liefern
dieselben drei IDs (233, 954, 657); die IDs sind sprachunabhängig. Jeder Treffer trägt
`name` (Zielsprache), `name_en` und `matched_via`.

Randnotiz: die Kategorienamen in `gt_trending_now` kommen von Google auch bei
`language="de"` englisch zurück (`Sports`, `Politics`, `Other`). Bei 0 Treffern listet der
`hinweis` die tatsächlich vorhandenen Namen auf.

---

## Cache & Kontingent

Alle Google-Trends-Antworten liegen in einer SQLite-DB (`.cache/trends.sqlite`, per
`TRENDS_MCP_CACHE` verlegbar, nicht im Git). Sie überlebt Neustarts — die früheren
In-Memory-Caches waren nach jedem Serverstart wieder leer.

| Namespace | TTL | Inhalt |
|---|---|---|
| `suggestions` | 30 Tage | `gt_resolve_topic` (Themen-mids sind statisch) |
| `categories` | 30 Tage | Kategorie-Baum je Sprache |
| `iot` | 6 Std | `gt_topic_across_properties`, **pro Property einzeln** |
| `compare` | 6 Std | `gt_compare` |
| `ibr` | 6 Std | `gt_interest_by_region` |
| `related_queries` / `related_topics` | 6 Std | `gt_discover_category`, getrennt gecacht |
| `trending_now` | 30 Min | `gt_trending_now` |

**Bei erschöpftem Kontingent** greift der Cache als Fallback, statt den Aufruf scheitern
zu lassen:

1. Frischer Eintrag da → sofort zurück, gar kein Request.
2. Nur ein **abgelaufener** Eintrag da → der kommt zurück, markiert als
   `"cache": {"stale": true, "age_min": 412, ...}` samt Warnhinweis. **Null Sekunden
   Wartezeit** — das ist der eigentliche Zeitgewinn.
3. Nichts im Cache → einmal 30 s warten und genau einmal wiederholen, dann klare
   Fehlermeldung. Kurze Backoffs (2/4/8 s) helfen bei Googles Kontingent nicht, das
   erholt sich eher im Minutenbereich. Netzfehler (Timeout/Connection) bekommen davon
   unabhängig drei Versuche mit 2/4 s.

Pro Aufruf wird höchstens **einmal** 30 s gewartet — danach laufen die restlichen
Teilabfragen nur noch gegen den Cache.

Jede Antwort trägt einen `cache`-Block mit `hit`, `stale`, `age_min` und Zeitstempel,
damit nie unklar ist, wie alt die Zahlen sind. Teilergebnisse mit Fehlern werden
**nicht** gespeichert — sonst würde ein einzelner Quota-Fehler stundenlang einfrieren.

`gt_cache(action="stats")` zeigt die Belegung, `gt_cache(action="clear", namespace="...")`
erzwingt frische Daten.

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
`gt_interest_by_region`, `gt_categories`, `gt_discover_category`, `gt_trending_now`,
`gt_cache` (Cache-Wartung: `stats` / `clear`)
**Google News:** `news_search` (Content-Ideen zu einem Thema), `news_headlines` (Top/Ressort)
**YouTube:** `yt_search`, `yt_trending`, `yt_video_stats`, `yt_categories`
**Reddit:** `reddit_search`, `reddit_rising`, `reddit_hot`, `reddit_top`, `reddit_find_subreddits`
— **bewusst nicht in Betrieb**, siehe [Reddit: bewusst deaktiviert](#reddit-bewusst-deaktiviert).

---

## Reddit: bewusst deaktiviert

**Entscheidung (29.07.2026): Wir richten Reddit nicht ein.** Die fünf `reddit_*`-Tools
bleiben im Code, laufen aber mangels Credentials nicht — das ist **kein
Konfigurationsfehler und soll nicht "repariert" werden.**

**Grund:** Reddits [Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy)
trennt zwischen nicht-kommerzieller Nutzung (freie Script-App) und kommerzieller
Nutzung, die eine **ausdrückliche schriftliche Freigabe** von Reddit verlangt:

> „If you'd like to use Reddit data for commercial purposes, you'll need to get
> explicit written approval."

Themenrecherche für homeandsmart.de ist kommerzielle Nutzung — der Zweck ist Content,
der Umsatz bringt. Ohne Freigabe wäre der Betrieb formal ein Policy-Verstoß, und der
Aufwand einer Anfrage steht in keinem Verhältnis zum Nutzen: Reddit war hier ohnehin
nur Beiwerk, die Recherche trägt sich über Google Trends, News und YouTube.

Zur Einordnung, falls die Frage wieder aufkommt:

- Die App-Verbote der Policy (Vote-Manipulation, Spam, automatisierte DMs) wären
  strukturell gar nicht verletzbar — der Server ist auf `read_only = True` festgenagelt
  und hat kein einziges Schreib-Tool. Daran liegt es also nicht.
- Das Verbot betrifft **Training** von KI-Modellen mit Reddit-Daten. Inhalte in einen
  Chat-Kontext zu laden ist kein Training — dieser Punkt war nicht der Blocker.
- Der Blocker ist allein die fehlende kommerzielle Freigabe.

**Wenn sich das ändern soll:** über den Kontaktweg in der Policy die kommerzielle
Freigabe einholen, *danach* eine Script-App unter
https://www.reddit.com/prefs/apps anlegen und `REDDIT_CLIENT_ID` /
`REDDIT_CLIENT_SECRET` / `REDDIT_USER_AGENT` in der `.env` setzen. Codeseitig ist
nichts zu tun.

---

## Einrichtung

Die virtuelle Umgebung (`.venv`) mit allen Abhängigkeiten ist bereits angelegt, und der
Server ist in Claude Code user-weit als **`trends-local`** registriert.

### Keys hinterlegen (nur für YouTube)
Google Trends und Google News funktionieren sofort ohne Key. Für YouTube
`.env.example` nach `.env` kopieren und ausfüllen:

```bash
cp .env.example .env
```

- **YOUTUBE_API_KEY** — [Google Cloud Console](https://console.cloud.google.com/) → Projekt
  anlegen → „YouTube Data API v3" aktivieren → Anmeldedaten → API-Schlüssel.

Die Reddit-Variablen bleiben leer — siehe [Reddit: bewusst deaktiviert](#reddit-bewusst-deaktiviert).

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

### Update einer bestehenden Installation
Seit 29.07.2026 läuft der Server auf **MCP-SDK 2.x** (Spec-Revision `2026-07-28`).
Das ist ein **gekoppelter** Wechsel: `mcp 2.0` hat `FastMCP` in `MCPServer`
umbenannt und `mcp.server.fastmcp` entfernt. Der neue Code startet mit dem alten
SDK nicht (`ModuleNotFoundError: No module named 'mcp.server.mcpserver'`).

Also immer **beides zusammen**:

```bash
git pull
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Danach Claude Code neu starten. An der Registrierung ändert sich nichts.

---

## Grenzen (ehrlich)
- Google Trends liefert **relative** 0-100-Werte, kein absolutes Suchvolumen (dafür Ahrefs).
- `trendspy` nutzt inoffizielle Endpunkte — kann bei Google-Änderungen mal brechen (Update abwarten).
- `related_queries/related_topics` sind kontingentiert. Der Cache mildert das, hebt es aber
  nicht auf: beim allerersten Aufruf einer Kombination gibt es nichts zurückzufallen.
- Ein `stale`-Ergebnis ist per Definition nicht tagesaktuell — bei `gt_trending_now` ist das
  relevant, bei Rising Queries über drei Monate praktisch nie.
- YouTube `search.list` kostet 100 Quota-Einheiten → ~100 Suchen/Tag im Gratis-Rahmen.
