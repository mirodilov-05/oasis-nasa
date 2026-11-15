"""Regression lock for the calculation engine.

The numbers in regression_baseline.json were recorded from the engine before the
2026-11 redesign. Any change to a formula, a crop parameter or a rotation shows up
here as a failing test, so formulas never change silently.

Re-record only after an intended, documented change:
    UPDATE_BASELINE=1 python -m pytest tests/test_regression.py -q
"""
import json
import math
import os
from pathlib import Path

from backend import agro, climate
from tests.test_backend import fake_data

BASELINE = Path(__file__).with_name("regression_baseline.json")
REL_TOL = 1e-6


def _snapshot():
    out = {}
    for warming in (0.0, 0.03):
        d = climate.prepare(fake_data(warming=warming))
        clim = climate.analyze(d)
        key = f"warming_{warming}"
        out[key] = {
            "shift": {k: {f: v.get(f) for f in ("baseline", "recent", "delta")}
                      for k, v in clim["shift"].items() if isinstance(v, dict) and "delta" in v},
            "crops": {c["id"]: c["recent"]["water_need_mm"] for c in agro.Field(d).crops()},
        }
        for irr in ("furrow", "drip"):
            rot = agro.Field(d, "medium", irr).rotations(
                "cotton_mono", None, {"water": 40, "income": 30, "soil": 20, "risk": 10})
            out[key][f"rotations_{irr}"] = {
                "best_id": rot["best_id"],
                "order": [it["id"] for it in rot["items"]],
                "water_p50": {it["id"]: it["water_m3_ha"]["p50"] for it in rot["items"]},
            }
    return json.loads(json.dumps(out))  # normalise tuples / numpy scalars


def _compare(a, b, path="$"):
    if isinstance(a, dict):
        assert set(a) == set(b), f"{path}: keys differ {set(a) ^ set(b)}"
        for k in a:
            _compare(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        assert len(a) == len(b), f"{path}: length {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            _compare(x, y, f"{path}[{i}]")
    elif isinstance(a, float) or isinstance(b, float):
        assert a is not None and b is not None and math.isclose(a, b, rel_tol=REL_TOL, abs_tol=1e-9), \
            f"{path}: {a} != {b}"
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


def test_engine_outputs_match_recorded_baseline():
    current = _snapshot()
    if os.environ.get("UPDATE_BASELINE") == "1" or not BASELINE.exists():
        BASELINE.write_text(json.dumps(current, indent=1, sort_keys=True), encoding="utf-8")
    _compare(json.loads(BASELINE.read_text(encoding="utf-8")), current)
