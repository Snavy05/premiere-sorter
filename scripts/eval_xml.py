#!/usr/bin/env python3
"""
eval_xml.py — field-test fail-rate scorer from a Premiere/FCP7 XML.

Workflow (zero frame-labeling):
  1. Run SteadyCut on a batch, import the XML into Premiere.
  2. Review the timeline. Leave usable windows on V1. Drag the bad ones up:
        V2 = unusable / junk
        V3 = usable shot but wrong boundaries (cut too early / late)
  3. Export the sequence back to a Final Cut Pro 7 XML.
  4. python3 scripts/eval_xml.py /path/to/exported.xml

It reads which video track each clip sits on and reports the fail rate, broken
down by SteadyCut's clip-name tags so you see WHERE it's weak:
    (plain)     primary stable window
    [w2]        extra window in a multi-window clip
    [shot3]     multi-shot coverage clip (segment_shots)
    [review]    a clip SteadyCut couldn't place (kept for manual review)

Track->verdict mapping is V1=pass, V2=junk, V3=boundaries by default; override
with --tracks if you arranged them differently. Unlike the frame-label grader
this measures PRECISION (is the output usable) — it cannot see misses (good
shots SteadyCut never put on the timeline).
"""
import argparse
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict


def classify(name):
    """Tag bucket from a clip name."""
    if "[review]" in name:
        return "review"
    if re.search(r"\[shot\d+\]", name):
        return "shot"
    if re.search(r"\[w\d+\]", name):
        return "window+"
    return "primary"


def source_clip(name):
    """Strip SteadyCut tags to get the underlying source clip name."""
    return re.sub(r"\s*\[(?:shot\d+|w\d+|review)\]\s*", "", name).strip()


def parse(xml_path, verdicts):
    tree = ET.parse(xml_path)
    root = tree.getroot()
    video = next(root.iter("video"), None)
    if video is None:
        print("No <video> element found — is this a video sequence XML?")
        sys.exit(1)
    tracks = video.findall("track")

    rows = []  # (verdict, tag, source, name)
    for idx, tr in enumerate(tracks, start=1):
        verdict = verdicts.get(idx, f"V{idx}?")
        for ci in tr.findall("clipitem"):
            name = (ci.findtext("name") or "").strip()
            if not name:
                continue
            rows.append((verdict, classify(name), source_clip(name), name))
    return rows, len(tracks)


def pct(n, d):
    return f"{n/d:.0%}" if d else "—"


def report(rows, n_tracks, verdicts):
    total = len(rows)
    if total == 0:
        print("No clips found on any track.")
        return
    by_verdict = defaultdict(int)
    by_tag = defaultdict(lambda: defaultdict(int))   # tag -> verdict -> n
    for verdict, tag, _src, _name in rows:
        by_verdict[verdict] += 1
        by_tag[tag][verdict] += 1

    order = [v for v in ("pass", "junk", "boundaries") if v in by_verdict]
    order += [v for v in by_verdict if v not in order]

    print(f"\nParsed {total} windows across {n_tracks} track(s).")
    print(f"  track map: " + ", ".join(f"V{k}={v}" for k, v in sorted(verdicts.items())))
    print("\n" + "=" * 56)
    print("FIELD-TEST SCORECARD")
    print("=" * 56)
    good = by_verdict.get("pass", 0)
    bad = by_verdict.get("junk", 0)
    bnd = by_verdict.get("boundaries", 0)
    print(f"  usable (V1)        {good}/{total}  ({pct(good, total)})")
    print(f"  junk   (V2)        {bad}/{total}  ({pct(bad, total)})   <- unusable")
    print(f"  boundaries (V3)    {bnd}/{total}  ({pct(bnd, total)})   <- right shot, wrong in/out")
    fixable = good + bnd
    print(f"  ----")
    print(f"  hard fail rate     {pct(bad, total)}   (truly unusable)")
    print(f"  usable-after-trim  {pct(fixable, total)}   (V1 + V3, a nudge from done)")

    print("\n  by clip type (junk / total):")
    for tag in sorted(by_tag):
        d = by_tag[tag]
        tt = sorted(d.items())
        tot = sum(d.values())
        j = d.get("junk", 0)
        detail = ", ".join(f"{v} {k}" for k, v in tt)
        print(f"    {tag:>9}: {j}/{tot} junk ({pct(j, tot)})   [{detail}]")

    # which source clips produced junk — quick hit-list to eyeball
    junk_src = sorted({src for v, _t, src, _n in rows if v == "junk"})
    if junk_src:
        print(f"\n  source clips with >=1 junk window: {len(junk_src)}")
        print("    " + ", ".join(junk_src))


def main():
    p = argparse.ArgumentParser(description="Score a field-test XML by track.")
    p.add_argument("xml", help="exported FCP7 XML from Premiere")
    p.add_argument("--tracks", default="1=pass,2=junk,3=boundaries",
                   help="track->verdict map (default '1=pass,2=junk,3=boundaries')")
    args = p.parse_args()
    verdicts = {}
    for pair in args.tracks.split(","):
        k, v = pair.split("=")
        verdicts[int(k)] = v.strip()
    rows, n_tracks = parse(args.xml, verdicts)
    report(rows, n_tracks, verdicts)


if __name__ == "__main__":
    main()
