"""Write all known events as one iCalendar file (local copy; iCloud comes later)."""
import datetime as dt
import html
import os
import re

from .moodle import WWWROOT

HOST = WWWROOT.split("://", 1)[-1]


def _esc(s):
    return (s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r", "").replace("\n", "\\n"))


def _fold(line):
    """RFC 5545: lines over 75 octets continue with a leading space."""
    out, b = [], line.encode()
    while len(b) > 75:
        cut = 75
        while (b[cut] & 0xC0) == 0x80:  # never split inside a UTF-8 sequence
            cut -= 1
        out.append(b[:cut].decode())
        b = b" " + b[cut:]
    out.append(b.decode())
    return "\r\n".join(out)


def _utc(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _plain(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def write(db, path):
    now = _utc(dt.datetime.now(dt.timezone.utc).timestamp())
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//isis//isis//DE", "CALSCALE:GREGORIAN",
             "X-WR-CALNAME:ISIS", "X-WR-TIMEZONE:Europe/Berlin"]
    rows = db.execute("""SELECT e.*, c.shortname FROM events e LEFT JOIN courses c ON c.id=e.courseid
                         WHERE e.gone=0 ORDER BY e.timestart""").fetchall()
    for e in rows:
        short = re.sub(r"^(\[[^\]]*\]\s*)+", "", e["shortname"] or "")  # drop "[WiSe 2026/27] "
        summary = f"[{short}] {e['name']}" if short else e["name"]
        desc = "\n\n".join(x for x in (_plain(e["description"]), e["url"]) if x)
        lines += ["BEGIN:VEVENT", f"UID:isis-{e['id']}@{HOST}", f"DTSTAMP:{now}",
                  f"LAST-MODIFIED:{_utc(e['timemodified'] or e['first_seen'])}",
                  f"DTSTART:{_utc(e['timestart'])}", f"DTEND:{_utc(e['timestart'] + (e['timeduration'] or 0))}",
                  f"SUMMARY:{_esc(summary)}", f"DESCRIPTION:{_esc(desc)}"]
        if e["url"]:
            lines.append(f"URL:{e['url']}")
        lines.append(f"CATEGORIES:{_esc(e['modulename'] or e['eventtype'] or 'course')}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    tmp = f"{path}.tmp"
    with open(tmp, "w", newline="") as f:
        f.write("\r\n".join(_fold(l) for l in lines) + "\r\n")
    os.replace(tmp, path)
    return len(rows)
