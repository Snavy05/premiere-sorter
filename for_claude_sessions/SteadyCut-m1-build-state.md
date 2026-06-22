---
name: steadycut-m1-build-state
description: Where the SteadyCut M1 (testers-only) build stands and the exact next step to resume
metadata: 
  node_type: memory
  type: project
  originSessionId: e255da4c-9a75-4692-9927-f82e40e7e725
---

SteadyCut M1 = testers-only, **no licensing** (licensing is M2, build dormant later). Goal:
download → run → works with zero hand-holding. Punch list: repo `docs/TIER0_SHIP.md` (canonical;
HTML mirror in `TheCollective/SteadyCut-docs/`). `docs/` is gitignored — strategy docs never enter the (private) repo.

**As of 2026-06-18, v1.0.2-beta boots on a real Mac** (matplotlib-eager-import crash fixed in `25e6eda`).

**Done this session (in source, committed locally, NOT pushed):**
- 4b — recovery-loop progress feedback (`steadycut_pipeline.py`): the 100-clip run looked frozen
  because the threshold-relaxation recovery sweep emitted no progress. Now shows "Recovering clips · X.Xpx".
- 5 — completion popup + ping (`static/index.html`, `#doneModal` + Web Audio chime).

**Resume here:** push → rebuild on Mac (`bash build.sh`) → re-run the 100-clip batch with
**Stable seconds = 1.5, Max threshold = 8** (the strict 3 s preset + 100 px code cap is what made
recovery grind) → import XML to Premiere → T0.1 done.

**Remaining T0:** F1 stereo (`xml_assembler.py` — one L/R track not two), F2 keep-fails colour-coded,
A1 multi-shot splitting (a real feature — needs a shot-boundary-detection design call).
**Then T1 adaptive threshold** — reprioritized: it's the proper fix for the recovery-sweep slowness
(per-clip variance-based threshold, one pass, no sweep). See [[design-doc-html-export]] for doc-render rules; logo work [[steadycut-logo-refine]].
