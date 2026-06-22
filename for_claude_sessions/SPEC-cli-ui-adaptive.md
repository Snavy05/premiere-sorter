# SPEC — expose adaptive thresholding on CLI + UI

**Status:** backend already wired. `run.py:354-355` passes `adaptive`/`sensitivity` to
`run_pipeline`; they're request-body fields (`run.py:229-230`, default `adaptive=False`,
`sensitivity=3.0`). Two gaps remain. Default `sensitivity` value is being chosen by Claude
from a sweep — leave it at `3.0` for now; Claude will change the one number later.

## Task A — CLI flags in `steadycut_pipeline.py:main()`
Add two argparse args next to the existing ones (block starts ~line 1248):

```python
parser.add_argument("--adaptive", action="store_true", dest="adaptive",
                    help="Per-clip threshold = median + k*MAD of the clip's own motion "
                         "(overrides --threshold). Off by default.")
parser.add_argument("--sensitivity", type=float, default=3.0, dest="sensitivity",
                    metavar="K", help="The k in median + k*MAD (higher = keep more). Default 3.0.")
```

Then pass them through to the `run_pipeline(...)` call inside `main()`:
`adaptive=args.adaptive, sensitivity=args.sensitivity`.

**Acceptance:** `python steadycut_pipeline.py --help` shows both flags.
`python steadycut_pipeline.py --input <dir> --output /tmp/a.xml --adaptive --sensitivity 2.5 --skip-classification`
runs and the log prints `threshold=... px  (adaptive)` per clip (not a fixed value).

## Task B — UI toggle
The front-end (HTML/JS served by `run.py`; grep the UI markup for an existing settings
control like `threshold` or `stable_secs`) needs:
- a checkbox "Adaptive threshold" bound to request body `adaptive`,
- a number input "Sensitivity (k)" bound to `sensitivity`, default 3.0, shown only when
  adaptive is checked.

Mirror however the existing numeric settings post to the FastAPI endpoint — do not invent a
new request shape; the body fields already exist.

**Acceptance:** launch app, tick Adaptive, set k=2.5, run a small batch; backend log shows
`(adaptive)` thresholds. Unticked = unchanged fixed-threshold behavior.

## Out of scope
Picking the default k (Claude, from sweep triage). Don't change pipeline logic.
