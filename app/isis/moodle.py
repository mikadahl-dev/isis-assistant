"""Minimal ISIS (Moodle) web-service client: SSO token via the mobile-app launch flow, REST calls, file download.

The token comes from admin/tool/mobile/launch.php after a browser login (Shibboleth + TOTP); only the
web-service token is kept, the private token (cookie generator) is thrown away.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

import httpx

WWWROOT = os.environ.get("ISIS_URL", "https://isis.tu-berlin.de").rstrip("/")
STATE = Path(os.environ.get("ISIS_STATE", "/var/lib/isis"))
TOKEN_FILE = STATE / "token"
PASSPORT_FILE = STATE / "passport"
SERVICE = "moodle_mobile_app"
SCHEME = "moodlemobile"
MIN_INTERVAL = 1.0  # seconds between requests, be gentle with ISIS
UA = "MoodleMobile isis (single user)"


class TokenExpired(Exception):
    pass


class MoodleError(Exception):
    pass


def _write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def make_login_url():
    passport = secrets.token_hex(16)
    _write_private(PASSPORT_FILE, passport)
    return (f"{WWWROOT}/admin/tool/mobile/launch.php?service={SERVICE}"
            f"&passport={passport}&urlscheme={SCHEME}")


def redeem(redirect):
    """Takes the moodlemobile://token=... URL (or just the base64 part), checks the signature, stores the token."""
    m = re.search(r"token=([A-Za-z0-9+/=_-]+)", redirect.strip())
    blob = m.group(1) if m else redirect.strip()
    try:
        decoded = base64.b64decode(blob + "=" * (-len(blob) % 4)).decode()
    except ValueError as e:
        raise MoodleError(f"kein gültiges Token in der Eingabe ({e})")
    parts = decoded.split(":::")
    if len(parts) < 2:
        raise MoodleError("unerwartetes Format (erwartet signatur:::token)")
    if not PASSPORT_FILE.exists():
        raise MoodleError("kein offener Login – zuerst `isis login` ohne Argument")
    passport = PASSPORT_FILE.read_text().strip()
    if parts[0] != hashlib.md5((WWWROOT + passport).encode()).hexdigest():
        raise MoodleError("Signatur passt nicht – Link stammt nicht aus dem letzten `isis login`")
    if not re.fullmatch(r"[0-9a-f]{32}", parts[1]):
        raise MoodleError("Token hat ein unerwartetes Format")
    _write_private(TOKEN_FILE, parts[1])
    PASSPORT_FILE.unlink()
    return parts[1]


def _flatten(params, prefix=""):
    """Moodle REST wants nested params as courseids[0]=1&options[0][name]=x."""
    out = {}
    items = params.items() if isinstance(params, dict) else enumerate(params)
    for k, v in items:
        key = f"{prefix}[{k}]" if prefix else str(k)
        if isinstance(v, (dict, list, tuple)):
            out.update(_flatten(v, key))
        elif isinstance(v, bool):
            out[key] = int(v)
        else:
            out[key] = v
    return out


class Client:
    def __init__(self, token=None):
        if token is None:
            if not TOKEN_FILE.exists():
                raise TokenExpired("kein Token gespeichert")
            token = TOKEN_FILE.read_text().strip()
        self.token = token
        self.http = httpx.Client(timeout=60, headers={"User-Agent": UA}, follow_redirects=True)
        self._last = 0.0

    def _throttle(self):
        wait = self._last + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def call(self, function, **params):
        self._throttle()
        data = {"wstoken": self.token, "wsfunction": function, "moodlewsrestformat": "json", **_flatten(params)}
        r = self.http.post(f"{WWWROOT}/webservice/rest/server.php", data=data)
        r.raise_for_status()
        res = r.json()
        if isinstance(res, dict) and "exception" in res:
            if res.get("errorcode") in ("invalidtoken", "accessexception"):
                raise TokenExpired(res.get("message", res["errorcode"]))
            raise MoodleError(f"{function}: {res.get('errorcode')}: {res.get('message')}")
        return res

    def download(self, fileurl, dest):
        """fileurl as returned by the API (…/webservice/pluginfile.php/…); streams to dest, returns sha256."""
        if not fileurl.startswith(WWWROOT + "/"):
            raise MoodleError(f"fremde Datei-URL abgelehnt: {fileurl}")
        self._throttle()
        h = hashlib.sha256()
        dest = Path(dest)
        tmp = dest.with_name(dest.name + ".part")
        with self.http.stream("GET", fileurl, params={"token": self.token}) as r:
            if "/login/" in r.url.path:
                raise TokenExpired("ISIS leitet zur Login-Seite um")
            r.raise_for_status()
            if r.headers.get("content-type", "").startswith("application/json"):
                err = json.loads(r.read())  # pluginfile answers errors as JSON, files never are
                if err.get("errorcode") in ("invalidtoken", "accessexception"):
                    raise TokenExpired(err.get("message", err["errorcode"]))
                if "errorcode" in err:
                    raise MoodleError(f"{err['errorcode']}: {err.get('message')}")
                tmp.write_bytes(json.dumps(err).encode())
                os.replace(tmp, dest)
                return hashlib.sha256(dest.read_bytes()).hexdigest()
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes():
                    h.update(chunk)
                    f.write(chunk)
        os.replace(tmp, dest)
        return h.hexdigest()
