"""Code-side merging: normalized title keys, near-duplicate check (jeff yes/no, asked once), edges."""
import re
import time
import unicodedata

from . import jeff

ARTICLES = {"der", "die", "das", "den", "dem", "des", "ein", "eine", "einer", "eines", "the", "a", "an"}
SUFFIXES = ("ern", "en", "er", "es", "e", "n", "s")


def key(title):
    s = unicodedata.normalize("NFKC", title).lower()
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    words = [w for w in re.split(r"[^a-z0-9]+", s) if w and w not in ARTICLES]
    out = []
    for w in words:  # light stemming, the same on both sides is all that matters
        for suf in SUFFIXES:
            if len(w) > 5 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        out.append(w)
    return " ".join(out)


def _lev(a, b, limit=2):
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _near(k, other):
    a, b = k.replace(" ", ""), other.replace(" ", "")
    if min(len(a), len(b)) >= 6 and (a in b or b in a):
        return True
    return len(a) >= 6 and _lev(a, b) <= 2


def find_node(db, courseid, item, k, stats):
    row = db.execute("SELECT id FROM nodes WHERE courseid=? AND key=?", (courseid, k)).fetchone()
    if row:
        return row["id"]
    cands = [r for r in db.execute("SELECT id, key, title, content FROM nodes WHERE courseid=? AND type=?",
                                   (courseid, item["type"])) if _near(k, r["key"])][:3]
    for r in cands:
        a, b = sorted((k, r["key"]))
        dec = db.execute("SELECT same FROM merge_decisions WHERE courseid=? AND key_a=? AND key_b=?",
                         (courseid, a, b)).fetchone()
        if dec is None:
            if not jeff.ENABLED:
                continue
            same = jeff.same(item, r) >= 0.5
            stats["jeff_same"] += 1
            db.execute("INSERT INTO merge_decisions VALUES(?,?,?,?)", (courseid, a, b, int(same)))
        else:
            same = bool(dec["same"])
        if same:
            stats["merged"] += 1
            return r["id"]
    return None


def upsert(db, courseid, item, file_key, chunk_sha, grounded, stats):
    k = key(item["title"])
    if not k:
        return None
    nid = find_node(db, courseid, item, k, stats)
    if nid is None:
        nid = db.execute("""INSERT INTO nodes(courseid, key, type, title, content, importance, first_seen)
                            VALUES(?,?,?,?,?,?,?)""",
                         (courseid, k, item["type"], item["title"], item["content"], item["importance"],
                          time.time())).lastrowid
        stats["new_nodes"] += 1
    else:
        db.execute("UPDATE nodes SET importance=max(importance, ?) WHERE id=?", (item["importance"], nid))
    db.execute("""INSERT OR IGNORE INTO sources(node_id, file_key, chunk_sha, page, quote, content, grounded)
                  VALUES(?,?,?,?,?,?,?)""", (nid, file_key, chunk_sha, item["page"], item["quote"], item["content"], grounded))
    for rel in item["related"]:
        rk = key(rel)
        if rk and rk != k:
            db.execute("INSERT OR IGNORE INTO edges(src, dst_key) VALUES(?,?)", (nid, rk))
    return nid


def resolve(db, courseid):
    """dst_key → node id, through merge decisions for keys that were folded into another node."""
    nodes = {r["key"]: r["id"] for r in db.execute("SELECT id, key FROM nodes WHERE courseid=?", (courseid,))}
    alias = {}
    for r in db.execute("SELECT key_a, key_b FROM merge_decisions WHERE courseid=? AND same=1", (courseid,)):
        a, b = r["key_a"], r["key_b"]
        if a in nodes and b not in nodes:
            alias[b] = nodes[a]
        elif b in nodes and a not in nodes:
            alias[a] = nodes[b]
    return lambda k: nodes.get(k) or alias.get(k)


def cleanup(db, file_key, current_shas):
    """A file changed: drop what came from its old chunks, then nodes nothing supports any more."""
    marks = ",".join("?" * len(current_shas)) or "''"
    db.execute(f"DELETE FROM sources WHERE file_key=? AND chunk_sha NOT IN ({marks})", (file_key, *current_shas))
    db.execute(f"DELETE FROM chunks WHERE file_key=? AND sha NOT IN ({marks})", (file_key, *current_shas))
    db.execute("DELETE FROM nodes WHERE id NOT IN (SELECT node_id FROM sources)")
