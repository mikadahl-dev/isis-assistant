"""One model call per chunk (structured JSON), then verification without the model.

The model only names facts and copies a supporting quote; everything it says is dropped unless that
quote is really in the chunk. The page comes from where the quote was found, not from the model.
"""
import json
import os
import re
import time
import unicodedata

import httpx

OLLAMA = os.environ.get("ISIS_OLLAMA", "http://127.0.0.1:11434").rstrip("/")
MODEL = os.environ.get("ISIS_MODEL", "qwen3.5:9b")
NUM_CTX = 8192
NUM_PREDICT = 1500      # prompt ≈ 0.7k + text ≤ 2.5k + this ≈ 60 % of 8k
MAX_ITEMS = 8
TYPES = ["begriff", "formel", "verfahren", "beispiel", "termin", "orga"]

SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "maxItems": MAX_ITEMS, "items": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": TYPES},
            "title": {"type": "string"},
            "content": {"type": "string"},
            "quote": {"type": "string"},
            "importance": {"type": "integer", "enum": [1, 2, 3]},
            "related": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        },
        "required": ["type", "title", "content", "quote", "importance", "related"],
    }}},
    "required": ["items"],
}

SYSTEM = """Du ziehst Lernwissen aus Vorlesungsunterlagen heraus. Antworte nur mit JSON nach dem Schema.
Regeln:
1. Nur Wissen, das im Text steht. Nichts ergänzen, nichts aus eigenem Wissen.
2. Höchstens 8 Einträge, die wichtigsten zuerst. Lieber weniger als Füllmaterial.
3. type: begriff = Fachbegriff mit Bedeutung; formel = Formel/Gesetz mit Bedeutung der Größen;
   verfahren = Methode oder Ablauf; beispiel = konkretes Beispiel oder Zahlenwert;
   termin = Datum/Frist mit Bezug (z. B. Übungstermine, Abgabe, Klausur); orga = Organisatorisches
   (Personen, Räume, Zulassung, Bewertung).
4. title: der Fachbegriff selbst, 1 bis 6 Wörter, Grundform, ohne Artikel. Ist der Begriff schon in
   "Bekannte Begriffe" genannt, schreibe ihn genau so.
5. content: 1 bis 2 eigene, sachliche Sätze.
6. quote: eine kurze Stelle WÖRTLICH aus dem Text kopiert (höchstens 200 Zeichen), die den Eintrag belegt.
7. importance: 3 = Kernbegriff des Themas, 2 = wichtig, 1 = Detail.
8. related: Titel anderer Fachbegriffe, die direkt damit zusammenhängen (aus diesem Text oder aus
   "Bekannte Begriffe").
Ignoriere Seitenzahlen, Folientitel ohne Inhalt, Literaturangaben und Bildbeschriftungen."""

DATE = re.compile(r"\b\d{1,2}\.\s?\d{1,2}\.(\s?\d{2,4})?|\b\d{1,2}\.\s?(Jan|Feb|Mär|Apr|Mai|Jun|Jul|Aug|Sep|Okt|Nov|Dez)", re.I)


class ModelError(Exception):
    pass


def norm(s):
    """For quote matching: NFKC (ligatures, math italics), hyphenation across lines, case, spacing."""
    s = unicodedata.normalize("NFKC", s).replace("­", "")
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _loose(s):
    return re.sub(r"[^\w]+", "", s)


def _call(messages, schema, num_predict):
    r = httpx.post(f"{OLLAMA}/api/chat", timeout=600, json={
        "model": MODEL, "messages": messages, "stream": False, "think": False, "format": schema,
        "options": {"temperature": 0.1, "num_ctx": NUM_CTX, "num_predict": num_predict}})
    r.raise_for_status()
    d = r.json()
    try:
        return json.loads(d["message"]["content"]), d.get("prompt_eval_count"), d.get("eval_count")
    except (KeyError, json.JSONDecodeError) as e:
        raise ModelError(f"keine gültige JSON-Antwort ({e}): {d.get('message', {}).get('content', '')[:200]}")


def extract(chunk, course, filename, known_titles):
    """→ (raw items as the model gave them, tok_in, tok_out, seconds); check them with verify()."""
    known = ", ".join(known_titles[:80]) or "–"
    user = f"Kurs: {course}\nDatei: {filename}\nBekannte Begriffe: {known}\n\nText:\n{chunk['text']}"
    t0 = time.monotonic()
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    try:
        res, tin, tout = _call(msgs, SCHEMA, NUM_PREDICT)
    except ModelError:  # one retry: a cut-off answer at num_predict is the usual cause
        res, tin, tout = _call(msgs, SCHEMA, NUM_PREDICT)
    return res.get("items", [])[:MAX_ITEMS], tin, tout, time.monotonic() - t0


def verify(items, chunk):
    pages = [(n, norm(t), _loose(norm(t))) for n, t in chunk["pages"]]
    whole = " ".join(t for _, t, _ in pages)
    kept, dropped = [], []
    for it in items:
        title = (it.get("title") or "").strip().strip(".:")
        quote = (it.get("quote") or "").strip()
        q, ql = norm(quote), _loose(norm(quote))
        page = next((n for n, t, _ in pages if q and q in t), None)
        if page is None and len(ql) >= 12:  # same letters, different punctuation/bullets
            page = next((n for n, _, tl in pages if ql in tl), None)
        why = ("Zitat nicht im Text" if page is None else
               "Form" if it.get("type") not in TYPES or not 2 <= len(title) <= 80 or len(title.split()) > 8
               or not it.get("content") else
               "Titel nicht im Text" if not _title_in_text(title, whole) else
               "Termin ohne Datum" if it["type"] == "termin" and not DATE.search(quote) else None)
        if why:
            dropped.append((why, it))
            continue
        kept.append({"type": it["type"], "title": title, "content": it["content"].strip()[:600],
                     "quote": quote[:300], "page": page, "importance": int(it.get("importance") or 1),
                     "related": [r.strip() for r in (it.get("related") or [])[:5] if r.strip()]})
    return kept, dropped


def _title_in_text(title, text):
    """At least half of the longer title words must occur in the chunk (first 5 letters, so inflections
    pass) – catches titles the model made up while allowing "<Name>-Verfahren"."""
    words = [w for w in re.findall(r"\w+", norm(title)) if len(w) >= 4]
    return not words or sum(w[:5] in text for w in words) * 2 >= len(words)  # "Czochralski-Verfahren" passes


def same_concept(a, b):
    """Mini call for near-identical titles: one boolean."""
    schema = {"type": "object", "properties": {"same": {"type": "boolean"}}, "required": ["same"]}
    msgs = [{"role": "system", "content": "Beantworte nur, ob beide Einträge denselben Fachbegriff meinen. JSON."},
            {"role": "user", "content": f"A: {a['title']} – {a['content']}\nB: {b['title']} – {b['content']}"}]
    res, _, _ = _call(msgs, schema, 20)
    return bool(res.get("same"))
