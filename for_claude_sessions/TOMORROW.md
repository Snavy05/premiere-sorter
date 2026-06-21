# Tomorrow's worklist — which app does what

Date target: launch by Fri 2026-07-03. Rule: **Claude decides+judges, Cursor types, you verify.**

| # | Task | App | Why this app | Done when |
|---|---|---|---|---|
| 1 | **Eval triage** — sort s2.5 then s2.0 clips into V1/V2/V3 tracks in Premiere, export FCP7 XML, run `scripts/eval_xml.py` | **You (Premiere)** | Needs human eyes; no tool can judge "usable" | junk% known for s2.5 + s2.0 → pick winning k |
| 2 | **F5 version label** (`SPEC-f5-version-label.md`) | **Cursor** | Pure mechanical, warm-up | header shows `v1.2.1-beta`, acceptance test passes |
| 3 | **CLI + UI adaptive toggle** (`SPEC-cli-ui-adaptive.md`) | **Cursor** | Backend half-done, just flags + toggle | `--adaptive`/`--sensitivity` work, UI toggle posts |
| 4 | **Lock winning k as default** | **Claude** | Reasoning — reads your triage numbers, edits one value in the spec | k from step 1 set as default; Cursor applies |
| 5 | **F1 stereo one-track** (`SPEC-f1-stereo.md`) | **Cursor → Claude if stalls** | Mechanical attempt; reasoning trap if XML shape resists | Premiere shows ONE stereo track (real import) |
| 6 | **Review each Cursor branch** | **Claude** | Cheap, high-value gate | diff reviewed, merged to main |
| 7 | **Prove bundled build end-to-end** | **You + Claude** | THE ship gate — needs a clean machine + judgment | ffmpeg resolves from `_MEIPASS/bin`, batch transcodes |

## Suggested sequence
1. **Morning, you:** triage s2.5 + s2.0 in Premiere (step 1). Longest human task — do it first.
2. **Cursor, parallel/after:** F5 (step 2) as warm-up → CLI+UI toggle (step 3). One branch each.
3. **Bring branches to Claude** (step 6): review + merge.
4. **Claude:** lock k from your triage numbers (step 4) → Cursor applies the one-line default.
5. **Cursor:** attempt F1 (step 5). If it stalls on the XML shape → run `claude` in Cursor's
   terminal, hand to Claude.
6. **End of day, you+Claude:** the build-proof (step 7) — the real gate.

## Defer (NOT tomorrow, NOT launch): A2–A4, F3, F4, F6, glue reversal-veto.

## Cheat sheet
- Cursor: `Cmd+I` Composer, "Implement `for_claude_sessions/SPEC-<x>.md`, run acceptance, report pass/fail."
- Branch handoff commands: see `.cursorrules` → "Branch handoff".
- Cursor lane: it reads `.cursorrules` automatically — implements specs, never redesigns.
