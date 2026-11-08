#!/usr/bin/env python3
"""Correlate a CPU sampling session with MaaCore asst.log (VM only; numbers only).

Usage: correlate-ocr-cpu.py raw.txt asst.log label=HH:MM:SS-HH:MM:SS ...
Per window: OCR calls and wall time from the log, sampled CPU of the ncnn OpenMP workers and of the
thread that issued the OCR calls, an OCR CPU estimate (issuing thread busy for the OCR wall time plus
worker CPU), and the functions with the most "| leave, N ms" time on that thread (nested scopes
overlap, so these are indicative, not additive). asst.log thread ids are not Linux tids, so the
issuing thread is the non-OpenMP MaaCore thread whose per-sample CPU best correlates with OCR time.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import re
import sys

spec = importlib.util.spec_from_file_location("acs", Path(__file__).with_name("analyze-cpu-samples.py"))
acs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acs)

LINE = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3})\]\[\w+\]\[Px(\d+)\]\[Tx(\d+)\] (.*)$")
OCR = re.compile(r"asst::(\w+)Ocr \[(.*)\] by OCR (Rec|Pipeline) , cost (\d+) ms$")
LEAVE = re.compile(r"^(\S+) \| leave, (\d+) ms")

def main():
    samples, clk, tz = acs.parse(sys.argv[1])
    day = datetime.fromtimestamp(samples[0]["epoch"], tz).date()
    windows = []
    for spec_text in sys.argv[3:]:
        label, _, span = spec_text.partition("=")
        a, _, b = span.partition("-")
        to_ts = lambda hms: datetime.combine(day, datetime.strptime(hms, "%H:%M:%S").time(), tz).timestamp()
        windows.append((label, to_ts(a), to_ts(b)))
    ocr = []
    leave = []
    with open(sys.argv[2], encoding="utf-8", errors="replace") as stream:
        for raw in stream:
            m = LINE.match(raw.rstrip("\n"))
            if not m:
                continue
            when = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=tz).timestamp()
            if not any(lo <= when < hi for _, lo, hi in windows):
                continue
            body = m.group(4)
            if (o := OCR.search(body)) is not None:
                ocr.append((when, int(m.group(3)), o.group(1), o.group(3), int(o.group(4))))
            elif (lv := LEAVE.match(body)) is not None:
                leave.append((when, int(m.group(3)), lv.group(1), int(lv.group(2))))
    for label, lo, hi in windows:
        calls = [c for c in ocr if lo <= c[0] < hi]
        threads = defaultdict(int)
        for c in calls:
            threads[c[1]] += 1
        tid = max(threads, key=threads.get) if threads else None
        rows = [s for s in samples if lo <= s["epoch"] < hi]
        dt = workers = 0.0
        ocr_series, per_thread = [], defaultdict(list)
        offset = rows[0]["epoch"] - rows[0]["t"] if rows else 0.0
        for a, b in zip(rows, rows[1:]):
            step = b["t"] - a["t"]
            if step <= 0 or step > 5:
                continue
            dt += step
            t0, t1 = a["t"] + offset, b["t"] + offset
            # OCR lines are logged when a call ends; spread each call over [end - cost, end].
            ocr_series.append(sum(max(0.0, min(t1, c[0]) - max(t0, c[0] - c[4] / 1000)) for c in calls
                                  if c[0] > t0 and c[0] - c[4] / 1000 < t1))
            for t, (comm, ticks) in b["threads"].items():
                if t in a["threads"] and ticks >= a["threads"][t][1]:
                    delta = (ticks - a["threads"][t][1]) / clk
                    if comm == "openmp_worker":
                        workers += delta
                    else:
                        per_thread[t].append((len(ocr_series) - 1, delta))
        def corr(points):
            xs = [ocr_series[i] for i, _ in points]
            ys = [v for _, v in points]
            n = len(xs)
            if n < 3:
                return -1.0
            mx, my = sum(xs) / n, sum(ys) / n
            sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            sxx = sum((x - mx) ** 2 for x in xs)
            syy = sum((y - my) ** 2 for y in ys)
            return sxy / (sxx * syy) ** 0.5 if sxx > 0 and syy > 0 else -1.0
        linux_tid = max(per_thread, key=lambda t: corr(per_thread[t])) if per_thread and calls else None
        issuing = sum(v for _, v in per_thread.get(linux_tid, []))
        issuing_r = corr(per_thread[linux_tid]) if linux_tid is not None else 0.0
        wall = sum(c[4] for c in calls) / 1000
        by_kind = defaultdict(lambda: [0, 0])
        for c in calls:
            by_kind[f"{c[2]}/{c[3]}"][0] += 1
            by_kind[f"{c[2]}/{c[3]}"][1] += c[4]
        span = dt or 1
        print(f"== {label}: sampled {dt:.0f}s, OCR calls {len(calls)} on Tx{tid} ({dict(threads)})")
        print("   " + ", ".join(f"{k} n={n} {ms / 1000:.1f}s" for k, (n, ms) in sorted(by_kind.items())))
        print(f"   OCR wall {wall:.1f}s ({100 * wall / span:.1f}% of window); issuing thread tid {linux_tid} "
              f"(r={issuing_r:.2f}) {issuing / span:.3f} cores, openmp workers {workers / span:.3f} cores")
        print(f"   OCR CPU estimate {(wall + workers) / span:.3f} cores; issuing thread non-OCR "
              f"{max(0.0, issuing - wall) / span:.3f} cores")
        top = defaultdict(lambda: [0, 0])
        for when, t, name, ms in leave:
            if lo <= when < hi and t == tid:
                top[name][0] += 1
                top[name][1] += ms
        best = sorted(top.items(), key=lambda kv: -kv[1][1])[:10]
        print("   top leave scopes: " + "; ".join(f"{n} x{c} {ms / 1000:.1f}s" for n, (c, ms) in best))
    return 0

if __name__ == "__main__":
    sys.exit(main())
