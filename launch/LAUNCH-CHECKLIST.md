# Gumroad Launch — Master Checklist

Everything needed to publish SteadyCut on Gumroad ($19 one-time + maintenance support).
Split by **who does it**. ☐ = not done. I draft/produce everything marked **[me]**; the rest is **[you]** — your accounts, your footage, your decisions.

---

## 🚧 BLOCKERS (must clear before publish)

- ☐ **[you] Test the new build** on real footage — run the updated app end-to-end, confirm the stable-window export imports into Premiere cleanly with the new `(i/N)` clip names + keeper/review colours.
- ☐ **[you] Windows validation before selling Windows** — after rebuilding, run one Windows job with the selected folder containing originals and (if used) a `proxies/` subfolder. Confirm the output XML has **zero** `<pathurl>` entries containing `/proxies/` or `_Proxy`, and imports into Premiere with clips linked to originals.
- ☐ **[me→you] Cut a fresh build** — once the above are clear, tag a release so CI rebuilds the mac + win zips with all the new changes (UI, XML labels). You click "tag"; I prep.

---

## 📦 FROM YOU — assets

- ☐ **Demo video (highest-impact asset).** Screen-record a real run on real wedding/event footage. ~20–30s. Script in `marketing-posts.md`: messy folder → pick folder + preset → Run → drag XML into Premiere → timeline fills with trimmed keeper clips. This sells the product more than anything else. Phone-camera-over-shoulder also fine.
- ☐ **Proxies folder note for camera-generated proxies.** If testing with already-generated camera proxies, select the folder that contains the original clips and place proxies in a subfolder named exactly `proxies` inside it. Do not select the `proxies` folder itself.
- ☐ **One real Premiere screenshot** of an imported sequence (the colour-coded keeper timeline) — for the gallery. I can't fake this; needs your Premiere + footage.
- ☐ *(optional)* A 5–10s "before" clip of you manually scrubbing clips, for contrast in the demo.

## 🏦 FROM YOU — Gumroad account

- ☐ Create / log in to gumroad.com
- ☐ Settings → Payments → connect bank or PayPal (**without this you can't be paid**)
- ☐ Settings → fill tax info if prompted

## ⚖️ FROM YOU — decisions

- ☐ **Price** — confirm **$19** one-time (or change)
- ☐ **Mac-only vs mac+win** at launch (depends on the Windows bug)
- ☐ **Refund window** — I propose **14-day, no questions**. OK?
- ☐ **Apple notarization?** Right now the app is unsigned → buyers see a Gatekeeper warning (we explain the right-click-Open step). Notarizing removes it but needs a paid Apple Developer account ($99/yr). Ship unsigned for v1? (recommended — ship now, notarize later)
- ☐ **Product name / tagline** — confirm "SteadyCut — find the steadiest, most usable window of every clip, automatically" (or tweak)
- ☐ **Terms** — want a short usage/license blurb on the page? I can draft one (digital product, single-user). Yes/no.

---

## ✍️ FROM ME — I produce/draft all of this

- ☐ **[me] Listing copy** — `gumroad-listing.md` (done, this folder) — title, description, requirements, receipt, refund. **Reframed: stable-window finder, Premiere-supported, DaVinci experimental.** You paste it in.
- ☐ **[me] Cover image** — 1280×720, official logo. Re-shoot against the new UI (dark; maybe a light variant).
- ☐ **[me] Gallery screenshots** — new dashboard (Run + Experimental), once the build is final.
- ☐ **[me] Marketing posts** — `marketing-posts.md` — Reddit / FB-group / demo-caption copy, ready to paste.
- ☐ **[me] Runbook** — `gumroad-runbook.md` — the ~20-min click-by-click publish steps.
- ☐ **[me] Receipt / post-purchase text** — in `gumroad-listing.md`.

---

## 🚀 PUBLISH (you, ~20 min — see gumroad-runbook.md)

- ☐ New product → Digital product → name + **$19** single payment
- ☐ Upload the two zips (start first — they're big: mac ~840 MB, win ~440 MB)
- ☐ Paste description + receipt, set refund, add cover + gallery
- ☐ Replace support email placeholder → `snavy.works@gmail.com` (already in copy)
- ☐ Publish, copy the URL
- ☐ Record + post the demo video to 2–3 best channels (don't blast all at once)

---

### Fastest path to live
1. You test the build → 2. send the Windows artifact (or decide mac-only) → 3. I fix + we cut a fresh build → 4. I re-shoot cover/gallery → 5. you record the demo → 6. you do the 20-min Gumroad setup → live.
