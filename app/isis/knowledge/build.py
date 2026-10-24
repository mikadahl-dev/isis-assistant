"""`isis wissen build`: course files → chunks → 9B extraction → quote check → jeff grounding → merge → vault."""
import fcntl
import fnmatch
import json
import os
import unicodedata
import re
import sys
import time

from .. import db as dbm
from ..moodle import STATE
from ..sync import DATA, FILES, MARKER, SKIP
from . import chunk, extract, jeff, merge, vault

FILE_SKIP = [p.strip() for p in os.environ.get(
    "ISIS_WISSEN_SKIP", "").split(",") if p.strip()]


def _files(db, course=None, file_key=None):
    q = """SELECT f.*, c.shortname FROM files f JOIN courses c ON c.id=f.courseid
           WHERE f.gone=0 AND f.sha256 IS NOT NULL AND f.need_download=0"""
    args = []
    if course:
        q += " AND f.courseid=?"
        args.append(course)
    if file_key:
        q += " AND f.key=?"
        args.append(file_key)
    out = []
    for r in db.execute(q + " ORDER BY f.courseid, f.section, f.filename", args):
        name = r["filename"].lower()
        if r["courseid"] in SKIP or not name.endswith((".pdf", ".html", ".htm")):
            continue
        if any(fnmatch.fnmatch(r["filename"], p) for p in FILE_SKIP):
            continue
        out.append(r)
    return out


def _known(db, courseid):
    return [r["title"] for r in db.execute("""SELECT n.title FROM nodes n LEFT JOIN sources s ON s.node_id=n.id
                                              WHERE n.courseid=? GROUP BY n.id
                                              ORDER BY n.importance DESC, count(s.node_id) DESC LIMIT 80""", (courseid,))]


def _sources(item, c, page_text, filename, n=3, per_page=1500):
    """The quote's page first, then the chunk pages sharing most words with the item (the definition is often
    one slide away from where the model quoted it). ground was trained on 1-6 passages of 30-300 words."""
    words = {w for w in re.findall(r"\w{4,}", extract.norm(item["title"] + " " + item["content"]))}
    others = sorted({p for p, _ in c["pages"]} - {item["page"]},
                    key=lambda p: -len(words & set(re.findall(r"\w{4,}", extract.norm(page_text[p])))))
    pages = [item["page"]] + others[: n - 1]
    return "\n\n".join(f"[{i}] {filename}, Seite {p}\n{page_text[p][:per_page]}" for i, p in enumerate(pages, 1))


def process_file(db, f, stats, log, recheck=False):
    page_list = chunk.pages(FILES / f["local_path"])
    cs = chunk.chunks(page_list)
    page_text = {n: unicodedata.normalize("NFKC", t) for n, t in page_list}  # jeff reads 𝑊 better as W
    course = re.sub(r"^(\[[^\]]*\]\s*)+", "", f["shortname"])
    for i, c in enumerate(cs):
        done = db.execute("SELECT status, raw, tok_in, tok_out, took FROM chunks WHERE file_key=? AND sha=?",
                          (f["key"], c["sha"])).fetchone()
        if done and done["status"] == "ok" and not (recheck and done["raw"]):
            stats["cached"] += 1
            continue
        try:
            if done and done["raw"]:  # --recheck: the model's old answer, only the checks run again
                raw, tin, tout, took = json.loads(done["raw"]), done["tok_in"], done["tok_out"], done["took"]
                db.execute("DELETE FROM sources WHERE file_key=? AND chunk_sha=?", (f["key"], c["sha"]))
                stats["rechecked"] += 1
            else:
                raw, tin, tout, took = extract.extract(c, course, f["filename"], _known(db, f["courseid"]))
                stats["calls"] += 1
            kept, dropped = extract.verify(raw, c)
            for why, it in dropped:
                log(f"    – {why:<19} {it.get('title')}: „{(it.get('quote') or '')[:70]}“")
            n_keep = n_doubt = 0
            for it in kept:
                if it["type"] == "orga":
                    continue
                grounded = None
                if jeff.ENABLED:
                    p = jeff.grounding(_sources(it, c, page_text, f["filename"]), f"{it['title']}: {it['content']}")
                    stats["jeff_ground"] += 1
                    verdict = max(p, key=p.get)
                    grounded = f"{verdict} {p[verdict]:.2f}"
                    if verdict != "supported":
                        n_doubt += 1
                        log(f"    ? jeff: {grounded}  {it['title']}: {it['content'][:70]}")
                merge.upsert(db, f["courseid"], it, f["key"], c["sha"], grounded, stats)
                n_keep += 1
            stats["items"] += n_keep
            stats["doubt"] += n_doubt
            stats["dropped_quote"] += len(dropped)
            db.execute("""INSERT OR REPLACE INTO chunks(file_key, sha, idx, pages, status, n_items, n_dropped, tok_in,
                          tok_out, took, error, done_at, raw) VALUES(?,?,?,?, 'ok', ?,?,?,?,?, NULL, ?, ?)""",
                       (f["key"], c["sha"], i, c["label"], n_keep, len(dropped), tin, tout, took, time.time(),
                        json.dumps(raw, ensure_ascii=False)))
            log(f"  S. {c['label']:>6}: {n_keep} Einträge ({n_doubt} von jeff angezweifelt), {len(dropped)} ohne Beleg "
                f"verworfen, {took:.0f}s, {tin}+{tout} Token")
        except (extract.ModelError, jeff.JeffError, OSError) as e:
            stats["errors"] += 1
            db.execute("""INSERT OR REPLACE INTO chunks(file_key, sha, idx, pages, status, error, done_at)
                          VALUES(?,?,?,?, 'error', ?, ?)""", (f["key"], c["sha"], i, c["label"], str(e)[:300], time.time()))
            log(f"  S. {c['label']:>6}: FEHLER {e}")
            if isinstance(e, jeff.JeffError) or "Connection" in str(e):
                db.commit()
                raise
        db.commit()
    merge.cleanup(db, f["key"], [c["sha"] for c in cs])
    db.commit()


def run(course=None, file_key=None, quiet=False, recheck=False):
    log = (lambda *a: None) if quiet else print
    lock = open(STATE / "wissen.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("Ein anderer Aufbau läuft schon.")
        return 0
    if DATA != STATE and not MARKER.exists():
        log(f"Datenablage {DATA} fehlt (Festplatte nicht eingehängt?)")
        return 1
    db = dbm.connect()
    stats = dict(calls=0, cached=0, rechecked=0, items=0, dropped_quote=0, doubt=0, jeff_ground=0, jeff_same=0,
                 merged=0, new_nodes=0, errors=0)
    t0 = time.monotonic()
    rc = 0
    try:
        for f in _files(db, course, file_key):
            log(f"{f['shortname']} / {f['filename']}")
            process_file(db, f, stats, log, recheck)
    except (jeff.JeffError, extract.ModelError, OSError) as e:
        log(f"Abbruch: {e}")
        rc = 1
    n, written, removed = vault.write(db, DATA)
    log(f"\n{stats['calls']} KI-Aufrufe ({stats['cached']} Häppchen schon fertig, {stats['rechecked']} neu geprüft), "
        f"{time.monotonic() - t0:.0f}s · {stats['items']} Einträge übernommen ({stats['doubt']} von jeff angezweifelt), "
        f"{stats['dropped_quote']} ohne Zitat-Beleg verworfen · {stats['new_nodes']} neue Notizen, "
        f"{stats['merged']} zusammengeführt ({stats['jeff_same']} jeff-Fragen) · Vault: {n} Notizen, {written} geschrieben, "
        f"{removed} entfernt" + (f" · {stats['errors']} Fehler" if stats["errors"] else ""))
    db.close()
    return rc or (1 if stats["errors"] else 0)


if __name__ == "__main__":
    sys.exit(run(quiet="--quiet" in sys.argv))
