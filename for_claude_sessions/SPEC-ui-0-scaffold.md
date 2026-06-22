# SPEC — UI-0: scaffold the React/Vite frontend (non-destructive)

**Context:** UI overhaul track (DEV-PLAN §7). We are porting the SteadyCut UI to
React/Vite, building to the v2 dashboard mockup. This is step 0: stand up the toolchain
only. **Branch: `design/ui-overhaul`** (continue on it; do not branch to main).

**Hard rule — do NOT clobber the shipping UI.** The current `static/index.html` is the
live app the launch gate ships. This spec must NOT modify or overwrite `static/`,
`run.py`, `build.sh`, `build.bat`, `.github/workflows/build.yml`, or `steadycut.spec`.
The cutover that replaces `static/` happens in a later spec, only after the port works.

## Task

1. Create a new Vite + React + TypeScript project in `ui/` at repo root. Use the standard
   `react-ts` template. Result: `ui/package.json`, `ui/vite.config.ts`, `ui/tsconfig.json`,
   `ui/index.html`, `ui/src/main.tsx`, `ui/src/App.tsx`.
2. In `ui/vite.config.ts`:
   - `base: "./"` (relative asset paths — required for the webview / `file://`).
   - Build `outDir` stays the Vite default **`dist`** (i.e. `ui/dist`) for now. Do **not**
     point it at `../static` yet — that is the cutover spec's job.
   - Add a dev proxy so the browser dev server can reach the running backend:
     `server.proxy` maps `/api` → `http://127.0.0.1:8765` (`changeOrigin: true`).
3. Replace the template `ui/src/App.tsx` with a minimal placeholder that proves the proxy
   works: render the text `SteadyCut UI — scaffold OK` and, on mount, `fetch("/api/ffmpeg-status")`
   and show the returned `status` string (or `"backend offline"` if the fetch throws).
   No styling, no design system yet — that arrives in SPEC-ui-2.
4. Add `ui/node_modules/` and `ui/dist/` to the repo-root `.gitignore` (append; do not
   rewrite existing entries).
5. Do not install extra deps beyond the Vite template (no router, no state lib yet).

## Acceptance

- `cd ui && npm install` succeeds.
- `cd ui && npm run build` emits `ui/dist/index.html` + `ui/dist/assets/*` with **relative**
  (`./assets/...`) paths in the HTML.
- `cd ui && npm run dev` serves on its dev port; opening it shows `SteadyCut UI — scaffold OK`.
  With `python run.py` (or uvicorn on :8765) also running, the page shows the ffmpeg status
  string (proxy reaches `/api/ffmpeg-status`); with the backend down it shows `backend offline`.
- `git status` shows new files only under `ui/` plus the `.gitignore` edit. `static/`,
  `run.py`, `build.sh`, `build.bat`, `steadycut.spec`, `.github/` are **unchanged**
  (`git diff --name-only` lists none of them).

## Out of scope (later specs)

- Pointing `outDir` at `../static` and replacing `static/index.html` → **SPEC-ui-cutover**.
- Wiring `npm run build` into `build.sh`/`build.bat`/CI → **SPEC-ui-cutover**.
- The `HostBridge` abstraction → **SPEC-ui-1-bridge** (Claude designs).
- Components / dashboard shell / DESIGN.md tokens → **SPEC-ui-2-shell**.
- Projects feature → **SPEC-ui-3-projects**.

If you hit a decision this spec doesn't answer (e.g. which state library, how the bridge
should be shaped), STOP and leave `# TODO(claude):` — do not improvise architecture.
