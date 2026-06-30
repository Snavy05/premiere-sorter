# Gumroad Publish Runbook — ~20 min

You do these (your login, payout, footage). Assets are prepped in this folder.

## 0. One-time setup (5 min)
- [ ] Log in: gumroad.com
- [ ] Settings → Payments → connect bank / PayPal. **Without this you can't be paid.**
- [ ] Settings → tax info if prompted.

## 1. Create the product (5 min)
- [ ] New product → **Digital product** → name: `SteadyCut`
- [ ] Price: **$19**, single payment.
- [ ] Upload the zips: `SteadyCut-macOS-arm64.zip` (+ `SteadyCut-Windows-x64.zip` if launching Windows). **Start the uploads first — they're big (~840 MB / ~440 MB) and slow.**
- [ ] Cover image: `cover.png` (1280×720). Add the gallery screenshots + the Premiere import shot.
- [ ] Summary + description: paste from `gumroad-listing.md`.
- [ ] Refund: enable, 14-day.

## 2. Receipt / post-purchase (2 min)
- [ ] Content tab → paste the "Receipt / post-purchase content" block from `gumroad-listing.md`. (Support email `snavy.works@gmail.com` is already in it.)

## 3. Publish (1 min)
- [ ] Publish → copy the product URL.

## 4. Launch posts (10 min) — see `marketing-posts.md`
- [ ] Post the demo video to 2–3 best channels first.
- [ ] Pin the link in your socials/bio.

## Gotchas
- Unsigned app → Gatekeeper/SmartScreen warning. The listing + receipt already explain the right-click-Open step — don't cut that text or you'll get "won't open" emails.
- Don't promise Intel-Mac support — build is **Apple Silicon only**.
- Lead with the **stable-window** value prop. Don't market the experimental action/DaVinci features — they're labelled experimental in-app for a reason.
