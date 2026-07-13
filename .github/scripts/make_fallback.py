#!/usr/bin/env python3
"""Regenerate ctls-data.js (the offline fallback) from ctls-data.json (the catalogue).

ctls-data.js is what index.html loads via a plain <script> tag, and it is what the app
falls back to when the fetch for ctls-data.json fails (a file:// open, an offline
laptop, a Pages hiccup). It was a hand-made snapshot that nothing kept up to date, so
it drifted: by July 2026 it still had 6 tasks against the live 17-resource catalogue's
10, and none of the resource ids that share links are built on. In fallback mode every
#/r/<id> link would therefore resolve to nothing and land on the dead-link banner.

So it is now generated, not written, and the merge bot regenerates it in the same
commit that publishes a save. A stale fallback is worse than no fallback: it answers
confidently and wrongly.

_meta is deliberately dropped. It is automation-owned (rev counts published versions),
and a fallback must never look like a real published revision, or an editor who loaded
it could save against a meaningless baseRev. index.html already refuses to run edit
mode when it has fallen back (staleFallback), and this keeps that honest.

Run it by hand after a hand-edit to the data:
    python3 .github/scripts/make_fallback.py
"""
import json
import sys
from pathlib import Path

DATA = "ctls-data.json"
FALLBACK = "ctls-data.js"

HEADER = """\
// Resource Finder catalogue data, for offline and file:// use.
//
// GENERATED FILE - DO NOT EDIT BY HAND. Your change would be overwritten, and worse,
// it would not reach the live site: the app reads ctls-data.json, and only falls back
// to this copy when that fetch fails. Edit ctls-data.json instead, or use the editor.
//
// Regenerated from ctls-data.json by .github/scripts/make_fallback.py, which the merge
// bot runs whenever it publishes a save, so this cannot drift from the catalogue.
// Carries no _meta: a fallback must never look like a published revision.
"""


def build(root=Path(".")):
    src = root / DATA
    doc = json.loads(src.read_text(encoding="utf-8"))
    body = {k: v for k, v in doc.items() if k != "_meta"}
    out = HEADER + "window.CTLS = " + json.dumps(body, indent=1, ensure_ascii=False) + ";\n"
    dst = root / FALLBACK
    before = dst.read_text(encoding="utf-8") if dst.exists() else None
    if before == out:
        return False
    dst.write_text(out, encoding="utf-8")
    return True


if __name__ == "__main__":
    changed = build()
    doc = json.loads(Path(DATA).read_text(encoding="utf-8"))
    print("%s %s: %d resources (%d with an id), %d tasks" % (
        FALLBACK,
        "regenerated" if changed else "already up to date",
        len(doc.get("resources", [])),
        sum(1 for r in doc.get("resources", []) if r.get("id")),
        len(doc.get("tasks", [])),
    ))
    sys.exit(0)
