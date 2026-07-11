#!/usr/bin/env python3
"""Three-way, JSON-aware merge for ctls-data.json (per-editor-branch sync, Phase A).

Usage:
  merge_catalogue.py BASE THEIRS MAIN -o MERGED [--report REPORT.md] [--json-report R.json]

  BASE   = the catalogue version the editor's page loaded (resolved from _meta.baseRev)
  THEIRS = the file the editor saved (from their save/<name> branch)
  MAIN   = current main
  MERGED = written only when the merge is clean

Exit codes: 0 = clean merge written; 2 = conflicts (report written/printed, MERGED not
written); 1 = bad input (unreadable file, duplicate keys inside one file).

Merge model:
  * Collections are merged item-by-item, keyed by their natural key:
      topics by id, resources by title, tasks by id, glossary by term,
      edges by (from, to).
  * An item changed on only one side wins; changed identically on both sides applies
    once; changed differently on both sides merges FIELD by field, and only a field
    changed two different ways is a conflict. Delete-vs-edit and add-vs-add-different
    are item-level conflicts. Renames arrive as delete+add (the editor's renameRefs
    already rewrote every reference inside THEIRS), so rename-vs-edit surfaces as
    delete-vs-edit, which is correct: titles are the catalogue's foreign key.
  * Derived fields are never diffed, always recomputed on the merged result:
    meta.counts and topics[].resourceCount. (The live file's counts.edges is already
    stale; the first merge quietly corrects it.)
  * _meta is automation-owned: stripped from all inputs before diffing; the output
    gets main's _meta with rev bumped by 1 (rev starts at 1 if main has none yet).
  * Item order follows MAIN; items added by THEIRS are inserted after the nearest
    preceding item of THEIRS that survives in the result.

Output is serialized like the editor writes it: JSON, indent=1, unicode kept, trailing
newline, so formatting churn cannot masquerade as a content change.
"""
import argparse
import json
import sys

MISSING = object()  # field/item absent (never equal to any JSON value)

COLLECTIONS = [
    ("topics", lambda x: x["id"]),
    ("resources", lambda x: x["title"]),
    ("tasks", lambda x: x["id"]),
    ("glossary", lambda x: x["term"]),
    ("edges", lambda x: (x["from"], x["to"])),
]
# (collection, field) pairs recomputed after the merge instead of diffed.
DERIVED = {("meta", "counts"), ("topics", "resourceCount")}


class Conflict:
    def __init__(self, collection, key, field, base, theirs, main):
        self.collection = collection
        self.key = key
        self.field = field  # None = whole-item conflict
        self.base, self.theirs, self.main = base, theirs, main

    def kind(self):
        if self.field is not None:
            return "same field changed two ways"
        if self.base is MISSING:
            return "added on both sides with different content"
        if self.theirs is MISSING:
            return "deleted (or renamed away) by the editor but changed on main"
        if self.main is MISSING:
            return "deleted (or renamed away) on main but changed by the editor"
        return "item conflict"

    def as_dict(self):
        def val(v):
            return None if v is MISSING else v
        return {
            "collection": self.collection,
            "key": " -> ".join(self.key) if isinstance(self.key, tuple) else self.key,
            "field": self.field,
            "kind": self.kind(),
            "base": val(self.base),
            "theirs": val(self.theirs),
            "main": val(self.main),
        }


def index_by_key(name, keyf, items):
    """Return ({key: item}, [key, ...]); duplicate keys inside one file are corrupt input."""
    idx, order = {}, []
    for it in items:
        try:
            k = keyf(it)
        except (KeyError, TypeError):
            raise ValueError("%s: item missing its key field: %r" % (name, it))
        if k in idx:
            raise ValueError("%s: duplicate key %r inside one file" % (name, k))
        idx[k] = it
        order.append(k)
    return idx, order


def merge_fields(collection, key, b, t, m, conflicts):
    """Field-level three-way merge of one item present in all three versions."""
    fields = list(m.keys())
    fields += [k for k in t.keys() if k not in m]
    fields += [k for k in b.keys() if k not in m and k not in t]
    out = {}
    for f in fields:
        bv, tv, mv = b.get(f, MISSING), t.get(f, MISSING), m.get(f, MISSING)
        if (collection, f) in DERIVED:
            v = mv if mv is not MISSING else tv
        elif tv == bv:
            v = mv
        elif mv == bv:
            v = tv
        elif tv == mv:
            v = tv
        else:
            conflicts.append(Conflict(collection, key, f, bv, tv, mv))
            continue
        if v is not MISSING:
            out[f] = v
    return out


def merge_collection(name, keyf, base, theirs, main, conflicts):
    b_idx, _b_ord = index_by_key(name, keyf, base)
    t_idx, t_ord = index_by_key(name, keyf, theirs)
    m_idx, m_ord = index_by_key(name, keyf, main)

    all_keys = list(m_ord)
    all_keys += [k for k in t_ord if k not in m_idx]
    all_keys += [k for k in b_idx if k not in m_idx and k not in t_idx]

    resolved = {}
    for k in all_keys:
        b, t, m = b_idx.get(k, MISSING), t_idx.get(k, MISSING), m_idx.get(k, MISSING)
        if t == b:
            resolved[k] = m
        elif m == b:
            resolved[k] = t
        elif t == m:
            resolved[k] = t
        elif b is not MISSING and t is not MISSING and m is not MISSING \
                and isinstance(b, dict) and isinstance(t, dict) and isinstance(m, dict):
            resolved[k] = merge_fields(name, k, b, t, m, conflicts)
        else:
            conflicts.append(Conflict(name, k, None, b, t, m))
            resolved[k] = m if m is not MISSING else t

    out_keys = [k for k in m_ord if resolved[k] is not MISSING]
    placed = set(out_keys)
    anchor = None
    for k in t_ord:
        if k in placed:
            anchor = k
            continue
        if resolved[k] is MISSING:
            continue  # e.g. deleted on main, untouched by the editor
        pos = out_keys.index(anchor) + 1 if anchor is not None else 0
        out_keys.insert(pos, k)
        placed.add(k)
        anchor = k
    return [resolved[k] for k in out_keys]


def drop_dangling_refs(payload):
    """Remove references to resource titles that no longer exist, and report them.

    Resources are keyed by title, and four other structures point AT that title:
    topics[].startHere, tasks[].steps[].res, resources[].related, and edges from/to.
    The editor rewrites all four when it renames a card, but only inside its own
    file. Two editors from the same base can each be self-consistent and still merge
    into a broken whole: A renames X to Y while B adds a reference to X somewhere
    else. Different items, no conflict, clean merge -- and main now points at a
    resource that does not exist. On the live site those show as dead entries in a
    topic's "Start here" list and in task steps. Drop them so a merge cannot publish
    a broken reference; the returned list is surfaced in the run log.
    """
    titles = {r.get("title") for r in payload.get("resources", []) if isinstance(r, dict)}
    dropped = []

    for topic in payload.get("topics", []):
        if isinstance(topic, dict) and isinstance(topic.get("startHere"), list):
            keep = [t for t in topic["startHere"] if t in titles]
            for t in topic["startHere"]:
                if t not in titles:
                    dropped.append("topic %r startHere -> %r" % (topic.get("name"), t))
            topic["startHere"] = keep

    for task in payload.get("tasks", []):
        if isinstance(task, dict) and isinstance(task.get("steps"), list):
            keep = [s for s in task["steps"]
                    if not (isinstance(s, dict) and s.get("res") and s["res"] not in titles)]
            for s in task["steps"]:
                if isinstance(s, dict) and s.get("res") and s["res"] not in titles:
                    dropped.append("task %r step -> %r" % (task.get("id"), s["res"]))
            task["steps"] = keep

    for res in payload.get("resources", []):
        if isinstance(res, dict) and isinstance(res.get("related"), list):
            keep = [t for t in res["related"] if t in titles]
            for t in res["related"]:
                if t not in titles:
                    dropped.append("resource %r related -> %r" % (res.get("title"), t))
            res["related"] = keep

    edges = payload.get("edges")
    if isinstance(edges, list):
        keep = []
        for e in edges:
            if isinstance(e, dict) and (e.get("from") not in titles or e.get("to") not in titles):
                dropped.append("edge %r -> %r" % (e.get("from"), e.get("to")))
            else:
                keep.append(e)
        payload["edges"] = keep

    return dropped


def recompute_derived(payload):
    # Order matters: drop dangling references FIRST, so the counts below describe
    # what actually ships rather than counting entries that are about to vanish.
    dropped = drop_dangling_refs(payload)
    resources = payload.get("resources", [])
    meta = payload.get("meta")
    if isinstance(meta, dict) and isinstance(meta.get("counts"), dict):
        for cname in ("resources", "glossary", "edges"):
            if cname in meta["counts"]:
                meta["counts"][cname] = len(payload.get(cname, []))
    for topic in payload.get("topics", []):
        if isinstance(topic, dict) and "name" in topic:
            topic["resourceCount"] = sum(1 for r in resources if r.get("topic") == topic["name"])
    return dropped


def merge_payloads(base, theirs, main):
    """Pure merge. Returns (merged_payload_or_None, [Conflict, ...])."""
    base, theirs, main = dict(base), dict(theirs), dict(main)
    main_meta = main.get("_meta") if isinstance(main.get("_meta"), dict) else {}
    for d in (base, theirs, main):
        d.pop("_meta", None)

    conflicts = []
    out = {}
    collection_names = {n for n, _ in COLLECTIONS}
    top_keys = list(main.keys())
    top_keys += [k for k in theirs.keys() if k not in main]
    top_keys += [k for k in base.keys() if k not in main and k not in theirs]

    for key in top_keys:
        if key in collection_names:
            continue  # handled below, in canonical order
        bv, tv, mv = base.get(key, MISSING), theirs.get(key, MISSING), main.get(key, MISSING)
        if all(isinstance(v, dict) for v in (bv, tv, mv)):
            out[key] = merge_fields(key, None, bv, tv, mv, conflicts)
            continue
        if tv == bv:
            v = mv
        elif mv == bv:
            v = tv
        elif tv == mv:
            v = tv
        else:
            conflicts.append(Conflict("(top level)", key, None, bv, tv, mv))
            continue
        if v is not MISSING:
            out[key] = v

    for name, keyf in COLLECTIONS:
        out[name] = merge_collection(
            name, keyf,
            base.get(name, []), theirs.get(name, []), main.get(name, []),
            conflicts)

    if conflicts:
        return None, conflicts

    for ref in recompute_derived(out):
        print("dropped dangling reference: %s" % ref, file=sys.stderr)
    new_meta = dict(main_meta)
    # baseRev is an editor-save annotation; it must not fossilize into main's _meta
    # (interim straight-to-main commits can leave one behind).
    new_meta.pop("baseRev", None)
    new_meta["rev"] = int(new_meta.get("rev", 0)) + 1
    out["_meta"] = new_meta
    return out, []


def render_report(conflicts):
    def show(v):
        if v is MISSING:
            return "(not present)"
        s = json.dumps(v, ensure_ascii=False)
        return s if len(s) <= 300 else s[:300] + "..."
    lines = ["# Merge needs a human: %d conflict%s" % (len(conflicts), "s" if len(conflicts) != 1 else ""), ""]
    for c in conflicts:
        key = " -> ".join(c.key) if isinstance(c.key, tuple) else (c.key or "")
        where = "**%s** / %s" % (c.collection, json.dumps(key, ensure_ascii=False))
        if c.field:
            where += " / field `%s`" % c.field
        lines += ["## " + where, "", "_%s_" % c.kind(), "",
                  "- what it said before either edit: " + show(c.base),
                  "- the editor's version: " + show(c.theirs),
                  "- what is live now: " + show(c.main), ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Three-way JSON-aware merge for ctls-data.json")
    ap.add_argument("base")
    ap.add_argument("theirs")
    ap.add_argument("main_file", metavar="main")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--report", help="write the conflict report (markdown) here")
    ap.add_argument("--json-report", help="write the conflicts as JSON here")
    args = ap.parse_args(argv)

    payloads = []
    for path in (args.base, args.theirs, args.main_file):
        try:
            with open(path, encoding="utf-8") as f:
                payloads.append(json.load(f))
        except (OSError, ValueError) as e:
            print("cannot read %s: %s" % (path, e), file=sys.stderr)
            return 1

    try:
        merged, conflicts = merge_payloads(*payloads)
    except ValueError as e:
        print("corrupt input: %s" % e, file=sys.stderr)
        return 1

    if conflicts:
        report = render_report(conflicts)
        print(report)
        if args.report:
            with open(args.report, "w", encoding="utf-8") as f:
                f.write(report + "\n")
        if args.json_report:
            with open(args.json_report, "w", encoding="utf-8") as f:
                json.dump([c.as_dict() for c in conflicts], f, ensure_ascii=False, indent=1)
        return 2

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(json.dumps(merged, indent=1, ensure_ascii=False) + "\n")
    print("clean merge -> %s (rev %s)" % (args.out, merged["_meta"]["rev"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
