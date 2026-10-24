"""DB → Obsidian vault (<DATA>/wissen/<Kurs>/<Titel>.md). Only touches files it generated itself."""
import os
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from . import merge

MARK = "generiert: isis"
TYPE_NAME = {"begriff": "Begriffe", "formel": "Formeln", "verfahren": "Verfahren", "beispiel": "Beispiele",
             "termin": "Termine aus dem Text"}


def _safe(name, maxlen=100):
    """Obsidian link syntax and NTFS both forbid these."""
    name = re.sub(r'[\\/:*?"<>|#^\[\]\x00-\x1f]+', " ", name)
    return re.sub(r"\s+", " ", name).strip(" .")[:maxlen] or "_"


def _course_dir(shortname):
    return _safe(re.sub(r"^(\[[^\]]*\]\s*)+", "", shortname or "") or shortname)


def _yaml(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def write(db, data_dir):
    root = Path(data_dir) / "wissen"
    wanted = {}
    courses = {r["id"]: r for r in db.execute("SELECT id, shortname, fullname FROM courses")}
    names = {r["key"]: r["filename"] for r in db.execute("SELECT key, filename FROM files")}
    by_key_courses = defaultdict(list)
    for r in db.execute("SELECT id, courseid, key FROM nodes"):
        by_key_courses[r["key"]].append(r["courseid"])

    for cid, course in courses.items():
        nodes = db.execute("SELECT * FROM nodes WHERE courseid=? ORDER BY importance DESC, title", (cid,)).fetchall()
        if not nodes:
            continue
        cdir = _course_dir(course["shortname"])
        fname, used = {}, set()
        for n in nodes:  # NTFS is case-insensitive, so compare lowercased
            base = _safe(n["title"])
            name, i = base, 2
            while name.lower() in used:
                name, i = f"{base} ({i})", i + 1
            used.add(name.lower())
            fname[n["id"]] = name
        resolve = merge.resolve(db, cid)
        index = defaultdict(list)
        for n in nodes:
            srcs = db.execute("""SELECT file_key, page, quote, grounded FROM sources WHERE node_id=?
                                 ORDER BY file_key, page""", (n["id"],)).fetchall()
            links = []
            for (dst_key,) in db.execute("SELECT dst_key FROM edges WHERE src=?", (n["id"],)):
                dst = resolve(dst_key)
                if dst and dst != n["id"] and dst in fname and fname[dst] not in links:
                    links.append(fname[dst])
            others = [c for c in by_key_courses[n["key"]] if c != cid and c in courses]
            verdicts = [(s["grounded"] or "").split(" ")[0] for s in srcs]
            status = ("ja" if "supported" in verdicts or not any(verdicts)  # no verdict = jeff switched off
                      else "teilweise" if "partly_supported" in verdicts else "unsicher")
            doubt = next((s["grounded"] for s in srcs if status == "unsicher"), "")
            quellen = sorted({f"{names.get(s['file_key'], s['file_key'])} S. {s['page']}" for s in srcs})
            lines = ["---", f"typ: {n['type']}", f"kurs: {_yaml(course['shortname'])}", f"wichtigkeit: {n['importance']}",
                     "quellen:", *[f"  - {_yaml(q)}" for q in quellen],
                     f"belegt: {status}", f"tags: [isis/{n['type']}{', isis/pruefen' if status == 'unsicher' else ''}]",
                     MARK, "---",
                     f"# {n['title']}", "", n["content"], ""]
            if status == "unsicher":
                lines += [f"> [!warning] jeff hält das für nicht durch die Folie belegt ({doubt}). Das Zitat steht "
                          "aber wörtlich im Text – bitte gegen die Quelle prüfen.", ""]
            elif status == "teilweise":
                lines += ["> [!note] Nur teilweise durch die Folie belegt (jeff).", ""]
            lines += ["## Belege", ""]
            for s in srcs:
                quote = re.sub(r"\s*\n\s*", " ", unicodedata.normalize("NFKC", s["quote"]))  # 𝑊𝐴 → WA, one line
                lines += [f"> {quote}", f"> — {names.get(s['file_key'], s['file_key'])}, S. {s['page']}", ""]
            if links:
                lines += ["## Verwandt", "", *[f"- [[{cdir}/{l}|{l}]]" for l in links], ""]
            if others:
                lines += ["## Auch in", "", *[f"- [[{_course_dir(courses[c]['shortname'])}/{_safe(n['title'])}|"
                                               f"{_course_dir(courses[c]['shortname'])}]]" for c in others], ""]
            wanted[root / cdir / f"{fname[n['id']]}.md"] = "\n".join(lines)
            index[n["type"]].append((n, status))
        idx = ["---", MARK, "---", f"# {course['fullname']}", ""]
        for t in TYPE_NAME:
            if index.get(t):
                idx += [f"## {TYPE_NAME[t]}", ""] + [f"- {'★' * x['importance']} [[{cdir}/{fname[x['id']]}|{x['title']}]]"
                                                    + (" ⚠" if st == "unsicher" else "") for x, st in index[t]] + [""]
        wanted[root / cdir / "_Index.md"] = "\n".join(idx)

    written = removed = 0
    for path, text in wanted.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
            written += 1
    if root.exists():
        for p in root.rglob("*.md"):
            if p not in wanted and MARK in p.read_text(encoding="utf-8", errors="replace")[:400]:
                p.unlink()
                removed += 1
    return len(wanted), written, removed
