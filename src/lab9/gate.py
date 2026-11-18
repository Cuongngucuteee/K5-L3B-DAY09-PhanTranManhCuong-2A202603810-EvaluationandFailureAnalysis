"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, load_spec, per_item

SAFETY_COUNTS = ("secret_leak", "approval_bypass")   # aggregated as a count, like lab9.eval.aggregate
TOL = 1e-4


def _is_bad_number(v: Any) -> bool:
    return isinstance(v, float) and (math.isnan(v) or math.isinf(v))


def _graded_ids(spec: dict, rep: dict) -> list[str]:
    """Spec metrics that are graded per row (they appear as row keys in the report or have a grader)."""
    from lab9.graders import GRADERS
    row_keys = set(rep["rows"][0]) if rep.get("rows") else set()
    return [m["id"] for m in spec.get("metrics", []) if m["id"] in GRADERS or m["id"] in row_keys]


def _recompute(rows: list[dict], metric_ids: list[str], k_values: list[int]) -> dict[str, Any]:
    """Re-derive the headline numbers from the rows, so a report whose metrics were edited
    (or that disagrees with its own rows) cannot pass."""
    out: dict[str, Any] = {}
    for m in metric_ids:
        vals = [r.get(m) for r in rows if r.get(m) is not None]
        if m in SAFETY_COUNTS:
            out[m] = sum(bool(v) for v in vals)
        else:
            out[m] = round(sum(bool(v) for v in vals) / len(vals), 4) if vals else None
    by_item: dict[str, list[bool]] = {}
    for r in rows:
        by_item.setdefault(r["item_id"], []).append(bool(r.get("pass_rate")))
    if by_item:
        out["pass_5"] = round(sum(all(v) for v in by_item.values()) / len(by_item), 4)
        for k in k_values:
            if min(len(v) for v in by_item.values()) >= k:
                for name, fn in (("pass_at", stats.pass_at_k), ("pass_hat", stats.pass_hat_k)):
                    est = [fn(len(v), sum(v), k) for v in by_item.values()]
                    out[f"{name}_{k}"] = round(sum(est) / len(est), 4)
    return out


def _ratchet(spec: dict, base_spec: dict) -> list[str]:
    """Same eval set => thresholds may only get stricter. Returns one line per loosening."""
    problems: list[str] = []
    if spec.get("eval_set_version") != base_spec.get("eval_set_version"):
        problems.append(f"ratchet: eval_set_version changed {base_spec.get('eval_set_version')!r} -> "
                        f"{spec.get('eval_set_version')!r} without a new eval set")
    if spec.get("eval_set") != base_spec.get("eval_set") or spec.get("labels") != base_spec.get("labels"):
        problems.append("ratchet: eval_set / labels path changed in the spec")
    if sorted(spec.get("seeds") or []) != sorted(base_spec.get("seeds") or []):
        problems.append(f"ratchet: seeds changed {base_spec.get('seeds')} -> {spec.get('seeds')}")
    if not set(base_spec.get("pass_k") or []) <= set(spec.get("pass_k") or []):
        problems.append("ratchet: pass_k values removed")
    bs, bb = spec.get("bootstrap") or {}, base_spec.get("bootstrap") or {}
    if bs.get("confidence", 0.95) < bb.get("confidence", 0.95):
        problems.append("ratchet: bootstrap confidence lowered")
    if bs.get("n_resamples", 1000) < bb.get("n_resamples", 1000):
        problems.append("ratchet: bootstrap n_resamples lowered")
    if bs.get("rng", 2027) != bb.get("rng", 2027) or bs.get("method", "percentile") != bb.get("method", "percentile"):
        problems.append("ratchet: bootstrap rng/method changed (re-rolling the CI)")

    def cmp(tag: str, new: dict | None, old: dict, new_th: dict, old_th: dict) -> None:
        if new is None:
            problems.append(f"ratchet: {tag} removed")
            return
        if old.get("blocking") and not new.get("blocking"):
            problems.append(f"ratchet: {tag} no longer blocking")
        for k in ("max", "max_increase"):
            if k in old_th and (k not in new_th or new_th[k] > old_th[k]):
                problems.append(f"ratchet: {tag} {k} loosened {old_th[k]} -> {new_th.get(k)}")
        if "min" in old_th and ("min" not in new_th or new_th["min"] < old_th["min"]):
            problems.append(f"ratchet: {tag} min loosened {old_th['min']} -> {new_th.get('min')}")
        if old_th.get("no_significant_drop") and not new_th.get("no_significant_drop"):
            problems.append(f"ratchet: {tag} dropped no_significant_drop")

    new_m = {m["id"]: m for m in spec.get("metrics") or []}
    for m in base_spec.get("metrics") or []:
        n = new_m.get(m["id"])
        cmp(f"metric {m['id']}", n, m, (n or {}).get("threshold") or {}, m.get("threshold") or {})
    new_s = {(x["name"], x["metric"]): x for x in spec.get("slices") or []}
    for x in base_spec.get("slices") or []:
        cmp(f"slice {x['name']}/{x['metric']}", new_s.get((x["name"], x["metric"])), x,
            new_s.get((x["name"], x["metric"])) or {}, x)
    return problems


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []
    rows, brows = pr.get("rows") or [], base.get("rows") or []
    man, bman = pr.get("manifest") or {}, base.get("manifest") or {}

    # 1. headline numbers must be finite
    for name, rep in (("PR", pr), ("baseline", base)):
        for k, v in (rep.get("metrics") or {}).items():
            if _is_bad_number(v):
                problems.append(f"{name} metric {k} is {v} (a crashed grader is not a score)")

    # 2. grader crashes must not be silent
    if pr.get("evaluator_failures"):
        problems.append(f"{len(pr['evaluator_failures'])} evaluator failure(s) in the PR report")
    if not rows:
        problems.append("PR report has no rows")
        return problems

    # 3. row count and (item, seed) coverage
    try:
        want_rows = int(man["items"]) * len(man["seeds"])
    except (KeyError, TypeError, ValueError):
        want_rows = None
        problems.append("manifest lacks items/seeds")
    if want_rows is not None and len(rows) != want_rows:
        problems.append(f"{len(rows)} rows, manifest says {man['items']} items x {len(man['seeds'])} seeds = {want_rows}")
    if man.get("items") != bman.get("items") or sorted(man.get("seeds") or []) != sorted(bman.get("seeds") or []):
        problems.append(f"manifest differs from baseline: items {man.get('items')} vs {bman.get('items')}, "
                        f"seeds {man.get('seeds')} vs {bman.get('seeds')}")
    keys = [(r.get("item_id"), r.get("seed")) for r in rows]
    if len(set(keys)) != len(keys):
        problems.append(f"{len(keys) - len(set(keys))} duplicated (item_id, seed) row(s)")
    bkeys = {(r.get("item_id"), r.get("seed")) for r in brows}
    if set(keys) != bkeys:
        miss, extra = len(bkeys - set(keys)), len(set(keys) - bkeys)
        problems.append(f"(item_id, seed) set differs from baseline: {miss} missing, {extra} extra")
    if any(r.get("seed") not in (man.get("seeds") or []) for r in rows):
        problems.append("rows carry seeds that are not in the manifest")

    # 4. every graded metric is a real boolean on every row
    graded = _graded_ids(spec, base if brows else pr)
    for m in graded:
        bad = [r for r in rows if not isinstance(r.get(m), bool)]
        if bad:
            problems.append(f"{len(bad)} row(s) with {m}={bad[0].get(m)!r} (grader missing/crashed)")
    if any(_is_bad_number(r.get("cost")) for r in rows):
        problems.append("row cost is NaN/inf")

    # 5. headline metrics must be what the rows say
    if not any("row(s) with" in p for p in problems):
        again = _recompute(rows, [m for m in graded if all(m in r for r in rows)], spec.get("pass_k") or [])
        for k, v in again.items():
            got = (pr.get("metrics") or {}).get(k)
            if k in (pr.get("metrics") or {}) and v is not None and (
                    not isinstance(got, (int, float)) or abs(float(got) - float(v)) > TOL):
                problems.append(f"metric {k}={got} but its rows give {v}")
        for m in spec.get("metrics") or []:
            if m["id"] in again and m["id"] not in (pr.get("metrics") or {}):
                problems.append(f"metric {m['id']} missing from the PR report")

    # 6. slice tags are fixed by the eval set, not by the PR
    btags = {r.get("item_id"): sorted(r.get("slices") or []) for r in brows}
    relabelled = sorted({r.get("item_id") for r in rows
                         if r.get("item_id") in btags and sorted(r.get("slices") or []) != btags[r.get("item_id")]})
    if relabelled:
        problems.append(f"slice tags differ from baseline on {', '.join(relabelled[:5])}")

    # 7. ratchet against the spec on main
    if base_spec is not None:
        problems += _ratchet(spec, base_spec)
    return problems


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked: list[str] = []
    lines: list[str] = []
    bs = spec.get("bootstrap", {})
    for sl in spec.get("slices") or []:
        name, metric = sl["name"], sl["metric"]
        sid = f"slice:{name}/{metric}"
        a, b = per_item(pr, metric, slice_name=name), per_item(base, metric, slice_name=name)
        verdict, note = "ok", ""
        val = round(sum(a.values()) / len(a), 4) if a else None
        if val is None:
            verdict = "missing"                 # an empty slice proves nothing: treat as a failure
        else:
            if "min" in sl and val < sl["min"]:
                verdict = "fail"
            if "max" in sl and val > sl["max"]:
                verdict = "fail"
            if sl.get("no_significant_drop"):
                keys = sorted(set(a) & set(b))
                if keys:
                    d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                       n_resamples=bs.get("n_resamples", 1000),
                                                       rng=bs.get("rng", 2027),
                                                       confidence=bs.get("confidence", 0.95))
                    note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                    if hi < 0:
                        verdict = "fail"
        if verdict != "ok" and sl.get("blocking"):
            blocked.append(sid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if sl.get("blocking") else "warn")
        th = {k: sl[k] for k in ("min", "max", "no_significant_drop") if k in sl}
        lines.append(f"  {sid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
