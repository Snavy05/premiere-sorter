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

---

## Review fix (round 2) — Apple plist version must be numeric
First pass set `CFBundleShortVersionString` and `CFBundleVersion` to `"1.2.1-beta"`
(`steadycut.spec:211-212`). Apple requires these be **dotted-numeric only** (1–3 ints);
the `-beta` suffix makes them invalid → codesign/notarization warns or rejects. Regression:
they were `"1.2.1"` (valid) before this branch.

Fix:
1. In `_version.py`, add a second constant — numeric only, no suffix:
   ```python
   __version__ = "1.2.1-beta"      # display label (UI header)
   __bundle_version__ = "1.2.1"    # Apple plist — dotted-numeric only, no suffix
   ```
2. In `steadycut.spec`, import `__bundle_version__` and use it for BOTH `CFBundleShortVersionString`
   and `CFBundleVersion`. Leave the UI header using `__version__` (it should still show `v1.2.1-beta`).

**Acceptance:**
- `grep CFBundle steadycut.spec` shows both keys = `__bundle_version__` (resolves to `1.2.1`, no `-beta`).
- UI header still renders `v1.2.1-beta`.
- `python -c "from _version import __version__, __bundle_version__; print(__version__, __bundle_version__)"`
  → `1.2.1-beta 1.2.1`.
