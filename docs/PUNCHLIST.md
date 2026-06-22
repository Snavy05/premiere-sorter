# SteadyCut — Punch List

Source: tester feedback from **Hoàng** (event videographer, 17 Jun 2026) + run logs in
`SteadyCut_Feedback/`. Hardware: a7S III on RS4 Pro gimbal, 4K60 10-bit, M1 Pro 16GB.

Tester verdict: **"Tiếp tục, có lực kéo"** (continue, has traction) · **"rất cần cực cần"**
(strong demand) · willing to pay **$50 one-time** with fixes + updates + warranty.

> **Reconciled against the codebase on 2026-06-22.** Many items the original list framed as
> "to do" are already implemented — this version reflects what the code actually does, with
> file:line evidence. Status legend:
> **✅ DONE** (in code) · **🔬 VERIFY** (done in code, unproven as a shipped bundle / on
> footage) · **🟡 PARTIAL** (started, gaps remain) · **⬜ OPEN** (not built).

---

## Boot work-stream — ✅ DONE in code · 🔬 unproven as a shipped bundle

All five boot blockers are fixed in source. The remaining risk is that they have **never
been proven inside a packaged `.app` on a clean machine** — that proof is the launch gate
(DEV-PLAN §6.1), not more code.

- **B1 — ffmpeg download ✅** zip variant + stdlib `zipfile`; `py7zr`/BCJ2 path removed.
  `ffmpeg_helper.py:142,163,172`.
- **B2 — cache trusts bad binary ✅** every resolve runs `<bin> -version` + `chmod +x`,
  self-heals on failure. `ffmpeg_helper.py:64,78,220,282,296,327`.
- **B3 — ship ffmpeg inside app ✅** `_bundle_dir()` resolves `sys._MEIPASS/bin` first;
  `steadycut.spec` bundles `./bin`. `ffmpeg_helper.py:94-104`.
- **B4 — wrong "No clips" error ✅** `generate_proxy` catches `FileNotFoundError`,
  `PermissionError`, `OSError` distinctly; abort distinguishes "0 files" from "N files, all
  failed". `pipelinev3.py:263,266,273` · `steadycut_pipeline.py:375,391`.
- **B5 — silent first-boot crash ✅** `faulthandler` + `sys.excepthook` + thread hook write
  a crash file before UI init. `run.py` (crash handler installed first).

**🔬 Launch gate (the only boot work left):** build with `./bin` populated, run on a clean
account (no brew, no PATH ffmpeg, wiped cache), process a real batch, confirm ffmpeg
resolves from `_MEIPASS/bin`. Highest-risk unknown.

---

## Accuracy work-stream

- **A1 — multi-shot splitting ✅** `segment_shots` (`pipelinev3.py:893`) detects intra-file
  cuts (motion-spike transitions + T1.5b coherence gate + T1.5c reversal split). Wired and
  **on by default** (`multi_shot=True`, `steadycut_pipeline.py:276,914`): every shot not
  already covered by a selected window is emitted as its own `[shotN]` "review" clip.
  🔬 **Verify on Hoàng footage** + 🟡 **gap:** uncovered shots ship **uncut** (`label_reason
  :"review"`), not trimmed to their own stable window. Combining per-shot trimming with A2
  is the real remaining lever — a Claude-designed spec, not a wiring task.
- **A2 — all qualifying stable windows ✅** pipeline calls `find_stable_windows` (plural,
  returns ALL qualifying windows + `_merge_split_windows` glue) at `steadycut_pipeline.py
  :657,835`. No longer "one window per clip."
- **A3 — over-trims head/tail 🟡** head/tail trim are now user-tunable
  (`head_trim_frames`/`tail_trim_frames`, presets default 5), but there is **no algorithmic
  over-trim fix**. Open as a tuning/eval question, not code.
- **A4 — full moving shots ⬜ OPEN** no "full-move / continuous-move" detection exists
  (grep: none). A continuous gimbal move still gets cut mid-move and isn't flagged as a full
  shot. This is the T1 cut-on-action cluster (8619/20/21) root cause — a separate algorithm,
  Claude lane.

---

## Tuning work-stream

- **T1 — adaptive sensitivity ✅ CLOSED, default k=3.0.** `adaptive_threshold = median +
  k·MAD`. Sweep closed 2026-06-22: k=2.5/2.0 triaged in Premiere and rejected (scorecard
  preferred 2.5 but undercounts over-merge + cut-on-action as near-success V3; human triage
  is ground truth). Now fully reachable: `--adaptive` / `--sensitivity` CLI flags
  (`steadycut_pipeline.py:1263,1266`, default 3.0) **and** UI (`run.py` `ProcessRequest
  .sensitivity=3.0`). The two residual pains are **not k-tunable**: over-merge of high-motion
  clips (needs per-clip threshold cap or forced internal split — DEV-PLAN §3) and
  cut-on-action pans (= **A4**).

---

## Feature work-stream

- **F1 — stereo one track 🟡 IN PROGRESS.** `xml_assembler.py` emits a single 2-channel
  `<audio>` block + `premiereChannelType="stereo"` clipitems + a stereo output bus
  (`:245,291,361,474`), but carries an open `TODO(claude): F1 stereo` (`:475`) — needs a
  real Premiere-export reference XML for a single stereo track to confirm the output-bus
  shape (without it a 2-channel clip has nowhere to route and Premiere drops the link).
  Spec: `for_claude_sessions/SPEC-f1-stereo.md`.
- **F2 — keep failed clips on timeline ✅** `keep_failed_clips=True` default
  (`steadycut_pipeline.py:273`); rich `label_reason` taxonomy drives Premiere labels.
  🔬 **Verify** the failed/skipped clips actually land with a distinct label colour
  (colour-map not found in `xml_assembler.py` grep — confirm where `label_reason` → colour).
- **F3 — multi-shot colour grouping ⬜ OPEN** no sibling-grouping colour: A1's `[shotN]`
  siblings get no shared colour so they aren't visually recognizable as one source. Pairs
  naturally with F2's colour work.
- **F4 — people labeler 🟡 PARTIAL** head-count classification exists internally
  (`person_count`, person/crowd/broll — `shot_classifier.py:514`, `steadycut_pipeline.py
  :1073`), but there is **no user-facing dropdown to pick N + colour per choice**. The
  classification half is done; the UI labeler is not.
- **F5 — version label ✅ DONE** `_version.py` is the single source of truth; `/api/version`
  endpoint (`run.py:282`); UI fetches and renders `v${version}` into `#appVersion`
  (`static/index.html:667,1259`). No hardcoded `v2`.
- **F6 — per-run settings + results sidecar 🟡 PARTIAL** `_write_dev_report` writes
  `*_dev_report.json` (per-clip windows + COA peaks; `steadycut_pipeline.py:223,232`), but
  it is **not** the full reproducible run sidecar F6 asks for: missing app version,
  timestamp, the complete settings set, input-batch manifest, timings, and fail counts in
  one file. Prototype of the full shape lives in `for_claude_sessions/run_adaptive_batch.py`
  (`*_runsettings.json`). Promote that into `run_pipeline`.

---

## Working positives (do not regress)
- Speed: 12m48s for 50 clips ("nhanh").
- Premiere import: clean.
- Audio lands on the timeline (collapsing to one stereo track = F1, in progress).

---

## Priority order (reconciled)
1. **🔬 Prove the bundled build end-to-end** — B1–B5 are done in code; the shipped-bundle
   proof on a clean machine is the actual ship gate (DEV-PLAN §6.1).
2. **F1 stereo** — finish the output-bus shape (needs reference XML); high-value, half-built.
3. **A1 verify + A1×A2 per-shot trimming** — confirm A1 fires on Hoàng footage, then decide
   whether to trim each emitted shot to its own stable window (Claude-designed spec).
4. **A4 full moving shots** — the cut-on-action root cause; new algorithm, Claude lane.
5. **F2 colour verify · F3 sibling colour · F4 user labeler dropdown** — labeling polish.
6. **F6** — promote the run sidecar from prototype into `run_pipeline`.
7. **A3** — over-trim tuning (eval, not code).

> UI overhaul (React/Vite dashboard + Projects) is a separate track — see DEV-PLAN §7 and
> PR #1. Deferred behind the launch gate.
