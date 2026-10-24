"""One sync pass: running courses, their files and links, calendar events → SQLite + files/.

Run by isis-sync.timer every 2 h. Files are only re-downloaded when ISIS reports a new
timemodified/filesize; vanished items are flagged `gone`, never deleted locally.
"""
import fcntl
import html
import os
import re
import sys
import time
from pathlib import Path

from . import db as dbm
from . import ics
from .moodle import STATE, WWWROOT, Client, MoodleError, TokenExpired

# Files, isis.ics and the vault go to DATA (e.g. an HDD mounted at /mnt/isis); token + DB stay in STATE.
# The marker guards against writing into an empty mount point when the disk is missing.
DATA = Path(os.environ.get("ISIS_DATA", str(STATE)))
FILES = DATA / "files"
MARKER = DATA / ".isis-data"
MAX_FILE = int(os.environ.get("ISIS_MAX_FILE_MB", "200")) * 2**20
SKIP = {int(x) for x in os.environ.get("ISIS_SKIP_COURSES", "").replace(",", " ").split()}
EVENT_PAST, EVENT_AHEAD = 30 * 86400, 180 * 86400


def _text(s):
    """ISIS names can carry markup (subsection titles come as <span …>…</span>)."""
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _part(name, maxlen=120):
    """One safe path component."""
    name = re.sub(r"[\x00-\x1f/\\:*?\"<>|]+", "_", _text(name)).strip(" .")
    return (name or "_")[:maxlen]


def _local_path(db, key, course, section, filepath, filename):
    rel = Path(f"{course['id']}_{_part(course['shortname'], 60)}", _part(section, 80),
               *[_part(p) for p in filepath.strip("/").split("/") if p], _part(filename, 180))
    other = db.execute("SELECT 1 FROM files WHERE local_path=? AND key<>?", (str(rel), key)).fetchone()
    if other:  # same name twice in one section → keep both
        rel = rel.with_name(f"{rel.stem} ({key.split(':')[0]}){rel.suffix}")
    return str(rel)


def sync_courses(c, db, now):
    res = c.call("core_course_get_enrolled_courses_by_timeline_classification", classification="inprogress", limit=0)
    courses = [k for k in res.get("courses", []) if k["id"] not in SKIP]
    for k in courses:
        db.execute("""INSERT INTO courses(id, shortname, fullname, startdate, enddate, synced_at) VALUES(?,?,?,?,?,?)
                      ON CONFLICT(id) DO UPDATE SET shortname=excluded.shortname, fullname=excluded.fullname,
                      startdate=excluded.startdate, enddate=excluded.enddate, synced_at=excluded.synced_at""",
                   (k["id"], _text(k["shortname"]), _text(k["fullname"]),
                    k.get("startdate"), k.get("enddate"), now))
    return courses


def sync_contents(c, db, course, now, stats):
    """Returns {(modname, instance): cmid} for linking calendar events to activities."""
    cms = {}
    seen_files, seen_links = set(), set()
    for sec in c.call("core_course_get_contents", courseid=course["id"]):
        section = _text(sec.get("name"))
        for m in sec.get("modules", []):
            cms[(m["modname"], m.get("instance"))] = m["id"]
            if not m.get("uservisible", True):
                continue
            for f in m.get("contents") or []:
                if f.get("type") == "url" and m["modname"] == "url":
                    seen_links.add(m["id"])
                    db.execute("""INSERT INTO links(cmid, courseid, section, name, url, first_seen) VALUES(?,?,?,?,?,?)
                                  ON CONFLICT(cmid) DO UPDATE SET section=excluded.section, name=excluded.name,
                                  url=excluded.url, gone=0""",
                               (m["id"], course["id"], section, _text(m["name"]), f.get("fileurl"), now))
                if f.get("type") != "file":
                    continue
                key = f"{m['id']}:{f.get('filepath') or '/'}{f['filename']}"
                seen_files.add(key)
                row = db.execute("SELECT timemodified, filesize, need_download FROM files WHERE key=?", (key,)).fetchone()
                if row is None:
                    stats["new_files"] += 1
                    db.execute("""INSERT INTO files(key, courseid, cmid, modname, section, module, filename, filesize,
                                  timemodified, mimetype, fileurl, local_path, first_seen)
                                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                               (key, course["id"], m["id"], m["modname"], section, _text(m["name"]),
                                f["filename"], f.get("filesize"), f.get("timemodified"), f.get("mimetype"),
                                f["fileurl"], _local_path(db, key, course, section, f.get("filepath") or "/", f["filename"]),
                                now))
                    continue
                changed = (row["timemodified"], row["filesize"]) != (f.get("timemodified"), f.get("filesize"))
                if changed:
                    stats["changed_files"] += 1
                db.execute("""UPDATE files SET section=?, module=?, filesize=?, timemodified=?, mimetype=?, fileurl=?,
                              gone=0, need_download=need_download OR ?, changed_at=CASE WHEN ? THEN ? ELSE changed_at END
                              WHERE key=?""",
                           (section, _text(m["name"]), f.get("filesize"), f.get("timemodified"),
                            f.get("mimetype"), f["fileurl"], changed, changed, now, key))
    for table, col, seen in (("files", "key", seen_files), ("links", "cmid", seen_links)):
        known = {r[0] for r in db.execute(f"SELECT {col} FROM {table} WHERE courseid=? AND gone=0", (course["id"],))}
        for gone in known - seen:
            db.execute(f"UPDATE {table} SET gone=1 WHERE {col}=?", (gone,))
    return cms


def sync_events(c, db, courses, cms, now, stats):
    ids = [k["id"] for k in courses]
    lo, hi = int(now) - EVENT_PAST, int(now) + EVENT_AHEAD
    res = c.call("core_calendar_get_calendar_events", events={"courseids": ids},
                 options={"timestart": lo, "timeend": hi, "userevents": 1, "siteevents": 0})
    action = c.call("core_calendar_get_action_events_by_timesort", timesortfrom=lo, timesortto=hi, limitnum=50)
    actionable = {e["id"]: e.get("url") for e in action.get("events", [])}
    seen = set()
    for e in res.get("events", []):
        if e.get("courseid") not in ids:
            continue
        seen.add(e["id"])
        cmid = cms.get((e.get("modulename"), e.get("instance"))) if e.get("modulename") else None
        url = actionable.get(e["id"]) or (f"{WWWROOT}/mod/{e['modulename']}/view.php?id={cmid}" if cmid
                                          else f"{WWWROOT}/course/view.php?id={e['courseid']}")
        vals = (e["courseid"], _text(e["name"]), e.get("description") or "", e.get("modulename"),
                e.get("eventtype"), e.get("instance"), cmid, e["timestart"], e.get("timeduration") or 0, url,
                int(e["id"] in actionable), e.get("timemodified"))
        row = db.execute("SELECT name, timestart, timeduration, gone FROM events WHERE id=?", (e["id"],)).fetchone()
        if row is None:
            stats["new_events"] += 1
            db.execute("""INSERT INTO events(courseid, name, description, modulename, eventtype, instance, cmid,
                          timestart, timeduration, url, actionable, timemodified, id, first_seen)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (*vals, e["id"], now))
            continue
        changed = (row["name"], row["timestart"], row["timeduration"], row["gone"]) != (vals[1], vals[7], vals[8], 0)
        if changed:
            stats["changed_events"] += 1
        db.execute("""UPDATE events SET courseid=?, name=?, description=?, modulename=?, eventtype=?, instance=?, cmid=?,
                      timestart=?, timeduration=?, url=?, actionable=?, timemodified=?, gone=0,
                      changed_at=CASE WHEN ? THEN ? ELSE changed_at END WHERE id=?""",
                   (*vals, changed, now, e["id"]))
    # only what should have come back in this window counts as removed
    marks = ",".join("?" * len(ids))
    for (eid,) in db.execute(f"""SELECT id FROM events WHERE gone=0 AND timestart BETWEEN ? AND ?
                                 AND courseid IN ({marks})""", (lo, hi, *ids)).fetchall():
        if eid not in seen:
            db.execute("UPDATE events SET gone=1, changed_at=? WHERE id=?", (now, eid))
            stats["changed_events"] += 1


def download_pending(c, db, stats, log):
    rows = db.execute("SELECT key, fileurl, local_path, filesize, filename FROM files WHERE need_download=1 AND gone=0").fetchall()
    for r in rows:
        if (r["filesize"] or 0) > MAX_FILE:
            db.execute("UPDATE files SET need_download=0, error=? WHERE key=?",
                       (f"größer als {MAX_FILE // 2**20} MB, nicht geladen", r["key"]))
            continue
        dest = FILES / r["local_path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            sha = c.download(r["fileurl"], dest)
        except TokenExpired:
            raise
        except Exception as e:  # one broken file must not stop the run; retried next time
            db.execute("UPDATE files SET error=? WHERE key=?", (str(e)[:300], r["key"]))
            stats["errors"] += 1
            log(f"  ! {r['filename']}: {e}")
            continue
        db.execute("UPDATE files SET sha256=?, need_download=0, error=NULL, downloaded_at=? WHERE key=?",
                   (sha, time.time(), r["key"]))
        db.commit()
        stats["downloaded"] += 1


def run(download=True, quiet=False):
    log = (lambda *a: None) if quiet else print
    STATE.mkdir(parents=True, exist_ok=True)
    lock = open(STATE / "sync.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("Ein anderer Sync läuft schon.")
        return 0
    db = dbm.connect()
    now = time.time()
    run_id = db.execute("INSERT INTO runs(started) VALUES(?)", (now,)).lastrowid
    db.commit()
    stats = dict(new_files=0, changed_files=0, new_events=0, changed_events=0, downloaded=0, errors=0)
    ok, msg = 0, ""
    try:
        c = Client()
        courses = sync_courses(c, db, now)
        cms = {}
        for k in courses:
            cms.update(sync_contents(c, db, k, now, stats))
            db.commit()
        sync_events(c, db, courses, cms, now, stats)
        db.commit()
        if DATA != STATE and not MARKER.exists():
            raise OSError(f"Datenablage {DATA} fehlt (Festplatte nicht eingehängt?) – nur Metadaten abgeglichen")
        if download:
            download_pending(c, db, stats, log)
        n_ics = ics.write(db, DATA / "isis.ics")
        ok = 1
        msg = (f"{len(courses)} Kurse, {stats['new_files']} neue / {stats['changed_files']} geänderte Dateien, "
               f"{stats['downloaded']} geladen, {stats['new_events']} neue / {stats['changed_events']} geänderte Termine"
               + f", isis.ics {n_ics}" + (f", {stats['errors']} Fehler" if stats["errors"] else ""))
    except TokenExpired as e:
        msg = f"token: {e}"
    except (MoodleError, OSError, ValueError) as e:
        msg = f"{type(e).__name__}: {e}"[:500]
    finally:
        db.execute("""UPDATE runs SET finished=?, ok=?, message=?, new_files=?, changed_files=?, new_events=?,
                      changed_events=? WHERE id=?""",
                   (time.time(), ok, msg, stats["new_files"], stats["changed_files"], stats["new_events"],
                    stats["changed_events"], run_id))
        db.execute("DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY id DESC LIMIT 500)")
        db.commit()
        db.close()
    log(("ok: " if ok else "FEHLER: ") + msg)
    return 0 if ok else (75 if msg.startswith("token:") else 1)


if __name__ == "__main__":
    sys.exit(run(quiet="--quiet" in sys.argv))
