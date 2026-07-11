#!/usr/bin/env python3
"""Merge-bot driver: publish one editor save from a save/<name> branch into main.

Run by .github/workflows/merge-saves.yml on every push to save/**, from a full
checkout of main, with env:
  SAVE_BRANCH  the pushed branch (e.g. save/jsmith)
  SAVE_SHA     the exact commit the push landed (github.event.after) - always
               read by SHA, never by branch head, which may have moved since
  GH_TOKEN     for `gh issue create` on collisions

What it does:
  1. Read the saved ctls-data.json from SAVE_SHA and its _meta.baseRev.
  2. Resolve baseRev to the NEWEST main commit whose file carries that rev.
  3. Three-way merge (base vs theirs vs main) with merge_catalogue.py.
  4. Clean -> commit to main as the Actions bot (rev bumped, pretty-printed),
     retrying against fresh main if the push races another writer.
     Byte-identical result -> skip (absorbs double-fired triggers).
     Collision / unreadable save / unresolvable base -> open a GitHub issue
     with the plain-language report. Never a PR: merging a whole-file PR the
     GitHub way would bypass this merge and clobber main.

Exit 0 on every handled outcome (issues are the visibility; a red job would
cry wolf). Non-zero only on infrastructure failure (e.g. push kept racing).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from merge_catalogue import merge_payloads, render_report

DATA = "ctls-data.json"
MAX_WALK = 500          # how far back in main history a base rev may sit
PUSH_ATTEMPTS = 3
EDITOR_URL = "https://rdp-ctls.github.io/Resource_Finder/?edit"
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"


def sh(*args, check=True):
    r = subprocess.run(list(args), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("command failed: %s\n%s" % (" ".join(args), r.stderr.strip()))
    return r


def file_at(ref):
    return sh("git", "show", "%s:%s" % (ref, DATA)).stdout


def resolve_base(base_rev):
    """Newest main commit whose ctls-data.json carries _meta.rev == base_rev."""
    shas = sh("git", "rev-list", "--max-count=%d" % MAX_WALK, "HEAD", "--", DATA).stdout.split()
    for sha in shas:
        try:
            doc = json.loads(file_at(sha))
        except (ValueError, RuntimeError):
            continue
        meta = doc.get("_meta") or {}
        if meta.get("rev") == base_rev:
            return sha, doc
    return None, None


def open_issue(title, body):
    # one open issue per problem: skip if an identical open title exists
    r = sh("gh", "issue", "list", "--state", "open", "--search", title,
           "--json", "title", check=False)
    try:
        if any(i.get("title") == title for i in json.loads(r.stdout or "[]")):
            print("issue already open, not duplicating: %s" % title)
            return
    except ValueError:
        pass
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(body)
        path = f.name
    sh("gh", "issue", "create", "--title", title, "--body-file", path)
    os.unlink(path)
    print("opened issue: %s" % title)


def issue_header(editor, branch, sha, base_rev, main_rev):
    return ("An edit saved by **%s** could not be published automatically.\n\n"
            "- save branch: `%s`, commit `%.10s`\n"
            "- based on published version: %s; version live now: %s\n\n"
            % (editor, branch, sha, base_rev, main_rev))


HOW_TO_FIX = ("\n## How to fix\n\n"
              "Open the affected card in the [live editor](%s), re-apply the wording that "
              "should win, and save normally; the publish loop takes it from there. Then close "
              "this issue.\n\n**Do not merge the save branch with git**: that would replace the "
              "whole catalogue and lose the other editor's work.\n" % EDITOR_URL)


def main():
    branch = os.environ["SAVE_BRANCH"]
    sha = os.environ["SAVE_SHA"]
    if not sha or set(sha) <= {"0"}:
        print("branch deletion push, nothing to do")
        return 0
    editor = branch.split("/", 1)[-1]
    sh("git", "fetch", "origin", sha, check=False)  # ensure the commit is local

    try:
        theirs = json.loads(file_at(sha))
    except (ValueError, RuntimeError) as e:
        open_issue("Needs a look: a save from %s could not be read" % editor,
                   issue_header(editor, branch, sha, "?", "?")
                   + "The saved file is not valid JSON (%s), so nothing was published."
                     % e.__class__.__name__
                   + HOW_TO_FIX)
        return 0

    base_rev = (theirs.get("_meta") or {}).get("baseRev")

    for attempt in range(PUSH_ATTEMPTS):
        sh("git", "fetch", "origin", "main")
        sh("git", "reset", "--hard", "origin/main")
        main_doc = json.loads(Path(DATA).read_text(encoding="utf-8"))
        main_rev = (main_doc.get("_meta") or {}).get("rev")

        if base_rev is None:
            open_issue("Needs a look: a save from %s arrived without a base version" % editor,
                       issue_header(editor, branch, sha, "none recorded", main_rev)
                       + "Without knowing which published version the editor started from, a "
                         "safe merge is impossible, so nothing was overwritten. Usual cause: a "
                         "stale editor tab from before the base-version update, or a hand-built "
                         "file.\n" + HOW_TO_FIX)
            return 0

        base_sha, base_doc = resolve_base(base_rev)
        if base_doc is None:
            open_issue("Needs a look: a save from %s references an unknown base version" % editor,
                       issue_header(editor, branch, sha, base_rev, main_rev)
                       + "No commit on main carries published version %s (checked the last %d "
                         "data commits), so a safe merge is impossible and nothing was "
                         "overwritten.\n" % (base_rev, MAX_WALK) + HOW_TO_FIX)
            return 0

        merged, conflicts = merge_payloads(base_doc, theirs, main_doc)
        if conflicts:
            first_key = conflicts[0].key
            if isinstance(first_key, tuple):
                first_key = " -> ".join(first_key)
            open_issue('Needs a look: %s\'s change to "%s" collided with a newer edit'
                       % (editor, first_key),
                       issue_header(editor, branch, sha, base_rev, main_rev)
                       + render_report(conflicts) + HOW_TO_FIX)
            return 0

        stripped_merged = {k: v for k, v in merged.items() if k != "_meta"}
        stripped_main = {k: v for k, v in main_doc.items() if k != "_meta"}
        if stripped_merged == stripped_main:
            print("no-op: save adds nothing new to main (double-fire or replay), skipping")
            return 0

        Path(DATA).write_text(json.dumps(merged, indent=1, ensure_ascii=False) + "\n",
                              encoding="utf-8")
        sh("git", "-c", "user.name=%s" % BOT_NAME, "-c", "user.email=%s" % BOT_EMAIL,
           "commit", "-am",
           "Publish editor save (rev %s, from %.10s)" % (merged["_meta"]["rev"], sha))
        push = sh("git", "push", "origin", "HEAD:main", check=False)
        if push.returncode == 0:
            print("published rev %s from %s" % (merged["_meta"]["rev"], branch))
            return 0
        print("push raced another writer (attempt %d), remerging against fresh main"
              % (attempt + 1))

    print("could not push after %d attempts" % PUSH_ATTEMPTS, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
