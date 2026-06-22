# SteadyCut — Tier 0 Ship Punch List (M1: self-serve usable)

Date: 18 Jun 2026. Goal: **download → run → works, zero hand-holding.** This is the minimum
to put the package in a real user's hands without you on a call.

**Out of scope for M1 (deliberately):** licensing (next gate, M2 — see note at bottom),
accuracy polish A2–A4, the AI/adaptive-threshold roadmap. Ship M1, then layer those.

Source: `PUNCHLIST.md` (Hoàng tester feedback). Line refs are from that doc.

---

## 1. Boot — verify it actually runs on a clean machine  *(T0.1)*

B1–B5 look fixed in commit `93ee269` (bundled ffmpeg, hardened resolver, crash handler).
This item is **verification**, not new code — but it gates everything: if it doesn't boot,
nothing else is seen.

- [ ] **macOS clean run** — machine with **no** Python and **no** ffmpeg preinstalled.
  App launches, transcodes, completes a full pipeline. No vanish-on-boot.
- [ ] **Windows clean run** — same, on Win 10/11.
- [ ] **Bundled ffmpeg is used**, not the runtime download (confirm `sys._MEIPASS/bin` path
  wins). Download stays fallback only.
- [ ] **Crash handler proven** — force an early failure, confirm a traceback file is written
  (faulthandler + `sys.excepthook`).
- *Done when:* a fresh machine completes end-to-end with zero manual setup.
- *Blocker:* need a clean test machine (VM or borrowed). 5–15 min once you have one.

---

## 2. A1 — multi-shot splitting  *(biggest accuracy lever)*

One source file often holds several camera shots; the app extracts only 1–2 and ignores the
rest. This is the single biggest reason tester accuracy sat at ~50%.

- [ ] Add **per-file shot-boundary detection** (scene/cut detection across the clip).
- [ ] Emit **every** detected shot as its own clip, each with its own selected window.
- [ ] Wire into the existing window-selection stage so each shot runs analysis independently.
- [ ] Validate on a known multi-shot file from Hoàng's batch.
- *Done when:* a file with N shots yields N clips, not 1.

---

## 3. F1 — single stereo track  *(cheap, high-value)*

Currently exports **two** audio tracks; should be one L/R stereo clip on the timeline.
Prior fix attempt failed. Lives in `xml_assembler.py`.

- [ ] Find the exact Premiere/FCP7 XML shape for a 2-channel linked clip (one `clipitem`,
  stereo channels), not two separate audio `clipitem`s.
- [ ] Rebuild the audio assembly to emit that shape.
- [ ] Verify in Premiere: one linked stereo track, audio synced to video.
- *Done when:* Premiere import shows a single stereo track.

---

## 4. F2 — keep failed clips, colour-coded

Don't silently drop fails. Leave them on the timeline with a distinct label colour so the
editor can redo them by hand instead of wondering what's missing.

- [ ] On a clip that fails analysis/transcode, still place it on the timeline.
- [ ] Tag with a distinct label colour (reuse the Lavender "review me" convention).
- [ ] Confirm the count: input N → timeline shows N (some flagged), never silently < N.
- *Done when:* a forced-fail clip appears flagged, not vanished.

---

## 4b. Recovery phase reports no progress — looks frozen at 100%  *(real bug)*

Evidence: 100-clip Mac run (`steadycut_20260618_211207.txt`). Bar fills to 100% during
Phase 2 analysis, then the threshold-relaxation recovery sweep runs for minutes emitting
**zero** UI progress → user thinks it hung and quits before the XML is written. It wasn't
hung (no crash, no traceback) — just silent.

- [x] Emit progress during the relaxation/recovery loop (`steadycut_pipeline.py` — `_st`
  calls show "Recovering clips · X.Xpx" + a live `clip_current/clip_total` that climbs as
  clips recover). **Done 2026-06-18.**
- [ ] Bonus: surface "M clips added uncut (too shaky / full-motion)" in the final summary.
- *Done when:* a run with many recovery clips shows continuous progress, never a static 100%.
  ✅ core fix in; verify on next Mac build.
- *Note:* root cause is the fixed-threshold sweep — properly fixed by T1 adaptive threshold
  (one pass, no sweep). This item is the interim UX fix.

## 5. Completion notification — popup + ping  *(cheap polish)*

When the pipeline finishes, the user shouldn't have to babysit the progress bar.

- [x] On pipeline completion, show an in-app popup/modal (`#doneModal` in `static/index.html`,
  shows the saved XML path). **Done 2026-06-18.**
- [x] Play a short **ping** (synthesized two-note chime via Web Audio — no audio file to
  bundle; `unlockAudio()` on Run click satisfies WKWebView autoplay). **Done 2026-06-18.**
- [ ] Optional: a native OS notification too (macOS `osascript`, fires even if window is
  backgrounded). *(deferred — in-app popup + ping cover the ask)*
- *Done when:* finishing a run produces a visible popup **and** an audible ping.
  ✅ implemented; verify on next Mac build.

---

## Definition of done — M1 ships when

1. Boot verified on clean macOS **and** Windows.
2. Multi-shot files split correctly (A1).
3. Premiere import is clean: single stereo track (F1), no silent drops (F2).
4. A real user can download, run, and get a usable timeline **without contacting you.**

## Build order (fastest path out)

1. **#1 Boot verify** — gating, near-zero effort, just needs a clean machine.
2. **#3 F1 stereo + #4 F2 keep-fails** — cheap workflow wins, mostly `xml_assembler.py`.
3. **#2 A1 multi-shot** — the heavy lift, biggest accuracy payoff. Do last but don't skip;
   it's what makes the output trustworthy.

---

## Fast-follow (right after M1 core — cheap, high value)

### U1 — "Check for updates" button
Goal: a user who downloads once can discover new versions without you pinging them.
**Do the notify-only version, not full auto-update** (in-place swap = code-signing +
platform-specific complexity; later tier).

- **Wrinkle:** the repo is **private**, so the app can't hit the GitHub releases API (testers
  have no token). Instead, host a tiny **public version manifest** — a JSON file
  (`{ "version": "1.0.3-beta", "url": "<download link>", "notes": "…" }`) on something public
  (gist / Cloudflare / a public "releases" repo). Pairs perfectly with the rolling-beta
  release URL from the distribution plan.
- [ ] Embed the app's own version at build time (stamp from the git tag).
- [ ] Button fetches the manifest, compares versions.
- [ ] If newer: show "vX available → [Download]" opening the URL. Else "You're up to date."
- *Done when:* a stale build reports an update and links to the new one; a current build says
  up-to-date.

---

## Next gate (M2 — sellable, NOT in M1)

License system (`SteadyCut-docs/SteadyCut-ship-plan.md`): Lemon Squeezy machine-locked keys,
`license.py`, 7-day offline grace, `STEADYCUT_DEV_LICENSE=1` bypass. **Build dormant in
parallel; flip on when M1 is smooth and you're ready to charge.** Run M1 as an explicit
time-boxed *beta* so the free downloads don't anchor the price at $0.

---

## Session log

### 2026-06-18 — session 1
**Done (2 items, in source, NOT yet committed/pushed):**
- ✅ **4b** recovery progress feedback — `steadycut_pipeline.py` recovery loop now emits live
  "Recovering clips · X.Xpx" + climbing counter. Fixes the "frozen at 100" report.
- ✅ **5** completion popup + ping — `static/index.html`: `#doneModal` + synthesized Web Audio
  chime, audio unlocked on Run click.

**Immediate next step (tomorrow):**
1. Commit + push the two changes (`steadycut_pipeline.py`, `static/index.html`).
2. Rebuild on the Mac (`bash build.sh`) and re-run the 100-clip batch — **set Stable seconds
   = 1.5, Max threshold = 8** so it finishes fast and writes XML. Confirms T0.1 + verifies 4b/5.
3. Import XML to Premiere → T0.1 done.

**Remaining T0:** T0.1 (verify), F1 stereo (`xml_assembler.py`), F2 keep-fails, A1 multi-shot
(the feature — needs a shot-boundary-detection design decision). Then T1 adaptive threshold
(reprioritized — it's the real fix for the recovery-sweep slowness, per session-1 log analysis).
- New logo now in `assets/` — use it when polishing the popup / branding.
