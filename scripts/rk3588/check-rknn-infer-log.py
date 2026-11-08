#!/usr/bin/env python3
"""Check a rknn-infer device log against the pinned MobileNet V1 expectations (VM only)."""
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
LOCK = json.loads(Path(__file__).with_name("rknn-mobilenet.lock.json").read_text())
LABELS = (ROOT / ".cache/rk3588/rknn-infer/official/labels.txt").read_text().splitlines()

def label(index):
    return LABELS[index].split(":", 1)[-1].strip()

def main():
    text = Path(sys.argv[1]).read_text()
    failures = []
    if "INFERENCE_FLOW_PASS" not in text:
        failures.append("INFERENCE_FLOW_PASS marker missing")
    for name, expected in LOCK["expected"].items():
        top = {int(r): (int(c), float(s)) for r, c, s in
               re.findall(rf"top5 input={re.escape(name)} rank=(\d) class=(\d+) score=([\d.]+)", text)}
        if 1 not in top:
            failures.append(f"{name}: no top-1 result")
            continue
        for rank in sorted(top):
            print(f"{name} #{rank}: [{top[rank][0]:4d}] {top[rank][1]:.6f} {label(top[rank][0])}")
        allowed = expected.get("top1_any_of", [expected.get("top1")])
        if top[1][0] not in allowed:
            failures.append(f"{name}: top-1 {top[1][0]} not in {allowed}")
        if "reference_score" in expected:
            print(f"{name}: reference top-1 score {expected['reference_score']}, "
                  f"delta {top[1][1] - expected['reference_score']:+.6f}")
        consistency = re.search(rf"consistency input={re.escape(name)} max_abs_diff=(\S+) top1_changes=(\d+)", text)
        if consistency is None or int(consistency.group(2)) != 0:
            failures.append(f"{name}: warm-run top-1 consistency missing or changed")
    for failure in failures:
        print(f"FAIL {failure}")
    print("MOBILENET_TOP1_CHECK=" + ("FAIL" if failures else "PASS"))
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
