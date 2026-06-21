# SPEC — F5: real version label in UI (kill hardcoded "v2")

**Problem:** top of app shows `v2`, not the real release. No `__version__` exists anywhere.
Tester can't tell which build they're running.

## Task
1. Create a single source of truth. Add `__version__ = "1.2.1-beta"` to a new
   `_version.py` at repo root (one line + a comment). This is the canonical string.
2. Import it where the UI header is rendered. Grep the UI markup (HTML/JS served by
   `run.py`) for the literal `v2` label — replace it so the displayed version comes from
   `__version__` (inject it into the template / pass it to the front-end via an existing
   endpoint or template var; mirror how other dynamic values reach the UI).
3. If the build embeds a version anywhere else (check `steadycut.spec`), point it at the
   same `__version__` — no second hardcoded copy.

## Acceptance
- Launch app → header shows `v1.2.1-beta` (not `v2`).
- `grep -rn "v2" run.py <ui files>` returns no version label.
- Bumping `_version.py` and relaunching changes the header. One edit, one place.

## Out of scope
Auto-deriving from git tag (nice-to-have, later). One manual string is enough for launch.
