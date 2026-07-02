# Git Notes

## 2026-07-02 Pull recovery: diverged `main` + autostash conflict

Symptom: local `main` and `origin/main` had diverged:

```text
## main...origin/main [ahead 1, behind 1]
 M strata.md
```

Diagnosis:

- Local `main` had one local commit, `25a59cd` (`rm config`).
- `origin/main` had one remote commit, `4fe67b9` (`add parses`).
- The worktree also had an uncommitted edit that emptied `strata.md`.

Fix applied:

```bash
git pull --rebase --autostash origin main
```

The rebase completed and updated local `main` to match `origin/main`, but
re-applying the autostash conflicted in `strata.md`: upstream kept the shim and
added the roadmap link, while the stashed local change deleted the file
contents. Resolution was to keep the upstream `strata.md` shim, matching the
documentation contract. The original local deletion remains available in
`stash@{0}` as the autostash.

Lesson: when pulling with a dirty worktree, `--autostash` protects local edits
but may still require manual conflict resolution after the pull succeeds. Check
`git status` and `git stash list` before assuming the worktree is fully clean.

## 2026-06-10 `git pull` failure: no upstream + local ahead

Symptom: bare `git pull` failed with

```text
There is no tracking information for the current branch.
Please specify which branch you want to merge with.
```

Diagnosis (two separate facts, easy to conflate):

1. **No upstream tracking.** `git branch -vv` showed `main` without
   `[origin/main]`; `branch.main.remote` / `branch.main.merge` were unset.
   This is why bare `git pull` errored. Likely a side effect of the
   2026-06-08 corrupt-object recovery / re-fetch (see below), which left the
   local branch without tracking config.
2. **Nothing to pull anyway.** `git rev-list --left-right --count
   main...origin/main` showed `1 0`: local `main` was 1 commit *ahead* of
   `origin/main` and 0 behind. The unpushed local commit `a7ea445`
   ("Replace PDF RAG experiment with video/audio transcript CLI tool")
   deleted `rag_pdfs/`, `rag_langchain/`, `core/`, `git_notes.md` and
   rewrote `pyproject.toml`. The RAG code never left the remote — it was
   removed locally, so "pulling updates" could not bring it back.

Fix applied:

```bash
git branch --set-upstream-to=origin/main main   # restore tracking
git pull --ff-only                              # → Already up to date
git checkout origin/main -- rag_pdfs core rag_langchain git_notes.md
```

Lessons:

- After history surgery (force-push, object recovery, re-clone-like fetch),
  always re-check `git branch -vv` for missing upstream config.
- "Cannot pull updates" can mean "local is ahead with a deletion commit";
  check `rev-list --left-right --count` before assuming the remote is stale.
- Files deleted by a local commit are restorable per-path via
  `git checkout origin/main -- <path>` without touching local history.

## 2026-06-08 Push recovery

Goal: push the local repository state to `origin/main`, treating the local
workspace as authoritative and allowing remote-only updates to be overwritten.

Encountered errors:

- `fatal: detected dubious ownership in repository`
  - Git refused to operate on the WSL UNC path.
  - Fix: added this repository path to global `safe.directory`.

- `fatal: bad object HEAD`
  - `git status` failed because `HEAD` pointed at an empty/corrupt object:
    `39de875150b679b8d41e6a8a5eac375dcf8e7476`.

- `git fsck --full` reported empty or missing objects:
  - `00e42a926c349dfeed62bb8710758bb10f29e07c`
  - `090ad02cf249a564be331506f9e329d4516d630e`
  - `39de875150b679b8d41e6a8a5eac375dcf8e7476`
  - `f5f84e5b8e85ee8a94776edda23f48f4688f0a1c`

- Initial `git fetch origin` failed with:
  - `error: failed to read delta-pack base object 00e42a926c349dfeed62bb8710758bb10f29e07c`
  - `fatal: unpack-objects failed`

Recovery actions:

- Moved the empty object files into `.git/corrupt-object-backup-20260608-002000`.
- Re-ran `git fetch origin`; this completed successfully.
- Verified `git fsck --full` completed without errors after recovery.
- Confirmed local `main` was at `39de875150b679b8d41e6a8a5eac375dcf8e7476`.
- Confirmed `origin/main` had advanced to `341c38e3b7fa8eaae32eee4db85ded77ccb92319`
  with remote-only commit `Delete rag_langchain directory`.

Resolution:

- Committed the current local workspace as
  `9e236465f9749b58cfaa4ec645dc4e4e9f143c2e`.
- Pushed local `main` to `origin/main` with `--force-with-lease`.
- Remote `main` was force-updated from
  `341c38e3b7fa8eaae32eee4db85ded77ccb92319` to
  `9e236465f9749b58cfaa4ec645dc4e4e9f143c2e`, overwriting the remote-only
  deletion commit.
