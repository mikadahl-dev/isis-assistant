# Architektur

Python 3.13, keine Web-Frameworks, kein ORM: `httpx` für HTTP, `pypdf` für PDF-Text, SQLite (WAL, FTS5) für alles
Gespeicherte, systemd für Zeitpläne. Der Code liegt root-eigen in `/opt/isis`, die Dienste laufen als Benutzer `isis`.

| Datei | Aufgabe |
|---|---|
| `app/isis/moodle.py` | Login-Link, Token einlösen, REST-Client (`call`, 1 Anfrage/s), `download` (nur URLs von `ISIS_URL`) |
| `app/isis/sync.py` | ein Abgleich: Kurse → Inhalte → Termine → Downloads → `isis.ics` |
| `app/isis/db.py` | Schema + `MIGRATIONS` für später hinzugekommene Spalten |
| `app/isis/ics.py` | RFC-5545-Kalender (CRLF, 75-Oktett-Faltung) |
| `app/isis/icloud.py` | iCloud-CalDAV: Zugang speichern, Kalender lesend auflisten |
| `app/isis/cli.py` | Befehl `isis` |
| `app/isis/knowledge/` | Wissensnetz: `chunk`, `extract`, `jeff`, `merge`, `vault`, `build` |
| `app/isis/api.py` | lesende HTTP-API + Chat |
| `systemd/` | Units (gehärtet: `ProtectSystem=strict`, nur `/var/lib/isis` und `/mnt/isis` schreibbar) |

## Login

Moodle-Mobile-SSO: `admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=<zufall>&urlscheme=moodlemobile`.
Nach dem Shibboleth-Login leitet ISIS auf `moodlemobile://token=<base64>` weiter. Der Inhalt ist
`md5(wwwroot + passport):::TOKEN:::PRIVATETOKEN`. `redeem()` prüft die Signatur gegen den gespeicherten Passport und
speichert **nur TOKEN** (Web-Service-Token). Das private Token, mit dem man eine Browser-Sitzung erzeugen könnte, wird
verworfen. Siehe [SECURITY.md](SECURITY.md).

## Abgleich (`isis-sync`, alle 2 h)

REST `webservice/rest/server.php` (Parameter als `x[0][y]` geplättet):

| Funktion | wofür |
|---|---|
| `core_webservice_get_site_info` | Konto, Prüfung |
| `core_course_get_enrolled_courses_by_timeline_classification` (`inprogress`) | laufende Kurse, wie „Laufend“ im Dashboard |
| `core_course_get_contents` | Abschnitte, Module, Dateien, Links |
| `core_calendar_get_calendar_events` | Termine −30 bis +180 Tage |
| `core_calendar_get_action_events_by_timesort` | „noch zu erledigen“ → `actionable=1` |

Dateien kommen über `webservice/pluginfile.php?token=…`. Neu geladen wird nur, wenn ISIS ein neues
`timemodified`/`filesize` meldet. Was in ISIS verschwindet, bekommt `gone=1` und bleibt auf der Platte. Eine Weiterleitung
auf `/login/` oder eine JSON-Fehlermeldung beim Download heißt „Token abgelaufen“ (Exit 75).

Tabellen: `courses`, `files` (Schlüssel `cmid:pfad/name`, stabil über Neu-Uploads), `links`, `events`, `runs`.

## Wissensnetz (`isis wissen build`)

Ziel: Ein 9B-Modell soll nicht „ein Lernsystem bauen“, sondern nur kleine, prüfbare Aufgaben lösen. Den Rest macht Code.

1. **Häppchen** (`chunk.py`): Text je Seite (`pypdf`, ISIS-Textseiten als HTML→Text), an Seitengrenzen zu ≤ 5.500
   Zeichen gepackt. Folien mit Formeln haben ≈ 2,1 Zeichen/Token, so bleibt ein Aufruf (Anleitung + Text + Antwort)
   unter 60 % von 8k Kontext (gemessen max. 58 %). Seiten < 80 Zeichen (Bildfolien) fallen weg.
2. **Herausziehen** (`extract.py`): ein Ollama-Aufruf je Häppchen, `format` = JSON-Schema (structured outputs),
   `think: false`, Temperatur 0,1. Höchstens 8 Einträge mit `type ∈ {begriff, formel, verfahren, beispiel, termin, orga}`,
   Titel, 1–2 Sätze, **wörtliches Zitat**, Wichtigkeit 1–3, verwandte Titel. Die Rohantwort wird gespeichert (`chunks.raw`).
3. **Prüfen ohne KI** (`verify`): Das Zitat muss nach Normalisierung (NFKC, Silbentrennung, Leerraum, notfalls nur
   Buchstaben) im Häppchen stehen. Die Seite ergibt sich aus der Fundstelle. Mindestens die Hälfte der Titelwörter muss
   im Text vorkommen (gegen erfundene Titel). Termine brauchen ein Datum im Zitat. `orga` fliegt raus.
   `--recheck` wiederholt diese Prüfungen aus den gespeicherten Rohantworten, ohne Modell.
4. **jeff** (`jeff.py`, optional): [jeff](https://github.com/firelex/jeff) ist ein 0,8B-*Entscheidungs*modell
   (Qwen3.5-0.8B, GGUF Q8_0), das auf der CPU über llama.cpp `llama-server` läuft. Es antwortet nur mit Optionscodes:
   - Adapter `ground`: Ist die Aussage durch die Folienseiten belegt (supported / partly / contradicted / unsupported)?
   - Basismodell: Sind zwei fast gleiche Titel derselbe Begriff (ja/nein)? Die Antwort wird in `merge_decisions` gespeichert.

   Technik: Prompt im jeff-Format, `logit_bias` +1000 auf die Antwort-Token, Wahrscheinlichkeiten aus `n_probs`,
   Temperatur je Format aus `*.jeff.json`, Adapter je Anfrage über das Feld `lora`.
   **jeff löscht nichts.** `ground` wurde auf Englisch trainiert und hält bei deutschen Folien auch richtige Aussagen
   oft für widersprüchlich. Darum markiert jeff nur (`belegt: unsicher`, `#isis/pruefen`).
5. **Zusammenführen** (`merge.py`): Schlüssel = normalisierter Titel (Umlaute, Artikel, leichtes Stemming). Gleicher
   Schlüssel im selben Kurs = derselbe Knoten, neue Quelle wird angehängt. Fast gleiche Schlüssel (Teilstring,
   Levenshtein ≤ 2) fragt jeff. `related` wird zu Kanten.
6. **Vault** (`vault.py`): `<DATA>/wissen/<Kurs>/<Titel>.md` mit YAML-Kopf (`typ`, `kurs`, `wichtigkeit`, `quellen`,
   `belegt`, `tags`, `generiert: isis`), Inhalt, Zitaten, `[[Kurs/Titel|Titel]]`-Links, „Auch in“ für andere Kurse, plus
   `_Index.md` je Kurs. Geschrieben wird nur, was sich geändert hat, gelöscht nur, was `isis` selbst erzeugt hat.
   Dateinamen sind NTFS-tauglich.

Ein fertiges Häppchen (sha1 des Textes) kostet beim nächsten Lauf nichts. Ändert sich eine Datei, verschwindet, was aus
ihrer alten Fassung stammt.

Tabellen: `chunks`, `nodes`, `sources` (Zitat, Seite, jeff-Urteil), `edges`, `merge_decisions`.

## API und Chat (`isis-api`)

`ThreadingHTTPServer` aus der Standardbibliothek. Nur IPs aus `ISIS_API_ALLOW` mit Bearer-Token. Liest `isis.db` und die
Dateien, schreibt nur den eigenen Suchindex `search.db`. Endpunkte: [API.md](API.md).

**Chat** (Retrieval, kein Agent):

1. Aus den letzten zwei Nutzerfragen wird eine FTS5-Abfrage (Stoppwörter raus, längere Wörter als Präfix).
2. Gesucht wird in `notes` (Titel ×4, Inhalt + Zitate) und `pages` (Folientext je Seite) aus `search.db`. Der Index baut
   sich nach, wenn sich eine Datei (sha256) oder das Netz ändert.
3. Kontext: bis zu 6 Notizen, bis zu 5 Folienseiten (≤ 1.400 Zeichen, zusammen ≤ 7.000), die nächsten 12 ISIS-Termine,
   das heutige Datum. Alles nummeriert.
4. Das 9B antwortet als Stream und soll nur aus dem Kontext antworten und mit `[n]` belegen. Die Quellenliste geht vorher
   an den Client, der daraus Links zur Notiz oder zur Folienseite (`#page=n`) macht. Eine Frage gleichzeitig.
