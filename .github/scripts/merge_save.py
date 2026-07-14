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
import make_fallback

DATA = "ctls-data.json"
MAX_WALK = 500          # how far back in main history a base rev may sit
PUSH_ATTEMPTS = 6  # merges run per-branch now, so genuine push races are expected
EDITOR_URL = "https://rdp-ctls.github.io/Resource_Finder/?edit"
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"

# An editor save may change ONLY the catalogue file. Anything else on a save branch (a workflow,
# a script, a stray top-level file) has no business in the publish path; a .github/ path would be a
# CI-code-execution vector. See the path guard in main().
ALLOWED_SAVE_PATHS = {DATA}

# Floor guard against a catalogue wipe. The bot is the only writer of main and runs unattended, so a
# save that empties the catalogue must never publish silently. These are the collections whose loss
# means the public site was gutted; a single save that drops more than MAX_DROP_FRACTION of either
# (or empties it outright) is treated as a truncated/emptied file and held for a human, not published.
FLOOR_COLLECTIONS = ("resources", "topics")
MAX_DROP_FRACTION = 0.34   # one save removing more than a third of a collection is suspect, not routine


def sh(*args, check=True):
    r = subprocess.run(list(args), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("command failed: %s\n%s" % (" ".join(args), r.stderr.strip()))
    return r


def file_at(ref):
    return sh("git", "show", "%s:%s" % (ref, DATA)).stdout


def changed_paths(sha):
    """Repo-relative paths the save commit changed vs its OWN first parent, or None if undeterminable.
    Compared against the commit's parent (not current main) on purpose: a stale save branch
    legitimately carries an older copy of .github/, and diffing it against a newer main would flag
    those files as 'changed' and block every save. A commit's diff against its parent is exactly what
    that push introduced."""
    r = sh("git", "diff", "--name-only", "%s^" % sha, sha, check=False)
    if r.returncode != 0:      # e.g. a root commit with no parent; cannot tell, so do not block
        print("could not compute changed paths for %.10s, skipping path guard" % sha)
        return None
    return {p for p in r.stdout.split("\n") if p.strip()}


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

    # Path guard (defense in depth): a save must touch ONLY the catalogue file. Anything else,
    # especially a .github/ workflow or script, is refused. IMPORTANT LIMITATION: for a push to
    # save/**, GitHub has ALREADY chosen the workflow file from the pushed branch, so a poisoned
    # merge-saves.yml would run its own steps before this check ever executes. This guard is NOT the
    # primary control; it catches an accidental extra file and a tampered merge_save.py when the yaml
    # is unchanged. The real controls live outside this repo: a Contents-only, single-repo PAT that
    # cannot write .github/ at all, and a flow that commits ctls-data.json via a single-file PUT.
    changed = changed_paths(sha)
    if changed is not None and changed - ALLOWED_SAVE_PATHS:
        extra = ", ".join("`%s`" % p for p in sorted(changed - ALLOWED_SAVE_PATHS))
        open_issue("Needs a look: a save from %s changed files other than the catalogue" % editor,
                   issue_header(editor, branch, sha, "?", "?")
                   + "This save touched: %s. A normal editor save changes only `%s`, so nothing was "
                     "published. If you did not do anything unusual, tell whoever manages the repo, "
                     "because a save branch carrying other files can mean the upload credential is "
                     "being misused.\n" % (extra, DATA) + HOW_TO_FIX)
        return 0

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
        stripped_theirs = {k: v for k, v in theirs.items() if k != "_meta"}
        if stripped_merged == stripped_main:
            # A genuine replay (the flow double-fired, or the same content arrived twice) is
            # boring: what the editor sent is already what main says. Skip it quietly.
            if stripped_theirs == stripped_main:
                print("no-op: save is identical to main (double-fire or replay), skipping")
                return 0
            # Otherwise the editor DID send something different from main, yet merging it
            # changes nothing -- because every field she touched still matches her base, so
            # the three-way merge reads it as "she left this alone" and keeps main's newer
            # value. That is what happens when someone reverts a field to the value it had
            # when their page loaded (e.g. undoing a title they published minutes ago from a
            # tab that never moved its base forward). It used to be skipped in silence while
            # her editor showed a green "it is live" banner. Never again: say so.
            open_issue("Needs a look: a save from %s changed nothing on the live site" % editor,
                       issue_header(editor, branch, sha, base_rev, main_rev)
                       + "The save was built on published version %s, but the live site is on "
                         "version %s. Every field it changes still holds its version-%s value, "
                         "so the merge treated them as untouched and kept the newer text that "
                         "is already live. **Nothing was published and nothing was lost** -- but "
                         "this edit did NOT take effect.\n\nUsual cause: an editor tab left open "
                         "across an earlier publish, then used to undo that earlier change. Fix: "
                         "reload the editor (so it starts from version %s) and make the change "
                         "again.\n" % (base_rev, main_rev, base_rev, main_rev) + HOW_TO_FIX)
            return 0

        # Floor guard: never publish a save that empties or guts the catalogue. An emptied-but-valid
        # ctls-data.json (collections present but []) merges cleanly to "delete almost everything"
        # with zero conflicts and would otherwise be pushed straight to the live site in silence.
        # A truncated file that happens to close as valid JSON, a mis-click bulk delete, or a base
        # that predates most of main's content all land here. Hold it for a human instead of wiping.
        for coll in FLOOR_COLLECTIONS:
            was = len(main_doc.get(coll) or [])
            now = len(merged.get(coll) or [])
            if was and (now == 0 or (was - now) / was > MAX_DROP_FRACTION):
                open_issue("Needs a look: a save from %s would delete most of the catalogue" % editor,
                           issue_header(editor, branch, sha, base_rev, main_rev)
                           + "Publishing this save would cut the **%s** list from %d to %d. That is too "
                             "large a drop for one edit, so nothing was published, in case a truncated "
                             "or emptied file arrived by accident. **Nothing was lost** and the live "
                             "site is unchanged.\n\nIf you really did mean to remove that much, make the "
                             "deletions again in the editor in smaller saves, or ask whoever manages the "
                             "repo to publish it by hand.\n" % (coll, was, now) + HOW_TO_FIX)
                return 0

        Path(DATA).write_text(json.dumps(merged, indent=1, ensure_ascii=False) + "\n",
                              encoding="utf-8")
        # Keep the offline fallback in step with what we are about to publish. It drifted
        # for months because nothing regenerated it, and a fallback that answers from a
        # stale catalogue is worse than one that fails loudly. "commit -am" below picks it
        # up, so the mirror always ships in the same commit as the data it mirrors.
        make_fallback.build()
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
