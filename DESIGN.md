# SteadyCut — DESIGN.md

Design system of record for the SteadyCut desktop app UI. The React/Vite overhaul
(see `SteadyCut-ui-overhaul-plan.html`) and its Phase-0 `mockup.html` both build to this
file. Anchored to the existing dark + red identity; identity preservation wins over
re-theming.

- **Register:** product (design serves the tool), not brand/marketing.
- **Surface:** desktop webview (pywebview now; Premiere/Resolve plugin later), ~860×740,
  min 680×580. Single-page dashboard.
- **Color strategy:** Restrained. Neutral dark surfaces, one red accent ≤10% of surface,
  semantic green/amber/red for state only.
- **Scene sentence:** a video editor at a workstation, indoor low light, prepping a cull
  before an edit; wants speed and trust, not delight. Dark theme is forced by that scene,
  not chosen for cool.

---

## 1. Color tokens

Dark-only. Carried from the current `:root` (`static/index.html:27`), with the muted
hint ink bumped for contrast. OKLCH not required; preserve the committed brand hexes.

| Token | Hex | Role |
|-------|-----|------|
| `--bg` | `#0d0d10` | app background |
| `--surface` | `#141418` | header/footer, inputs |
| `--card` | `#18181b` | cards, panels |
| `--border` | `#27272a` | hairlines |
| `--border-hi` | `#3f3f46` | stronger dividers, control borders |
| `--text` | `#e4e4e7` | primary ink |
| `--text-2` | `#a1a1aa` | secondary ink |
| `--text-3` | `#b3b3bb` | hint/label ink (raised from `#9a9aa3`; old value borderline at 12px) |
| `--accent` | `#ff3b3b` | on-dark accent ink, icons, borders |
| `--accent-hi` | `#ff6363` | accent hover |
| `--accent-dim` | `#ff3b3b1a` | accent tint background |
| `--accent-solid` | `#e00000` | filled buttons with white text |
| `--accent-solid-hi` | `#c40000` | filled button hover |
| `--ok` | `#22c55e` | success state |
| `--warn` | `#f59e0b` | warning state |
| `--err` | `#ef4444` | error state |

**Contrast rules (verify in build, do not assume):**
- Body/labels ≥ 4.5:1 against their actual surface; large/bold ≥ 3:1.
- Filled buttons use `--accent-solid` (white text passes AA ~5:1); never `--accent`
  (`#ff3b3b`) behind white text.
- State color always pairs with an icon or text, never color alone.
- Borders/dividers must stay visible (they do on dark; keep parity if a light mode is
  ever added).

---

## 2. Typography

Inter is banned here (detector tell: saturated AI face). Split roles:

- **Heading / display:** an identity face NOT on the tell list (Inter, Roboto, Geist,
  Space Grotesk, Plus Jakarta, Fraunces are out). Candidates: **Archivo**, Hanken
  Grotesk, Sora. Final pick locked in the mockup.
- **Numeric / timecode / clip counts / timing bars:** a **tabular mono** (JetBrains Mono
  or IBM Plex Mono) so columns and timers do not shift. `font-variant-numeric: tabular-nums`.
- **Dense body / control labels:** neutral system stack
  (`-apple-system, "Segoe UI", system-ui`) to avoid a second webfont tell and keep the
  bundle light.

Scale: 12 / 13 / 14 / 16 / 20 / 24. Body line-height 1.5. Weight carries hierarchy:
headings 600–700, labels 500, body 400. Display letter-spacing floor −0.04em.

---

## 3. Iconography

- **Set:** Lucide outline only. Drop every emoji.
- **Replace:** preset icons (💍🎉🎓✦⚙️), phase-step icons (⚙📊🤸🎯🎬), done-modal ✅.
- **Stroke:** 1.75 uniform. **Sizes (tokens):** `icon-sm 14`, `icon-md 18`, `icon-lg 24`.
- The header/folder/file/save SVGs already in `index.html` are this language; extend it.

---

## 4. Motion

- Durations: micro 150–300ms, complex ≤400ms, never >500ms.
- Easing: ease-out-quart on enter; exit ~70% of enter duration.
- Properties: `transform` / `opacity` only. **Replace `.phase-bar-fill` width animation
  with `transform: scaleX()` + `transform-origin: left`.**
- List/grid reveals stagger 30–50ms; reveals enhance already-visible content, never gate
  visibility.
- **Reduced motion is mandatory.** Every animation (spinner, pulse-dot, fade-in, bar
  fill) needs a `@media (prefers-reduced-motion: reduce)` alternative: crossfade or
  instant.

---

## 5. Layout & z-index

- Single scrollable page; sticky bottom **run bar**. Reserve bottom content inset equal to
  run-bar height so nothing hides behind it.
- Spacing on a 4/8px rhythm; vary section gaps (16/24/32/48) for hierarchy, not uniform.
- Flexbox for 1D, Grid for 2D. Responsive control grids: `repeat(auto-fit, minmax(…,1fr))`.
- **Z-index scale (semantic, no 999/1000):** dropdown 10 · sticky run-bar 20 · scrim 30 ·
  modal 40 · toast 50 · tooltip 60.
- Native dropdowns/popovers via `<dialog>` / popover API / `position:fixed`, not absolute
  inside an `overflow` container.

---

## 6. Components

- **Cards:** allowed but not the default; never nested. The 5-up preset grid (identical
  icon+heading+text cards) is an anti-pattern: rebuild as a segmented control or
  differentiated rows.
- **Buttons/toggles:** custom `<button>`s need visible `:focus-visible` rings (2–4px) and
  ≥44px target. Disabled = reduced opacity + `disabled` attr + no pointer.
- **Forms:** visible label per field (not placeholder-only); helper text persistent;
  errors inline below the field with cause + fix, and `aria-live`/`role="alert"`.
- **Stacked label + description** (preset rows, list items): the title and sub-text must
  be block-level (or the wrapper `display:flex;flex-direction:column`). Never two inline
  `<span>`s relying on whitespace, or they render glued ("WeddingCuts on movement").
- **Run/result panel:** on success, always surface a **Show in Finder / Reveal** action
  next to the saved XML path. Do not show the path without the reveal affordance.
- **Run bar / progress:** phase steps, % bar (scaleX), clip X/Y, result state
  (success path + Reveal / error + View Log / stopped). One primary action visible at a
  time (Run, or Stop while running).

---

## 7. Absolute bans (rewrite if reached)

Side-stripe accent borders · gradient text (`background-clip:text`) · glassmorphism as
default · hero-metric template · identical card grids · uppercase tracked eyebrow on every
section · numbered section markers as scaffolding · emoji as icons · text that overflows
its container at any breakpoint · animating layout properties · any animation without a
reduced-motion path.

---

## 8. Pre-ship checklist

- [ ] No emoji icons; one Lucide set, uniform stroke.
- [ ] Every text/bg pair verified ≥4.5:1 (≥3:1 large).
- [ ] Filled buttons use `--accent-solid`, white text.
- [ ] All animations transform/opacity + reduced-motion alt; no width/height anim.
- [ ] `:focus-visible` on every interactive element; targets ≥44px.
- [ ] Z-index from the semantic scale only.
- [ ] State conveyed by icon/text, not color alone.
- [ ] Run bar reserves content inset; nothing clipped behind it.
- [ ] Headings use the identity face; numerics use tabular mono.
