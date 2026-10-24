"""Read-only HTTP API for a dashboard (isis-api, port 8091).

Only the hosts in ISIS_API_ALLOW may call it, with a bearer token. It serves what the sync and the
knowledge net already stored (courses, dates, files, notes) and answers questions about it: /chat looks the
question up in the notes and in the slide text (SQLite FTS5) and lets the 9B answer from those hits only.
Nothing here writes to ISIS, the calendar or the vault; the only file it writes is its own search index.
"""
import json
import logging
import mimetypes
import os
import re
import sqlite3
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from . import db as dbm
from .knowledge import chunk as chunkm
from .knowledge import merge
from .knowledge.extract import MODEL, OLLAMA
from .moodle import STATE, TOKEN_FILE, WWWROOT
from .sync import DATA, FILES

TOKEN = os.environ.get("ISIS_API_TOKEN", "").strip()
ALLOWED = {ip for ip in os.environ.get("ISIS_API_ALLOW", "127.0.0.1").split(",") if ip}
PORT = int(os.environ.get("ISIS_API_PORT", "8091"))
INDEX_PATH = STATE / "search.db"
SEMESTER = re.compile(r"^\[[^\]]*\]\s*")
STOP = set("""aber alle allem als also auch auf aus bei beim bin bis bist dann das dass dem den der des die dies diese
dieser dir doch dort durch ein eine einem einen einer eines erkl erklär erkläre erklaere für gibt hab habe hat ich ihr
im in ist kann kannst mal man mein meine mir mit nach nicht noch nur oder sich sie sind so über um und uns unter vom von
vor was welche welcher welches wenn wer wie wird wir wo zu zum zur bitte genau eigentlich""".split())
CHAT_LOCK = threading.Semaphore(1)
INDEX_LOCK = threading.Lock()
logging.getLogger("pypdf").setLevel(logging.ERROR)  # font warnings on every slide deck


def short(name):
    return SEMESTER.sub("", name or "")


def db():
    return dbm.connect()


def node_status(srcs):
    """Same rule as the vault: ja / teilweise / unsicher from jeff's verdicts."""
    v = [(s["grounded"] or "").split(" ")[0] for s in srcs]
    return "ja" if "supported" in v else "teilweise" if "partly_supported" in v else "unsicher" if any(v) else "ja"


# ---------------------------------------------------------------- search index (pages + notes)
def _index():
    ix = sqlite3.connect(INDEX_PATH, timeout=30, check_same_thread=False)
    ix.executescript("""
        CREATE TABLE IF NOT EXISTS indexed (file_key TEXT PRIMARY KEY, sha256 TEXT, pages INTEGER);
        CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(file_key UNINDEXED, page UNINDEXED, text,
            tokenize='unicode61 remove_diacritics 2');
        CREATE VIRTUAL TABLE IF NOT EXISTS notes USING fts5(node_id UNINDEXED, title, body,
            tokenize='unicode61 remove_diacritics 2');
        CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);""")
    return ix


def refresh_index(d):
    """Re-reads files whose sha256 changed and rebuilds the note table when the net changed. Cheap when nothing did."""
    with INDEX_LOCK:
        ix = _index()
        have = {r[0]: r[1] for r in ix.execute("SELECT file_key, sha256 FROM indexed")}
        files = {r["key"]: r for r in d.execute(
            "SELECT key, local_path, sha256, mimetype FROM files WHERE gone=0 AND local_path IS NOT NULL AND sha256 IS NOT NULL")}
        for k in set(have) - set(files):
            ix.execute("DELETE FROM pages WHERE file_key=?", (k,))
            ix.execute("DELETE FROM indexed WHERE file_key=?", (k,))
        for k, f in files.items():
            if have.get(k) == f["sha256"] or not str(f["local_path"]).lower().endswith((".pdf", ".html", ".htm")):
                continue
            try:
                pl = chunkm.pages(FILES / f["local_path"])
            except Exception as e:  # noqa: BLE001 - a broken PDF must not stop the rest
                print(f"index: {k}: {e}", flush=True)
                pl = []
            ix.execute("DELETE FROM pages WHERE file_key=?", (k,))
            ix.executemany("INSERT INTO pages(file_key, page, text) VALUES (?,?,?)",
                           [(k, n, t) for n, t in pl if len(t) >= 40])
            ix.execute("INSERT OR REPLACE INTO indexed VALUES (?,?,?)", (k, f["sha256"], len(pl)))
        sig = "/".join(str(x) for x in d.execute(
            "SELECT count(*), max(id), (SELECT count(*) FROM sources) FROM nodes").fetchone())
        if (ix.execute("SELECT v FROM meta WHERE k='notes'").fetchone() or [None])[0] != sig:
            ix.execute("DELETE FROM notes")
            for n in d.execute("SELECT id, title, content FROM nodes"):
                quotes = " ".join(r[0] for r in d.execute("SELECT quote FROM sources WHERE node_id=?", (n["id"],)))
                ix.execute("INSERT INTO notes VALUES (?,?,?)", (n["id"], n["title"], f"{n['content']} {quotes}"))
            ix.execute("INSERT OR REPLACE INTO meta VALUES ('notes', ?)", (sig,))
        ix.commit()
        return ix


def fts_query(text):
    words = [w for w in re.findall(r"\w+", text.lower()) if len(w) >= 3 and w not in STOP and not w.isdigit()]
    terms = []
    for w in dict.fromkeys(words):
        terms.append(f'"{w}"' if len(w) < 5 else f'"{w[:max(5, len(w) - 2)]}"*')
    return " OR ".join(terms[:12])


def search(d, text, n_notes=6, n_pages=5, course=None):
    q = fts_query(text)
    if not q:
        return [], []
    ix = refresh_index(d)
    try:
        return _search(d, ix, q, n_notes, n_pages, course)
    finally:
        ix.close()


def _search(d, ix, q, n_notes, n_pages, course):
    names = {r["key"]: (r["filename"], r["courseid"]) for r in d.execute("SELECT key, filename, courseid FROM files")}
    shorts = {r["id"]: short(r["shortname"]) for r in d.execute("SELECT id, shortname FROM courses")}
    notes = []
    for nid, rank in ix.execute("SELECT node_id, bm25(notes, 0, 4.0, 1.0) FROM notes WHERE notes MATCH ? ORDER BY 2 LIMIT 40", (q,)):
        n = d.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone()
        if n and (course is None or n["courseid"] == course):
            notes.append({"id": n["id"], "title": n["title"], "type": n["type"], "content": n["content"],
                          "course": shorts.get(n["courseid"], ""), "courseid": n["courseid"]})
        if len(notes) >= n_notes:
            break
    pages = []
    for fk, page, txt in ix.execute("SELECT file_key, page, text FROM pages WHERE pages MATCH ? ORDER BY bm25(pages) LIMIT 40", (q,)):
        fn, cid = names.get(fk, (fk, None))
        if course is not None and cid != course:
            continue
        pages.append({"file_key": fk, "page": page, "file": fn, "course": shorts.get(cid, ""), "text": txt})
        if len(pages) >= n_pages:
            break
    return notes, pages


# ---------------------------------------------------------------- read endpoints
def status(d):
    last = d.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    last_ok = d.execute("SELECT finished FROM runs WHERE ok=1 ORDER BY id DESC LIMIT 1").fetchone()
    now = time.time()
    c = lambda sql, *a: d.execute(sql, a).fetchone()[0]  # noqa: E731
    return {
        "token": TOKEN_FILE.exists(),
        "last_run": dict(last) if last else None,
        "last_ok": last_ok[0] if last_ok else None,
        "courses": c("SELECT count(*) FROM courses"),
        "files": c("SELECT count(*) FROM files WHERE gone=0"),
        "files_local": c("SELECT count(*) FROM files WHERE gone=0 AND local_path IS NOT NULL AND need_download=0"),
        "files_error": c("SELECT count(*) FROM files WHERE gone=0 AND error IS NOT NULL"),
        "bytes": c("SELECT coalesce(sum(filesize),0) FROM files WHERE gone=0"),
        "events_upcoming": c("SELECT count(*) FROM events WHERE gone=0 AND timestart>=?", now),
        "open_tasks": c("SELECT count(*) FROM events WHERE gone=0 AND actionable=1 AND timestart>=?", now),
        "nodes": c("SELECT count(*) FROM nodes"),
        "edges": c("SELECT count(*) FROM edges"),
        "chunks": c("SELECT count(*) FROM chunks WHERE status='ok'"),
        "chunks_error": c("SELECT count(*) FROM chunks WHERE status='error'"),
        "wissen_last": c("SELECT max(done_at) FROM chunks"),
        "ics": (DATA / "isis.ics").exists(),
        "model": MODEL,
    }


def courses(d):
    now = time.time()
    out = []
    for r in d.execute("SELECT * FROM courses ORDER BY shortname"):
        q = lambda sql: d.execute(sql, (r["id"],)).fetchone()[0]  # noqa: E731
        out.append({"id": r["id"], "short": short(r["shortname"]), "name": short(r["fullname"]),
                    "url": f"{WWWROOT}/course/view.php?id={r['id']}",
                    "files": q("SELECT count(*) FROM files WHERE gone=0 AND courseid=?"),
                    "links": q("SELECT count(*) FROM links WHERE gone=0 AND courseid=?"),
                    "nodes": q("SELECT count(*) FROM nodes WHERE courseid=?"),
                    "next": (lambda x: dict(x) if x else None)(d.execute(
                        "SELECT name, timestart, actionable FROM events WHERE gone=0 AND courseid=? AND timestart>=? "
                        "ORDER BY timestart LIMIT 1", (r["id"], now)).fetchone())})
    return out


def events(d, past_days=7):
    shorts = {r["id"]: short(r["shortname"]) for r in d.execute("SELECT id, shortname FROM courses")}
    rows = d.execute("SELECT id, courseid, name, modulename, eventtype, timestart, timeduration, url, actionable "
                     "FROM events WHERE gone=0 AND timestart>=? ORDER BY timestart", (time.time() - past_days * 86400,))
    return [dict(r, course=shorts.get(r["courseid"], "")) for r in rows]


def files(d, course):
    rows = d.execute("""SELECT key, section, module, modname, filename, filesize, timemodified, mimetype, local_path,
                               need_download, error, first_seen, changed_at FROM files WHERE gone=0 AND courseid=?
                        ORDER BY section, module, filename""", (course,)).fetchall()
    links = d.execute("SELECT section, name, url FROM links WHERE gone=0 AND courseid=? ORDER BY section, name", (course,)).fetchall()
    out = []
    for r in rows:
        x = dict(r)
        x["local"] = bool(r["local_path"]) and not r["need_download"]
        x.pop("local_path")
        out.append(x)
    return {"files": out, "links": [dict(r) for r in links]}


def vault(d, course):
    out = []
    for n in d.execute("SELECT * FROM nodes WHERE courseid=? ORDER BY importance DESC, title", (course,)):
        srcs = d.execute("SELECT grounded FROM sources WHERE node_id=?", (n["id"],)).fetchall()
        out.append({"id": n["id"], "title": n["title"], "type": n["type"], "importance": n["importance"],
                    "status": node_status(srcs), "sources": len(srcs)})
    resolve = merge.resolve(d, course)
    ids = {n["id"] for n in out}
    edges = set()
    for e in d.execute("SELECT src, dst_key FROM edges WHERE src IN (SELECT id FROM nodes WHERE courseid=?)", (course,)):
        dst = resolve(e["dst_key"])
        if dst in ids and dst != e["src"]:
            edges.add(tuple(sorted((e["src"], dst))))
    return {"nodes": out, "edges": sorted(edges)}


def node(d, nid):
    n = d.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone()
    if not n:
        return None
    names = {r["key"]: r["filename"] for r in d.execute("SELECT key, filename FROM files")}
    srcs = d.execute("SELECT file_key, page, quote, content, grounded FROM sources WHERE node_id=? ORDER BY file_key, page",
                     (nid,)).fetchall()
    resolve = merge.resolve(d, n["courseid"])
    title = lambda i: d.execute("SELECT id, title, type FROM nodes WHERE id=?", (i,)).fetchone()  # noqa: E731
    links, seen = [], set()
    for (k,) in d.execute("SELECT dst_key FROM edges WHERE src=?", (nid,)):
        dst = resolve(k)
        if dst and dst != nid and dst not in seen and (t := title(dst)):
            seen.add(dst)
            links.append(dict(t))
    back = []
    for e in d.execute("SELECT src, dst_key FROM edges WHERE src IN (SELECT id FROM nodes WHERE courseid=?)", (n["courseid"],)):
        if e["src"] != nid and resolve(e["dst_key"]) == nid and e["src"] not in {b["id"] for b in back} and (t := title(e["src"])):
            back.append(dict(t))
    other = [dict(r, course=short(r["shortname"])) for r in d.execute(
        "SELECT n.id, n.title, c.shortname FROM nodes n JOIN courses c ON c.id=n.courseid WHERE n.key=? AND n.id!=?", (n["key"], nid))]
    course = d.execute("SELECT shortname FROM courses WHERE id=?", (n["courseid"],)).fetchone()
    return {**dict(n), "course": short(course[0]) if course else "", "status": node_status(srcs),
            "sources": [dict(s, file=names.get(s["file_key"], s["file_key"])) for s in srcs],
            "links": links, "backlinks": back, "also": other}


def file_path(d, key):
    r = d.execute("SELECT local_path, filename, mimetype FROM files WHERE key=? AND local_path IS NOT NULL", (key,)).fetchone()
    if not r:
        return None
    p = (FILES / r["local_path"]).resolve()
    if not p.is_relative_to(FILES.resolve()) or not p.is_file():
        return None
    return p, r["filename"], r["mimetype"] or mimetypes.guess_type(r["filename"])[0] or "application/octet-stream"


# ---------------------------------------------------------------- chat
SYSTEM = """Du bist der Lernassistent für das Studium an der TU Berlin. Antworte auf Deutsch, knapp und sachlich, und duze den Nutzer.
Nutze NUR die Angaben unter KONTEXT (Notizen aus dem Wissensnetz, Folientext, ISIS-Termine). Belege jede Aussage mit der
Nummer der Quelle in eckigen Klammern, z. B. [2]. Steht die Antwort nicht im Kontext, sag das ehrlich und rate nicht.
Termine immer mit Wochentag und Datum nennen. Formeln so wiedergeben, wie sie im Kontext stehen, ohne LaTeX (kein $)."""


def _fmt_ts(t):
    return time.strftime("%a %d.%m.%Y %H:%M", time.localtime(t)).replace("Mon", "Mo").replace("Tue", "Di").replace(
        "Wed", "Mi").replace("Thu", "Do").replace("Fri", "Fr").replace("Sat", "Sa").replace("Sun", "So")


def build_context(d, messages):
    users = [m["content"] for m in messages if m["role"] == "user"][-2:]
    notes, pages = search(d, " ".join(users))
    refs, parts = [], []
    for n in notes:
        refs.append({"n": len(refs) + 1, "kind": "note", "id": n["id"], "label": f"{n['title']} ({n['course']})"})
        parts.append(f"[{len(refs)}] Notiz „{n['title']}“ ({n['course']}, {n['type']}): {n['content']}")
    budget = 7000
    for p in pages:
        txt = re.sub(r"\s+", " ", p["text"])[:1400]
        if budget - len(txt) < 0:
            break
        budget -= len(txt)
        refs.append({"n": len(refs) + 1, "kind": "page", "file_key": p["file_key"], "page": p["page"],
                     "label": f"{p['file']} S. {p['page']} ({p['course']})"})
        parts.append(f"[{len(refs)}] Folie {p['file']} S. {p['page']} ({p['course']}): {txt}")
    ev = [e for e in events(d, past_days=0)][:12]
    if ev:
        refs.append({"n": len(refs) + 1, "kind": "events", "label": "ISIS-Termine"})
        lines = [f"- {_fmt_ts(e['timestart'])}: {e['course']}: {e['name']}" + (" (Abgabe offen)" if e["actionable"] else "")
                 for e in ev]
        parts.append(f"[{len(refs)}] Kommende ISIS-Termine:\n" + "\n".join(lines))
    ctx = f"Heute ist {_fmt_ts(time.time())[:-6]}.\n\nKONTEXT:\n" + "\n\n".join(parts)
    return ctx, refs


def clean_messages(raw):
    if not isinstance(raw, list) or not raw:
        raise ValueError("keine Nachrichten")
    out = []
    for m in raw[-8:]:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") or not isinstance(m.get("content"), str):
            raise ValueError("ungültige Nachricht")
        out.append({"role": m["role"], "content": m["content"][:2000]})
    if out[-1]["role"] != "user":
        raise ValueError("letzte Nachricht muss vom Nutzer sein")
    return out


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "isis-api"
    sys_version = ""

    def send(self, code, obj, ctype="application/json; charset=utf-8"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def guard(self):
        if self.client_address[0] not in ALLOWED:
            self.send(403, {"error": "forbidden"})
            return False
        if not TOKEN or self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self.send(401, {"error": "bad token"})
            return False
        return True

    def do_GET(self):
        if not self.guard():
            return
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        num = lambda k: int(q[k]) if re.fullmatch(r"\d{1,9}", q.get(k, "")) else None  # noqa: E731
        d = db()
        try:
            if u.path == "/status":
                return self.send(200, status(d))
            if u.path == "/courses":
                return self.send(200, courses(d))
            if u.path == "/events":
                return self.send(200, events(d))
            if u.path == "/files" and num("course"):
                return self.send(200, files(d, num("course")))
            if u.path == "/vault" and num("course"):
                return self.send(200, vault(d, num("course")))
            if u.path == "/node" and num("id"):
                n = node(d, num("id"))
                return self.send(200, n) if n else self.send(404, {"error": "Notiz nicht gefunden"})
            if u.path == "/search" and q.get("q"):
                notes, pages = search(d, q["q"][:300], n_notes=20, n_pages=15, course=num("course"))
                for p in pages:
                    p["text"] = re.sub(r"\s+", " ", p["text"])[:300]
                return self.send(200, {"notes": notes, "pages": pages})
            if u.path == "/file" and q.get("key"):
                f = file_path(d, q["key"])
                if not f:
                    return self.send(404, {"error": "Datei nicht vorhanden"})
                p, name, ctype = f
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(p.stat().st_size))
                self.send_header("Content-Disposition", "inline; filename*=UTF-8''" + urllib.parse.quote(name))
                self.end_headers()
                with open(p, "rb") as fh:
                    while b := fh.read(1 << 16):
                        self.wfile.write(b)
                return
            return self.send(404, {"error": "unbekannt"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            return self.send(500, {"error": str(e)})
        finally:
            d.close()

    def do_POST(self):
        if not self.guard():
            return
        if self.path != "/chat":
            return self.send(404, {"error": "unbekannt"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 40_000:
                raise ValueError("Anfrage zu groß")
            messages = clean_messages(json.loads(self.rfile.read(n) or b"{}").get("messages"))
        except (ValueError, AttributeError, json.JSONDecodeError) as e:
            return self.send(400, {"error": str(e)})
        if not CHAT_LOCK.acquire(blocking=False):
            return self.send(429, {"error": "Es läuft schon eine Frage – bitte kurz warten."})
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            emit = lambda o: (self.wfile.write(json.dumps(o, ensure_ascii=False).encode() + b"\n"), self.wfile.flush())  # noqa: E731
            d = db()
            try:
                ctx, refs = build_context(d, messages)
            finally:
                d.close()
            emit({"sources": refs})
            msgs = [{"role": "system", "content": SYSTEM}, *messages[:-1],
                    {"role": "user", "content": f"{ctx}\n\nFRAGE: {messages[-1]['content']}"}]
            t0, tokens = time.time(), 0
            try:
                with httpx.stream("POST", f"{OLLAMA}/api/chat", timeout=httpx.Timeout(600, connect=10), json={
                        "model": MODEL, "messages": msgs, "stream": True, "think": False,
                        "options": {"temperature": 0.2, "num_predict": 900}}) as r:
                    r.raise_for_status()
                    for line in r.iter_lines():
                        if not line:
                            continue
                        ev = json.loads(line)
                        if ev.get("message", {}).get("content"):
                            emit({"delta": ev["message"]["content"]})
                        if ev.get("done"):
                            tokens = ev.get("eval_count") or 0
                            dur = (ev.get("eval_duration") or 0) / 1e9
                            emit({"stats": {"tokens": tokens, "prompt": ev.get("prompt_eval_count"),
                                            "tps": round(tokens / dur, 1) if dur else None, "s": round(time.time() - t0, 1)}})
            except httpx.HTTPError as e:
                emit({"error": f"KI (Ollama) nicht erreichbar: {e}"})
            emit({"done": True})
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            CHAT_LOCK.release()

    def log_message(self, fmt, *args):
        if args and str(args[1])[:1] not in "23":
            print(f"{self.client_address[0]} {fmt % args}", flush=True)


def main():
    if not TOKEN:
        raise SystemExit("ISIS_API_TOKEN fehlt in /etc/isis/isis.env")
    try:
        refresh_index(db()).close()
    except Exception as e:  # noqa: BLE001 - the HDD may be missing; the API still answers status requests
        print(f"index: {e}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
