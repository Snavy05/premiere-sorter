# SteadyCut — Launch Notes

Working tracker for the commercial launch (Gumroad, $19 one-time + support, wedding/event videographers, mac + win). Not user-facing. Update as things land.

---

## Launch scope (locked)
SteadyCut **launches as a stable-window finder** only.
- **Stability / stable-window detection** = the supported, shipped feature.
- **Experimental tab (off by default):** Actions (AI) mode, Both mode, Cut-on-Action, Shot Classification. Flagged, unfinished — opt-in only.
- **Export:** **Premiere Pro = supported.** DaVinci Resolve (XML + live) = **Experimental** badge, not the launch target.

## UI (done)
- `static/index.html` rebuilt: sidebar-rail dashboard, **Hanken Grotesk** + Fira Code numerics, **light + dark** theme (toggle, persisted in localStorage), hairline borders, SVG icons (no emoji), restrained red.
- One **merged workspace**: Recipe (presets + settings) left column, **Run** right column (sticky).
- **Experimental** tab (amber banner). **Support** tab → `snavy.works@gmail.com`.
- Adaptive threshold toggle present on the Stable Window card. **Recommended** preset uses adaptive @ sensitivity 3.
- Presets retuned stability-first: Event Recap / Yearbook / Recommended / Custom (Wedding/action preset removed from the main flow).
- Mockup that was approved: `steadycut-ui-mockup.html` (repo root).

---

## TODO before publishing

### 1. Windows validation after rebuild
- Rebuild with the new UI + proxy fixes.
- On Windows, select the folder that contains original clips; if camera proxies are used, they must live in a `proxies/` subfolder inside that originals folder.
- Confirm the output XML has zero `<pathurl>` entries containing `/proxies/` or `_Proxy`.
- Confirm Premiere imports the XML and links to original clips.

### 2. Redo Gumroad cover + screenshots
Current `cover.png` / `shot-*.png` show the OLD UI. Re-shoot against the new theme (dark + maybe a light variant) once the app build is updated.

### 3. Cut a fresh build + CI run
After Windows validation passes, tag a release so CI rebuilds mac + win zips for upload.

---

## Decisions log
- 2026-06-30: Un-shelved → commercial. Price $19 + maintenance support. Premiere-first; DaVinci experimental. Launch = stable windows only; classification + COA + action mode → Experimental. New tabbed dashboard UI (Hanken Grotesk, light/dark). XML keeper-colour + `(n/N)` naming scheme locked. Support email `snavy.works@gmail.com`.
