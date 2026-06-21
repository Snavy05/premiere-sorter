# SteadyCut — Development Plan & Workflow

_Last updated: 2026-06-22. Owner: Snavy05._

---

## 0. Workflow — Claude Code + Cursor Pro (cost split)

**Goal:** stop burning Claude credits on mechanical work. Rule of thumb:
**Claude decides + judges, Cursor types.**

| Work | Tool | Why |
|---|---|---|
| Architecture, root-cause diagnosis, eval interpretation, A/B design | **Claude Code (Opus)** | High reasoning payoff |
| Implementing a *decided* spec, CLI wiring, boilerplate, test scaffolds, single-fn edits, docstrings | **Cursor Pro** | Flat-rate, mechanical |
| Live UI iteration, autocomplete, rename, format | **Cursor Pro** | Tab/agent covers it |
| Diff review of Cursor output + running eval | **Claude Code** (short) | Cheap, high-value gate |

**Handoff mechanism** (`for_claude_sessions/` is the bridge):
1. Claude writes spec → `for_claude_sessions/SPEC-<task>.md` (exact files, fn signatures, acceptance test).
2. Cursor implements from spec — its credits, not Claude's.
3. Claude reviews diff + runs eval — targeted, short.

**Test:** if "I already know exactly what code to write" → Cursor. If "why is this happening / what should we build" → Claude.

### Credit-saving habits inside Claude Code
- **Window-quality batches: always `skip_classification=True`** → ~1min not ~10min (YOLO labels don't change clip in/out points).
- **Reuse proxies** (`skip_proxies=True`) — never regenerate.
- Don't re-read files already in context; prefer Grep/targeted Read.
- Delegate mechanical edits to `cavecrew-builder` subagent (compressed output).
- Write a run-settings **sidecar** (F6) so reruns are comparable without re-deriving by hand.

---

## 1. Current state (v1.2.1-beta)

- Branch `main`, HEAD `aa7ed57`, tag `v1.2.1-beta` now points at HEAD (fixed this session).
- Local build works: `dist/SteadyCut.app` (775M). Build needs venv (`build.sh` calls `python`/`pip`;
  system only has `python3`). torch must be installed CPU-first (CI order) or pip resolver backtracks on Py 3.13.
- ffmpeg: GUI `.app` doesn't inherit shell PATH → runtime download fails on SSL. Workaround: brew
  ffmpeg/ffprobe copied to `~/Library/Application Support/SteadyCut/bin/` (cache, step 1 in resolver).
  Real fix = punchlist B1–B3 (bundle static ffmpeg).

---

## 2. KEY FINDING — adaptive thresholding is a net win

Field test, Hoàng footage, same proxies reused (A/B, manually triaged in Premiere):

| metric | fixed 1.5px (baseline) | **adaptive sens=3.0** |
|---|---|---|
| windows | 135 | 130 |
| **hard fail (junk V2)** | 17% | **9%** |
| **usable-after-trim (V1+V3)** | 83% | **91%** |
| boundaries V3 | 36% | 51% |
| usable V1 | 47% | 40% |

**Junk halved.** Adaptive trades unrecoverable junk for fixable boundaries (V3 = a nudge, V2 = dead).
8635 ("big chunk cut off") fixed: recovered a 28.5s primary window (was a 116f / 4% sliver at fixed 1.5px).

### Root cause of fixed-threshold collapse
`DEFAULT_THRESHOLD_PX=2.0` (user ran 1.5) sits **below** the gimbal footage's median motion (~4.3px/frame),
so most frames fail "stable" → primary window collapses; `segment_shots` rescues content onto V3.
`adaptive_threshold = median + sensitivity·MAD` fits each clip instead.

### `adaptive` is NOT reachable from the CLI
`run_pipeline(..., adaptive=, sensitivity=)` exists, but `main()` (`steadycut_pipeline.py:1396`) never
passes them, and there's no argparse flag. Drove it via `for_claude_sessions/run_adaptive_batch.py`.
**TODO (Cursor):** add `--adaptive` / `--sensitivity` CLI flags + UI toggle (`run.py:229` default False).

---

## 3. Open regressions from adaptive (sens=3.0 too loose on motion-heavy clips)

Same knob, two symptoms. `adaptive_threshold = median + 3·MAD` runs too high on high-motion clips,
and the threshold IS the "stable" definition:

- **Stopped multi-windowing** (8580 thr=10.4px, 8603 thr=4.6px): threshold exceeds the clip's whole
  motion range → ONE window @ 100% coverage, internal shot boundaries no longer split.
- **Action obfuscation** (8619 thr=5.6, 8620 thr=3.1, 8621 thr=2.8): threshold admits 3–6px/frame
  pans INTO the "stable" window → window contains visible motion; shots don't separate cleanly.
- (8602 is different: low-motion med=0.8, a junk issue, not merging.)

**Trade-off, not a free fix:** lowering sensitivity to fix these RE-breaks 8635 (needs ~10px; at 4px
its coverage drops to 20%). No single global value wins both — this is the **Tier-2 intent gap**.

Glue (`_merge_split_windows:843`) only merges windows ≤6 frames apart with gap motion `< threshold_px`,
and **never checks reversals**. Glue-victims here have large gaps (>6f) so glue isn't the direct cause —
but the no-reversal-veto is still a latent bug worth fixing.

---

## 4. Next steps (priority order)

1. **Sensitivity sweep** (Claude — needs reasoning + scoring). Re-run batch at k=2.0 and 2.5 with
   `skip_classification=True` (~1min each). Triage in Premiere → score with `scripts/eval_xml.py` →
   find batch-optimal. Expect 8635-recovery vs over-merge trade to surface a sweet spot.
2. **Glue reversal-veto + threshold cap** (Claude writes spec → Cursor implements → Claude reviews):
   - `_merge_split_windows`: veto merge if `detect_reversals` flagged the gap.
   - Optional per-clip cap on `adaptive_threshold` (cap may hurt 8635 — test it).
   - Regression test against the 16-clip ground-truth set (no keeper-recall loss).
3. **Wire `--adaptive` / `--sensitivity` CLI + UI toggle** (Cursor, mechanical).
4. **Punchlist F5** (version label sourced from real version string) — Cursor.
5. **Punchlist F6** (per-run settings+results sidecar) — Cursor; prototype already in
   `run_adaptive_batch.py` (writes `*_runsettings.json`).
6. Punchlist boot blockers **B1–B5** (ffmpeg bundling etc.) remain the ship-gate for end users.

---

## 6. LAUNCH checklist — now (2026-06-22) → Fri 2026-07-03

Ship-gate **boot blockers B1–B5 are DONE in code** (verified 6/22): B1 zip download +
B2 `_runs_ok` validation (`ffmpeg_helper.py`), B3 spec bundles `./bin`, B4 PermissionError
catch (`pipelinev3.py:266`), B5 faulthandler+excepthook (`run.py`). Never proven as a
shipped bundle — that's the gate.

Must-do, ordered (defer everything not here: A2–A4, F3, F4, F6, glue reversal-veto):
1. **Prove the bundled build end-to-end** (Claude+user). Build with `./bin` populated; run on
   a clean acct (no brew, no PATH ffmpeg, wiped cache dir); process real batch; ffmpeg must
   resolve from `_MEIPASS/bin`. Highest-risk unknown — do Monday, not Friday.
2. **Lock accuracy default** — finish sweep triage (T1, see PUNCHLIST), pick k, set as
   default. Wiring spec: `SPEC-cli-ui-adaptive.md`.
3. **F1 stereo one-track** — `SPEC-f1-stereo.md` (failed before; has escalation-to-Claude rule).
4. **F5 version label** — `SPEC-f5-version-label.md`.
5. **Package + ship to Hoàng**, get go/no-go on locked-k batch.

### Cursor bridge (LIVE as of 6/22)
- `.cursorrules` (repo root) pins Cursor to its lane: implement specs, don't redesign,
  leave `# TODO(claude):` on reasoning calls.
- Specs in `for_claude_sessions/SPEC-*.md`. One spec = one Composer session. Run order:
  F5 (warm-up) → cli-ui-adaptive → f1-stereo (hard, may bounce back).
- Loop: Claude writes spec → Cursor implements + runs acceptance test → Claude reviews diff.

---

## 7. UI overhaul track (design-complete; build DEFERRED behind launch B1–B5)

Added 2026-06-22. Separate stream from the ship-gate. **Do not start the React port until
§6 is done** (bundled build proven + k locked + shipped to Hoàng). The redesign is
non-blocking for the $50 sale; boot blockers are the gate.

- **Status:** design + mockups approved. PR **#1** (`design/ui-overhaul` → `main`).
- **Artifacts:** `DESIGN.md` (design system of record) · `SteadyCut-ui-overhaul-plan.html`
  (architecture + phases) · `mockup.html` (v1, single-page) · `mockup-v2.html`
  (v2 dashboard shell — chosen direction).
- **Direction:** dashboard shell (sidebar + main pane), refined dark+red, Lucide icons
  (no emoji), Archivo display + JetBrains Mono numerics. Seeds a future **Projects**
  feature: persist `{name, footage_dir, settings_payload, status, output_xml, runtime, ts}`
  per run; Reopen = rehydrate the New Run form, Re-run = POST straight to `/api/process`.
- **Architecture:** React/Vite building into `static/`; all backend access behind one
  swappable `HostBridge` (pywebview now; Premiere CEP/UXP, Resolve WI later). FastAPI
  `/api` routes + `ProcessRequest` (`run.py:219`) unchanged — byte-parity required.
- **Cost split (per §0):** the port is mostly mechanical (build components 1:1 from
  `mockup-v2.html`) → **Cursor lane** via `SPEC-ui-*.md`. Claude owns the reasoning bits:
  `HostBridge` design, Projects persistence design, DESIGN.md-adherence diff review.
- **Pre-req before porting:** F5 version label (§6.4) lands first so the new header shows a
  real version, not the hardcoded `v2` placeholder in the mockups.

---

## 5. Eval loop reference (how to score a run)

1. Run pipeline → import XML to Premiere.
2. Triage tracks: usable→**V1**, junk→**V2**, right-shot-wrong-in/out→**V3**.
3. Export FCP7 XML.
4. `python3 scripts/eval_xml.py <xml>` → hard fail rate (V2), usable-after-trim (V1+V3), by-clip-type.

Artifacts this session live in `/Users/mac/Downloads/STEADY/v1.2.1/`:
`FCP7.xml` (fixed baseline), `ADAPTIVE FCP7.xml` (adaptive triaged), `adaptive_s3*.{xml,json}`.
