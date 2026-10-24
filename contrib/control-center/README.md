# Dashboard-Reiter „ISIS“ (Beispiel)

So lässt sich `isis` an ein selbstgebautes Dashboard hängen. Das ist **kein eigenständiges Frontend**,
sondern ein Ausschnitt zum Übernehmen:

- **`isis.js`**: Reiter mit vier Bereichen:
  - Übersicht (Abgleich, offene Abgaben, Termine, Kurse)
  - Kurse & Dateien (PDFs öffnen)
  - Wissen (Notizen, Suche, Typfilter, Belege mit Sprung zur Folienseite, Rückverweise, Graph per Kräfte-Layout als SVG)
  - Chat (NDJSON-Stream, `[n]` als Quellen-Links)

  Es nutzt Hilfsfunktionen des Dashboards: `$`, `esc`, `num`, `bytes`, `ago`, `pill`, `kpi`, `md` (kleines
  Markdown→HTML, escaped zuerst), `safe(el, fn)` (setzt `innerHTML`, fängt Fehler) und `currentTab()`. Dazu CSS-Klassen
  wie `.card`, `.chip`, `.chat`, `.cmsg`. Die Einbindung braucht ein `<section id="view-isis">`, und der Router ruft beim
  Öffnen des Reiters `loadIsis()` auf.
- **`proxy_snippet.py`**: die Server-Seite. Sie reicht `/api/isis/<pfad>` mit fester Pfadliste an `isis-api` weiter,
  setzt den Bearer-Token (der Browser sieht ihn nie) und streamt `/chat` und `/file` durch. `POST` nur mit
  Same-Origin-Prüfung. Dateien gehen mit `Content-Security-Policy: sandbox` raus.

Ein eigenes Frontend braucht nur [docs/API.md](../../docs/API.md). Wichtig dabei: Den Token im Backend halten, und
`ISIS_API_ALLOW` auf die IP des Backends setzen.
