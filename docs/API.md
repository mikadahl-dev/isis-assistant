# HTTP-API (`isis-api`, Port 8091)

Jede Anfrage braucht `Authorization: Bearer $ISIS_API_TOKEN` und eine Client-IP aus `ISIS_API_ALLOW`, sonst 401/403.
Antworten sind JSON (UTF-8), Zeiten in Unix-Sekunden. Fehler: `{"error": "…"}`.

| Methode, Pfad | Antwort |
|---|---|
| `GET /status` | Token vorhanden, letzter Abgleich (`last_run`), Zähler (Kurse, Dateien, Bytes, kommende Termine, offene Abgaben, Notizen, Kanten, Häppchen), Modell |
| `GET /courses` | `[{id, short, name, url, files, links, nodes, next: {name, timestart, actionable}}]` |
| `GET /events` | Termine ab vor 7 Tagen: `[{id, courseid, course, name, modulename, eventtype, timestart, timeduration, url, actionable}]` |
| `GET /files?course=ID` | `{files: [{key, section, module, filename, filesize, timemodified, mimetype, local, error, first_seen, changed_at}], links: [{section, name, url}]}` |
| `GET /file?key=KEY` | die Datei selbst (`Content-Type` aus ISIS, `Content-Disposition: inline`) |
| `GET /vault?course=ID` | `{nodes: [{id, title, type, importance, status, sources}], edges: [[a, b], …]}` (ungerichtet, aufgelöst) |
| `GET /node?id=N` | Notiz mit `content`, `status` (ja/teilweise/unsicher), `sources: [{file_key, file, page, quote, grounded}]`, `links`, `backlinks`, `also` (gleicher Begriff in anderen Kursen) |
| `GET /search?q=…[&course=ID]` | `{notes: [...], pages: [{file_key, file, page, course, text}]}` (FTS5, Folientext auf 300 Zeichen gekürzt) |
| `POST /chat` | Body `{"messages": [{"role": "user"/"assistant", "content": "…"}]}` (letzte 8, die letzte vom Nutzer). Antwort NDJSON, siehe unten |

## `/chat`-Stream

Eine JSON-Zeile je Ereignis:

```json
{"sources": [{"n": 1, "kind": "note", "id": 35, "label": "Begriff X (Kurs A)"},
             {"n": 7, "kind": "page", "file_key": "123456:/vorlesung-02.pdf", "page": 10, "label": "vorlesung-02.pdf S. 10 (Kurs A)"},
             {"n": 12, "kind": "events", "label": "ISIS-Termine"}]}
{"delta": "Begriff X ist "}
{"delta": "… [1]."}
{"stats": {"tokens": 90, "prompt": 2214, "tps": 25.7, "s": 8.1}}
{"done": true}
```

`{"error": "…"}` kann vor `done` kommen. Läuft schon eine Frage: HTTP 429.

## Beispiel

```sh
. /etc/isis/isis.env
H="Authorization: Bearer $ISIS_API_TOKEN"
curl -s -H "$H" localhost:8091/events | jq '.[] | select(.actionable==1) | .name'
curl -sN -H "$H" -H 'Content-Type: application/json' localhost:8091/chat \
  -d '{"messages":[{"role":"user","content":"Wann ist meine nächste Abgabe?"}]}'
```

Ein Dashboard sollte die API **serverseitig** aufrufen und den Token nicht an Browser ausliefern. Beispiel:
[`contrib/control-center`](../contrib/control-center).
