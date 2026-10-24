"""iCloud credentials (Apple ID + app-specific password) and a read-only CalDAV check.

Stored in /var/lib/isis/icloud.json (0600, user isis). Nothing is written to iCloud yet.
"""
import json
import xml.etree.ElementTree as ET

import httpx

from .moodle import STATE, _write_private

CRED_FILE = STATE / "icloud.json"
ROOT = "https://caldav.icloud.com/"
NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}


class ICloudError(Exception):
    pass


def _propfind(http, url, body, depth="0"):
    r = http.request("PROPFIND", url, content=body, headers={"Depth": depth, "Content-Type": "application/xml"})
    if r.status_code == 401:
        raise ICloudError("Apple-ID oder app-spezifisches Passwort falsch (401)")
    if r.status_code != 207:
        raise ICloudError(f"{url}: HTTP {r.status_code}")
    return ET.fromstring(r.content)


def calendars(apple_id, password):
    """Returns [(name, url)] of the account's calendars; only reads."""
    with httpx.Client(auth=(apple_id, password), timeout=30, follow_redirects=True) as http:
        x = _propfind(http, ROOT, '<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>')
        principal = x.find(".//d:current-user-principal/d:href", NS)
        if principal is None:
            raise ICloudError("kein Principal in der Antwort")
        x = _propfind(http, httpx.URL(ROOT).join(principal.text.strip()),
                      '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
                      '<d:prop><c:calendar-home-set/></d:prop></d:propfind>')
        home = x.find(".//c:calendar-home-set/d:href", NS)
        if home is None:
            raise ICloudError("kein Kalender-Verzeichnis in der Antwort")
        home_url = httpx.URL(ROOT).join(home.text.strip())
        x = _propfind(http, home_url, '<d:propfind xmlns:d="DAV:"><d:prop><d:displayname/><d:resourcetype/></d:prop>'
                                      '</d:propfind>', depth="1")
        out = []
        for resp in x.findall("d:response", NS):
            if resp.find(".//d:resourcetype/c:calendar", NS) is not None:
                name = resp.findtext(".//d:displayname", default="?", namespaces=NS)
                out.append((name, str(home_url.join(resp.findtext("d:href", namespaces=NS)))))
        return out


def save(apple_id, password):
    _write_private(CRED_FILE, json.dumps({"apple_id": apple_id, "password": password}))


def load():
    if not CRED_FILE.exists():
        raise ICloudError("keine iCloud-Zugangsdaten – `isis set-icloud`")
    d = json.loads(CRED_FILE.read_text())
    return d["apple_id"], d["password"]
