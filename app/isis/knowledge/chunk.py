"""Course file → text pages → chunks small enough for the 8k-context model."""
import hashlib
import html
import re

import pypdf

MAX_CHARS = 5500        # ≈ 2.5k tokens: slides with formulas measured at ≈ 2.1 chars/token
MIN_PAGE = 80           # less than this is a picture/title slide
MIN_CHUNK = 200


def pages(path):
    """[(page_no, text)], 1-based. HTML (ISIS text pages) counts as one page."""
    if str(path).lower().endswith((".html", ".htm")):
        raw = open(path, encoding="utf-8", errors="replace").read()
        raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
        text = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?i)<(br|/p|/li|/h\d)[^>]*>", "\n", raw)))
        return [(1, re.sub(r"[ \t]+", " ", text).strip())]
    reader = pypdf.PdfReader(path)
    return [(i + 1, (p.extract_text() or "").strip()) for i, p in enumerate(reader.pages)]


def chunks(page_list):
    """Greedy packing at page boundaries; an oversized page is split at blank lines/lines."""
    out, cur, size = [], [], 0

    def flush():
        nonlocal cur, size
        if cur and sum(len(t) for _, t in cur) >= MIN_CHUNK:
            text = "\n\n".join(f"[Seite {n}]\n{t}" for n, t in cur)
            out.append({"pages": cur, "text": text, "sha": hashlib.sha1(text.encode()).hexdigest(),
                        "label": f"{cur[0][0]}-{cur[-1][0]}" if len(cur) > 1 else str(cur[0][0])})
        cur, size = [], 0

    for n, t in page_list:
        if len(t) < MIN_PAGE:
            continue
        parts = [t] if len(t) <= MAX_CHARS else _split(t)
        for part in parts:
            if size + len(part) > MAX_CHARS:
                flush()
            cur.append((n, part))
            size += len(part)
    flush()
    return out


def _split(text):
    parts, cur = [], ""
    for line in text.splitlines(keepends=True):
        if len(cur) + len(line) > MAX_CHARS and cur:
            parts.append(cur)
            cur = ""
        cur += line
    return parts + [cur] if cur else parts
