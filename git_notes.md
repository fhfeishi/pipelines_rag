# Git Notes

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

Resolution plan:

- Commit the current local workspace, including this note.
- Push local `main` to `origin/main` with force-with-lease so the remote-only
  deletion commit is overwritten by the local repository state.
