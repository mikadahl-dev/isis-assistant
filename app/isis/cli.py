"""`isis` command (Schritt 1: Verbindung)."""
import argparse
import getpass
import datetime as dt
import html
import sys
import time
import warnings

from . import db as dbm
from . import icloud, sync
from .knowledge import build as wissen
from .moodle import Client, MoodleError, TokenExpired, make_login_url, redeem

NEED_LOGIN = "Token fehlt oder ist abgelaufen → `isis login` ausführen"


def _ts(t):
    return dt.datetime.fromtimestamp(t).strftime("%a %d.%m.%Y %H:%M") if t else "–"


def cmd_login(a):
    if a.redirect:
        redeem(a.redirect)
        info = Client().call("core_webservice_get_site_info")
        print(f"Token gespeichert. Angemeldet als {info['fullname']} (userid {info['userid']}).")
        return
    print("1. Diese Adresse im Browser öffnen und mit TUB-Account + 2FA anmelden:\n")
    print("   " + make_login_url() + "\n")
    print("2. Am Ende will der Browser 'moodlemobile://token=…' öffnen (Fehlermeldung ist normal).")
    print("   Diese Adresse kopieren (Firefox: Adresszeile/Konsole, Chrome: F12 → Netzwerk, letzte Weiterleitung)")
    print("3. isis login 'moodlemobile://token=…'")


def cmd_whoami(a):
    info = Client().call("core_webservice_get_site_info")
    fns = {f["name"] for f in info.get("functions", [])}
    print(f"{info['fullname']} ({info['username']}, userid {info['userid']}) – {info['sitename']}, Moodle {info['release']}")
    for fn in ("core_course_get_contents", "mod_assign_get_assignments",
               "core_calendar_get_action_events_by_timesort", "core_enrol_get_users_courses"):
        print(f"  {'✓' if fn in fns else '✗'} {fn}")
    print(f"  {len(fns)} Funktionen freigegeben")


def cmd_courses(a):
    # "inprogress" is ISIS' own classification (dashboard "Laufend"); enddate is often unset
    c = Client()
    res = c.call("core_course_get_enrolled_courses_by_timeline_classification",
                 classification="all" if a.all else "inprogress", limit=0)
    for k in sorted(res.get("courses", []), key=lambda k: -(k.get("startdate") or 0)):
        print(f"{k['id']:>7}  {html.unescape(k['fullname'])}")


def cmd_deadlines(a):
    c = Client()
    now = int(time.time())
    res = c.call("core_calendar_get_action_events_by_timesort",
                 timesortfrom=now, timesortto=now + a.days * 86400, limitnum=50)
    events = res.get("events", [])
    if not events:
        print(f"Keine Termine/Abgaben in den nächsten {a.days} Tagen.")
    for e in events:
        course = html.unescape((e.get("course") or {}).get("shortname", ""))
        print(f"{_ts(e['timesort'])}  [{e.get('modulename') or e.get('eventtype')}] {html.unescape(e['name'])}  – {course}")


def cmd_files(a):
    for sec in Client().call("core_course_get_contents", courseid=a.courseid):
        for mod in sec.get("modules", []):
            for f in mod.get("contents", []) or []:
                if f.get("type") == "file":
                    print(f"{_ts(f.get('timemodified'))}  {f.get('filesize', 0) // 1024:>7} KB  "
                          f"{sec['name']} / {f['filename']}\n      {f['fileurl']}")


def cmd_download(a):
    print(Client().download(a.fileurl, a.dest), a.dest)


def cmd_sync(a):
    sys.exit(sync.run(download=not a.no_download))


def cmd_status(a):
    db = dbm.connect()
    for r in db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (a.n,)):
        took = f"{r['finished'] - r['started']:.0f}s" if r["finished"] else "läuft"
        print(f"{_ts(r['started'])}  {'ok ' if r['ok'] else 'ERR'}  {took:>5}  {r['message'] or ''}")
    f = db.execute("""SELECT count(*), coalesce(sum(filesize),0), sum(need_download), sum(error IS NOT NULL)
                      FROM files WHERE gone=0""").fetchone()
    e = db.execute("SELECT count(*), sum(actionable) FROM events WHERE gone=0 AND timestart > strftime('%s','now')").fetchone()
    print(f"\n{f[0]} Dateien ({f[1] // 2**20} MB), {f[2] or 0} offen, {f[3] or 0} mit Fehler · "
          f"{e[0]} kommende Termine, {e[1] or 0} davon mit offener Abgabe/Aufgabe")
    for r in db.execute("SELECT filename, error FROM files WHERE gone=0 AND error IS NOT NULL"):
        print(f"  ! {r['filename']}: {r['error']}")


def cmd_new(a):
    db = dbm.connect()
    since = time.time() - a.days * 86400
    print(f"Seit {_ts(since)}:")
    for r in db.execute("""SELECT f.*, c.shortname FROM files f JOIN courses c ON c.id=f.courseid
                           WHERE max(f.first_seen, coalesce(f.changed_at,0)) >= ? ORDER BY c.shortname, f.section""",
                        (since,)):
        tag = "geändert" if (r["changed_at"] or 0) >= since and r["changed_at"] != r["first_seen"] else "neu"
        print(f"  [{tag:8}] {r['shortname']} / {r['section']} / {r['filename']}" + ("  (entfernt)" if r["gone"] else ""))
    for r in db.execute("""SELECT e.*, c.shortname FROM events e LEFT JOIN courses c ON c.id=e.courseid
                           WHERE max(e.first_seen, coalesce(e.changed_at,0)) >= ? AND e.eventtype <> 'course'
                           ORDER BY e.timestart""", (since,)):
        print(f"  [termin  ] {_ts(r['timestart'])}  {r['name']} – {r['shortname']}" + ("  (entfernt)" if r["gone"] else ""))


def cmd_set_icloud(a):
    if not sys.stdin.isatty():
        sys.exit("Bitte interaktiv aufrufen (ssh isis → isis set-icloud), das Passwort wird verdeckt abgefragt.")
    apple_id = input("Apple-ID (E-Mail): ").strip()
    warnings.simplefilter("error", getpass.GetPassWarning)  # never fall back to echoing the password
    try:
        pw = getpass.getpass("App-spezifisches Passwort (Eingabe unsichtbar): ").strip().replace(" ", "")
    except getpass.GetPassWarning:
        sys.exit("Verdeckte Eingabe nicht möglich – abgebrochen.")
    if not apple_id or not pw:
        sys.exit("Abgebrochen.")
    print("Prüfe bei iCloud (nur lesend) …")
    try:
        cals = icloud.calendars(apple_id, pw)
    except (icloud.ICloudError, OSError) as e:
        sys.exit(f"Nicht gespeichert: {e}")
    icloud.save(apple_id, pw)
    print(f"Gespeichert. {len(cals)} Kalender gefunden: " + ", ".join(n for n, _ in cals))
    print("Es wird noch nichts in iCloud eingetragen.")


def cmd_icloud_test(a):
    try:
        cals = icloud.calendars(*icloud.load())
    except (icloud.ICloudError, OSError) as e:
        sys.exit(f"iCloud: {e}")
    for name, _ in cals:
        print(f"  {name}")


def cmd_wissen(a):
    if a.action == "build":
        sys.exit(wissen.run(course=a.course, file_key=a.file, recheck=a.recheck))
    db = dbm.connect()
    c = db.execute("""SELECT count(*), sum(status='ok'), sum(status='error'), coalesce(sum(n_items),0),
                      coalesce(sum(n_dropped),0), coalesce(sum(took),0) FROM chunks""").fetchone()
    print(f"Häppchen: {c[0]} ({c[1] or 0} ok, {c[2] or 0} Fehler) · {c[3]} Einträge übernommen, {c[4]} verworfen · "
          f"KI-Zeit {c[5] / 60:.0f} min")
    for r in db.execute("""SELECT c.shortname, count(DISTINCT n.id) nodes, count(DISTINCT e.src || e.dst_key) edges
                           FROM nodes n JOIN courses c ON c.id=n.courseid LEFT JOIN edges e ON e.src=n.id
                           GROUP BY n.courseid ORDER BY c.shortname"""):
        print(f"  {r['shortname']}: {r['nodes']} Notizen, {r['edges']} Verweise")
    for r in db.execute("SELECT file_key, pages, error FROM chunks WHERE status='error'"):
        print(f"  ! {r['file_key']} S. {r['pages']}: {r['error']}")


def main():
    p = argparse.ArgumentParser(prog="isis")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("login", help="Token holen (ohne Argument: Login-Adresse ausgeben)")
    s.add_argument("redirect", nargs="?")
    s.set_defaults(fn=cmd_login)
    sub.add_parser("whoami", help="Verbindung testen").set_defaults(fn=cmd_whoami)
    s = sub.add_parser("courses", help="laufende Kurse (--all: alle belegten)")
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_courses)
    s = sub.add_parser("deadlines", help="Termine/Abgaben")
    s.add_argument("--days", type=int, default=30)
    s.set_defaults(fn=cmd_deadlines)
    s = sub.add_parser("files", help="Dateien eines Kurses")
    s.add_argument("courseid", type=int)
    s.set_defaults(fn=cmd_files)
    s = sub.add_parser("download", help="eine Datei laden (gibt sha256 aus)")
    s.add_argument("fileurl")
    s.add_argument("dest")
    s.set_defaults(fn=cmd_download)
    s = sub.add_parser("sync", help="ISIS jetzt abgleichen (läuft sonst alle 2 h per Timer)")
    s.add_argument("--no-download", action="store_true")
    s.set_defaults(fn=cmd_sync)
    s = sub.add_parser("status", help="letzte Abgleiche + Bestand")
    s.add_argument("-n", type=int, default=5)
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("new", help="was ist neu/geändert")
    s.add_argument("--days", type=int, default=7)
    s.set_defaults(fn=cmd_new)
    sub.add_parser("set-icloud", help="Apple-ID + app-spezifisches Passwort speichern (verdeckte Eingabe)").set_defaults(fn=cmd_set_icloud)
    sub.add_parser("icloud-test", help="iCloud-Zugang prüfen (listet Kalender, schreibt nichts)").set_defaults(fn=cmd_icloud_test)
    s = sub.add_parser("wissen", help="Wissensnetz (Obsidian-Vault auf der HDD) aufbauen/anzeigen")
    s.add_argument("action", choices=["build", "status"])
    s.add_argument("--course", type=int)
    s.add_argument("--file")
    s.add_argument("--recheck", action="store_true", help="Prüfungen neu, aus gespeicherten Antworten (ohne 9B)")
    s.set_defaults(fn=cmd_wissen)
    a = p.parse_args()
    try:
        a.fn(a)
    except TokenExpired as e:
        sys.exit(f"{NEED_LOGIN} ({e})")
    except MoodleError as e:
        sys.exit(f"Fehler: {e}")


if __name__ == "__main__":
    main()
