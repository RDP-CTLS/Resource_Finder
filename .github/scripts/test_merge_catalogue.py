#!/usr/bin/env python3
"""Fixture tests for merge_catalogue.py (Phase A gate).

Run from anywhere:  python3 .github/scripts/test_merge_catalogue.py
Every scenario from the plan's Phase A row is here, plus a self-merge of the real
live catalogue as a ground-truth check.
"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from merge_catalogue import merge_payloads, render_report

REPO = Path(__file__).resolve().parents[2]
LIVE = REPO / "ctls-data.json"


def cat():
    """A miniature catalogue with the real file's shape."""
    return {
        "meta": {"generated": "2026-06-26", "counts": {"resources": 2, "glossary": 1, "edges": 1}},
        "topics": [
            {"name": "T1", "id": "t1", "blurb": "b1", "state": "active",
             "startHere": ["R1"], "icon": "compass", "resourceCount": 2},
            {"name": "T2", "id": "t2", "blurb": "b2", "state": "active",
             "startHere": [], "icon": "chat", "resourceCount": 0},
        ],
        "resources": [
            {"title": "R1", "topic": "T1", "status": "active", "oneLine": "one",
             "useWhen": ["u1"], "readTime": 2, "format": "how-to", "audience": "all",
             "poly": "p", "ai": "a", "related": ["R2"], "concepts": ["c"],
             "url": "https://x/1", "research": []},
            {"title": "R2", "topic": "T1", "status": "active", "oneLine": "two",
             "useWhen": [], "readTime": 5, "format": "guide", "audience": "all",
             "poly": "", "ai": "", "related": [], "concepts": [],
             "url": "https://x/2", "research": []},
        ],
        "tasks": [{"id": "task1", "label": "Do a thing", "intro": "i",
                   "steps": [{"res": "R1", "note": "n"}]}],
        "glossary": [{"term": "g1", "def": "d", "sources": ["R1"]}],
        "edges": [{"from": "R1", "to": "R2", "rel": "prerequisite-of"}],
    }


def res(payload, title):
    for r in payload["resources"]:
        if r["title"] == title:
            return r
    return None


def new_card(title, topic="T2"):
    return {"title": title, "topic": topic, "status": "active", "oneLine": "new",
            "useWhen": [], "readTime": 1, "format": "guide", "audience": "all",
            "poly": "", "ai": "", "related": [], "concepts": [],
            "url": "https://x/" + title, "research": []}


class MergeTests(unittest.TestCase):

    def merge(self, base, theirs, main):
        return merge_payloads(base, theirs, main)

    # -- the clean-merge cases ------------------------------------------------

    def test_different_cards_both_publish(self):
        base = cat()
        theirs = copy.deepcopy(base)
        res(theirs, "R1")["oneLine"] = "alice's fix"
        main = copy.deepcopy(base)
        res(main, "R2")["oneLine"] = "bob's fix"
        merged, conflicts = self.merge(base, theirs, main)
        self.assertEqual(conflicts, [])
        self.assertEqual(res(merged, "R1")["oneLine"], "alice's fix")
        self.assertEqual(res(merged, "R2")["oneLine"], "bob's fix")

    def test_both_add_new_cards(self):
        base = cat()
        theirs = copy.deepcopy(base)
        theirs["resources"].insert(1, new_card("R3"))  # theirs adds R3 after R1
        main = copy.deepcopy(base)
        main["resources"].append(new_card("R4"))       # main appends R4
        merged, conflicts = self.merge(base, theirs, main)
        self.assertEqual(conflicts, [])
        self.assertEqual([r["title"] for r in merged["resources"]], ["R1", "R3", "R2", "R4"])
        # derived fields recomputed, not conflicted
        self.assertEqual(merged["meta"]["counts"]["resources"], 4)
        t2 = next(t for t in merged["topics"] if t["id"] == "t2")
        self.assertEqual(t2["resourceCount"], 2)

    def test_same_card_different_fields(self):
        base = cat()
        theirs = copy.deepcopy(base)
        res(theirs, "R1")["oneLine"] = "reworded"
        main = copy.deepcopy(base)
        res(main, "R1")["readTime"] = 9
        merged, conflicts = self.merge(base, theirs, main)
        self.assertEqual(conflicts, [])
        self.assertEqual(res(merged, "R1")["oneLine"], "reworded")
        self.assertEqual(res(merged, "R1")["readTime"], 9)

    def test_identical_change_applies_once(self):
        base = cat()
        theirs = copy.deepcopy(base)
        res(theirs, "R1")["oneLine"] = "same new text"
        main = copy.deepcopy(theirs)
        merged, conflicts = self.merge(base, theirs, main)
        self.assertEqual(conflicts, [])
        self.assertEqual(res(merged, "R1")["oneLine"], "same new text")

    def test_delete_vs_untouched(self):
        base = cat()
        theirs = copy.deepcopy(base)
        theirs["resources"] = [r for r in theirs["resources"] if r["title"] != "R2"]
        main = copy.deepcopy(base)
        merged, conflicts = self.merge(base, theirs, main)
        self.assertEqual(conflicts, [])
        self.assertIsNone(res(merged, "R2"))
        self.assertEqual(merged["meta"]["counts"]["resources"], 1)

    def test_field_added_only_by_theirs_survives(self):
        # the mergePreserving lesson, at merge level: an unmanaged field must ride along
        base = cat()
        theirs = copy.deepcopy(base)
        res(theirs, "R1")["flag"] = "new-field"
        main = copy.deepcopy(base)
        res(main, "R1")["readTime"] = 9
        merged, conflicts = self.merge(base, theirs, main)
        self.assertEqual(conflicts, [])
        self.assertEqual(res(merged, "R1")["flag"], "new-field")
        self.assertEqual(res(merged, "R1")["readTime"], 9)

    def test_edges_edit_plus_add(self):
        base = cat()
        theirs = copy.deepcopy(base)
        theirs["edges"][0]["rel"] = "builds-on"
        main = copy.deepcopy(base)
        main["edges"].append({"from": "R2", "to": "R1", "rel": "see-also"})
        merged, conflicts = self.merge(base, theirs, main)
        self.assertEqual(conflicts, [])
        self.assertEqual(len(merged["edges"]), 2)
        self.assertEqual(merged["edges"][0]["rel"], "builds-on")

    def test_meta_counts_never_conflict(self):
        base = cat()
        theirs = copy.deepcopy(base)
        theirs["meta"]["counts"]["resources"] = 99   # stale/garbage on their side
        theirs["resources"].append(new_card("R3"))
        main = copy.deepcopy(base)
        main["meta"]["counts"]["resources"] = 41     # different garbage on main
        merged, conflicts = self.merge(base, theirs, main)
        self.assertEqual(conflicts, [])
        self.assertEqual(merged["meta"]["counts"]["resources"], 3)  # recomputed

    def test_rev_bumps_and_meta_owned_by_automation(self):
        base = cat()
        theirs = copy.deepcopy(base)
        theirs["_meta"] = {"baseRev": 7, "editor": "alice"}  # stripped, never merged
        main = copy.deepcopy(base)
        main["_meta"] = {"rev": 7}
        merged, conflicts = self.merge(base, theirs, main)
        self.assertEqual(conflicts, [])
        self.assertEqual(merged["_meta"], {"rev": 8})

    def test_stray_baserev_on_main_does_not_fossilize(self):
        # an interim straight-to-main commit can leave {rev, baseRev} in main's _meta;
        # the Action's output must shed the baseRev
        base = cat()
        main = copy.deepcopy(base)
        main["_meta"] = {"rev": 7, "baseRev": 7}
        merged, conflicts = self.merge(base, copy.deepcopy(base), main)
        self.assertEqual(conflicts, [])
        self.assertEqual(merged["_meta"], {"rev": 8})

    def test_rev_starts_at_one_before_bootstrap(self):
        merged, conflicts = self.merge(cat(), cat(), cat())
        self.assertEqual(conflicts, [])
        self.assertEqual(merged["_meta"], {"rev": 1})

    # -- the conflict cases ---------------------------------------------------

    def test_same_field_two_ways_is_a_conflict(self):
        base = cat()
        theirs = copy.deepcopy(base)
        res(theirs, "R1")["oneLine"] = "alice's wording"
        main = copy.deepcopy(base)
        res(main, "R1")["oneLine"] = "bob's wording"
        merged, conflicts = self.merge(base, theirs, main)
        self.assertIsNone(merged)
        self.assertEqual(len(conflicts), 1)
        c = conflicts[0].as_dict()
        self.assertEqual((c["collection"], c["key"], c["field"]), ("resources", "R1", "oneLine"))
        self.assertEqual(c["theirs"], "alice's wording")
        self.assertEqual(c["main"], "bob's wording")

    def test_delete_vs_edit_is_a_conflict(self):
        base = cat()
        theirs = copy.deepcopy(base)
        theirs["resources"] = [r for r in theirs["resources"] if r["title"] != "R2"]
        main = copy.deepcopy(base)
        res(main, "R2")["oneLine"] = "edited on main"
        merged, conflicts = self.merge(base, theirs, main)
        self.assertIsNone(merged)
        self.assertEqual(len(conflicts), 1)
        self.assertIn("deleted", conflicts[0].kind())

    def test_rename_vs_edit_is_a_conflict(self):
        base = cat()
        theirs = copy.deepcopy(base)
        # the editor renames R2 -> "R2 revised"; renameRefs rewrote every reference
        res(theirs, "R2")["title"] = "R2 revised"
        res(theirs, "R1")["related"] = ["R2 revised"]
        theirs["edges"][0]["to"] = "R2 revised"
        main = copy.deepcopy(base)
        res(main, "R2")["oneLine"] = "edited meanwhile on main"
        merged, conflicts = self.merge(base, theirs, main)
        self.assertIsNone(merged)
        kinds = {(c.collection, c.key if not isinstance(c.key, tuple) else "->".join(c.key))
                 for c in conflicts}
        self.assertIn(("resources", "R2"), kinds)  # the delete side of the rename

    def test_rename_with_untouched_main_is_clean(self):
        base = cat()
        theirs = copy.deepcopy(base)
        res(theirs, "R2")["title"] = "R2 revised"
        res(theirs, "R1")["related"] = ["R2 revised"]
        theirs["edges"][0]["to"] = "R2 revised"
        main = copy.deepcopy(base)
        merged, conflicts = self.merge(base, theirs, main)
        self.assertEqual(conflicts, [])
        self.assertIsNone(res(merged, "R2"))
        self.assertIsNotNone(res(merged, "R2 revised"))
        self.assertEqual(res(merged, "R1")["related"], ["R2 revised"])
        self.assertEqual(merged["edges"][0]["to"], "R2 revised")

    def test_add_add_different_is_a_conflict(self):
        base = cat()
        theirs = copy.deepcopy(base)
        theirs["resources"].append(new_card("Same Title"))
        main = copy.deepcopy(base)
        clash = new_card("Same Title")
        clash["oneLine"] = "different content"
        main["resources"].append(clash)
        merged, conflicts = self.merge(base, theirs, main)
        self.assertIsNone(merged)
        self.assertIn("added on both sides", conflicts[0].kind())

    def test_duplicate_key_in_one_file_is_corrupt_input(self):
        base = cat()
        broken = copy.deepcopy(base)
        broken["resources"].append(copy.deepcopy(broken["resources"][0]))
        with self.assertRaises(ValueError):
            merge_payloads(base, broken, copy.deepcopy(base))

    def test_report_reads_like_english(self):
        base = cat()
        theirs = copy.deepcopy(base)
        res(theirs, "R1")["oneLine"] = "a"
        main = copy.deepcopy(base)
        res(main, "R1")["oneLine"] = "b"
        _, conflicts = self.merge(base, theirs, main)
        report = render_report(conflicts)
        self.assertIn('**resources** / "R1" / field `oneLine`', report)
        self.assertIn("the editor's version", report)

    # -- ground truth against the live catalogue -------------------------------

    def test_self_merge_of_live_catalogue(self):
        live = json.loads(LIVE.read_text(encoding="utf-8"))
        merged, conflicts = self.merge(copy.deepcopy(live), copy.deepcopy(live), copy.deepcopy(live))
        self.assertEqual(conflicts, [])
        for coll in ("topics", "resources", "tasks", "glossary"):
            self.assertEqual(merged[coll], live[coll], coll + " changed in a no-op merge")
        self.assertEqual(merged["edges"], live["edges"])
        # rev is whatever the live file currently carries, plus the bot's bump; pinning
        # a literal here just breaks the suite every time the catalogue is published.
        self.assertEqual(merged["_meta"], {"rev": live.get("_meta", {}).get("rev", 0) + 1})
        # the one intended correction: stale derived count fixed (was 27 in the live file)
        self.assertEqual(merged["meta"]["counts"]["edges"], len(live["edges"]))

    def test_rename_racing_a_reference_add_leaves_no_dangling_refs(self):
        """A renames a card; B (same base) points a DIFFERENT item at the old title.

        Both files are self-consistent, the items touched do not overlap, so the merge
        is clean -- and main would reference a resource that no longer exists, showing
        as a dead entry in the topic's "Start here" list. The merge must not publish
        that.
        """
        live = json.loads(LIVE.read_text(encoding="utf-8"))
        old = live["resources"][0]["title"]
        new = old + " (renamed)"

        base = copy.deepcopy(live)

        theirs = copy.deepcopy(live)                      # editor A: rename, refs rewritten
        theirs["resources"][0]["title"] = new
        for tp in theirs["topics"]:
            tp["startHere"] = [new if t == old else t for t in tp.get("startHere", [])]
        for r in theirs["resources"]:
            r["related"] = [new if t == old else t for t in r.get("related", [])]
        for e in theirs["edges"]:
            if e.get("from") == old: e["from"] = new
            if e.get("to") == old: e["to"] = new
        for tk in theirs["tasks"]:
            for s in tk.get("steps", []):
                if s.get("res") == old: s["res"] = new

        main = copy.deepcopy(live)                        # editor B: add a ref to the OLD title
        other = next(tp for tp in main["topics"] if old not in tp.get("startHere", []))
        other.setdefault("startHere", []).append(old)

        merged, conflicts = self.merge(base, theirs, main)
        self.assertEqual(conflicts, [], "rename vs unrelated reference-add should merge cleanly")

        titles = {r["title"] for r in merged["resources"]}
        self.assertIn(new, titles)
        self.assertNotIn(old, titles)
        for tp in merged["topics"]:
            for t in tp.get("startHere", []):
                self.assertIn(t, titles, "startHere points at a resource that does not exist")
        for tk in merged["tasks"]:
            for s in tk.get("steps", []):
                if s.get("res"):
                    self.assertIn(s["res"], titles, "task step points at a missing resource")
        for r in merged["resources"]:
            for t in r.get("related", []):
                self.assertIn(t, titles, "related points at a missing resource")
        for e in merged["edges"]:
            self.assertIn(e["from"], titles)
            self.assertIn(e["to"], titles)

    def test_live_catalogue_realistic_two_editor_day(self):
        live = json.loads(LIVE.read_text(encoding="utf-8"))
        base = copy.deepcopy(live)
        theirs = copy.deepcopy(live)
        theirs["resources"][0]["oneLine"] = "Alice rewrote this summary."
        main = copy.deepcopy(live)
        main["resources"][5]["readTime"] = 99
        merged, conflicts = self.merge(base, theirs, main)
        self.assertEqual(conflicts, [])
        self.assertEqual(merged["resources"][0]["oneLine"], "Alice rewrote this summary.")
        self.assertEqual(merged["resources"][5]["readTime"], 99)

    # -- CLI smoke -------------------------------------------------------------

    def test_cli_exit_codes_and_output(self):
        script = Path(__file__).resolve().parent / "merge_catalogue.py"
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            base, theirs, main = cat(), cat(), cat()
            res(theirs, "R1")["oneLine"] = "edited"
            for name, payload in (("base", base), ("theirs", theirs), ("main", main)):
                (td / (name + ".json")).write_text(json.dumps(payload), encoding="utf-8")
            out = td / "merged.json"
            r = subprocess.run([sys.executable, str(script), str(td / "base.json"),
                                str(td / "theirs.json"), str(td / "main.json"), "-o", str(out)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            merged = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(res(merged, "R1")["oneLine"], "edited")
            self.assertTrue(out.read_text(encoding="utf-8").endswith("\n"))

            # now force a conflict: exit 2, no output file written
            res(main, "R1")["oneLine"] = "clashing edit"
            (td / "main.json").write_text(json.dumps(main), encoding="utf-8")
            out2 = td / "merged2.json"
            rep = td / "report.md"
            r = subprocess.run([sys.executable, str(script), str(td / "base.json"),
                                str(td / "theirs.json"), str(td / "main.json"),
                                "-o", str(out2), "--report", str(rep),
                                "--json-report", str(td / "report.json")],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            self.assertFalse(out2.exists())
            self.assertIn("oneLine", rep.read_text(encoding="utf-8"))
            j = json.loads((td / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(j[0]["field"], "oneLine")


if __name__ == "__main__":
    unittest.main(verbosity=2)
