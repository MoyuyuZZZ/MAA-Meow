#!/usr/bin/env python3
"""Summarise a sample-cpu-load.sh session (VM only; numbers only).

Usage: analyze-cpu-samples.py raw.txt [label=HH:MM-HH:MM ...]
CPU figures are in "cores busy" (1.0 = one core fully busy). /proc/stat in the container is
host-wide, so totals include fnOS host load. guest time is already part of user time.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import sys

def stat_fields(line):
    """Return (pid, comm, utime+stime ticks) from a /proc/<pid>/stat line."""
    head, _, rest = line.rpartition(")")
    pid, _, comm = head.partition(" (")
    fields = rest.split()
    return int(pid), comm, int(fields[11]) + int(fields[12])

def parse(path):
    samples, clk, tz = [], 100, timezone(timedelta(hours=8))
    cur = None
    pending = None
    with open(path, errors="replace") as stream:
        for raw in stream:
            line = raw.rstrip("\n")
            if pending is not None:
                if line.startswith(("T ", "cpu", "P ", "F ", "Z ", "A ", "TH ")):
                    pending = None
                else:
                    cur[pending] = stat_fields(line)[2]
                    pending = None
                    continue
            tag, _, body = line.partition(" ")
            if tag == "S":
                parts = body.split()
                clk = int(parts[1])
                sign = 1 if parts[-1][0] == "+" else -1
                tz = timezone(sign * timedelta(hours=int(parts[-1][1:3]), minutes=int(parts[-1][3:5])))
            elif tag == "T":
                cur = {"t": float(body.split()[1]), "epoch": int(body.split()[0]), "cores": {}, "threads": {},
                       "procs": {}}
                samples.append(cur)
            elif cur is None:
                continue
            elif tag.startswith("cpu"):
                v = [int(x) for x in body.split()]
                busy = v[0] + v[1] + v[2] + v[5] + v[6] + v[7]
                cur["cores"][tag] = (busy, busy + v[3] + v[4])
            elif tag == "P":
                pending = body.split()[0]
            elif tag == "TH":
                tid, comm, ticks = stat_fields(body)
                cur["threads"][tid] = (comm, ticks)
            elif tag == "A":
                pid, comm, ticks = stat_fields(body)
                cur["procs"][pid] = (comm, ticks)
            elif tag == "F":
                cur["freq"] = [int(x) for x in body.split()]
            elif tag == "Z":
                cur["temp"] = [int(x) / 1000 for x in body.split()]
    return samples, clk, tz

def summarise(samples, clk, start, end):
    rows = [s for s in samples if start <= s["epoch"] < end]
    if len(rows) < 2:
        return None
    acc = defaultdict(float)
    span = rows[-1]["t"] - rows[0]["t"]
    for a, b in zip(rows, rows[1:]):
        dt = b["t"] - a["t"]
        if dt <= 0 or dt > 5:
            continue
        acc["dt"] += dt
        for name, key in (("total", "cpu"),):
            if key in a["cores"] and key in b["cores"]:
                acc[name] += (b["cores"][key][0] - a["cores"][key][0]) / clk
        for core in range(8):
            key = f"cpu{core}"
            if key in a["cores"] and key in b["cores"]:
                acc["little" if core < 4 else "big"] += (b["cores"][key][0] - a["cores"][key][0]) / clk
        for proc in ("maacore", "game"):
            if proc in a and proc in b and b[proc] >= a[proc]:
                acc[proc] += (b[proc] - a[proc]) / clk
        for tid, (comm, ticks) in b["threads"].items():
            if tid in a["threads"] and ticks >= a["threads"][tid][1]:
                acc["openmp" if comm == "openmp_worker" else "maacore_other_threads"] += (ticks - a["threads"][tid][1]) / clk
        acc["npu_1ghz_s"] += dt if b.get("freq", [0, 0, 0, 0])[3] >= 1_000_000_000 else 0
        acc["bigfreq_mhz"] += dt * max(b.get("freq", [0, 0, 0])[1:3]) / 1000
        acc["temp_max"] = max(acc["temp_max"], max(b.get("temp", [0])))
    dumps = [s for s in rows if s["procs"]]
    top = defaultdict(float)
    if len(dumps) >= 2:
        for a, b in zip(dumps, dumps[1:]):
            for pid, (comm, ticks) in b["procs"].items():
                if pid in a["procs"] and ticks >= a["procs"][pid][1]:
                    top[comm] += (ticks - a["procs"][pid][1]) / clk
        dump_span = dumps[-1]["t"] - dumps[0]["t"]
        top = {k: round(v / dump_span, 3) for k, v in sorted(top.items(), key=lambda kv: -kv[1])[:8]}
    dt = acc["dt"] or 1
    return {
        "samples": len(rows), "span_s": round(span),
        "cores_busy_total": round(acc["total"] / dt, 3), "little_busy": round(acc["little"] / dt, 3),
        "big_busy": round(acc["big"] / dt, 3), "maacore": round(acc["maacore"] / dt, 3),
        "maacore_openmp_workers": round(acc["openmp"] / dt, 3),
        "maacore_other_threads_sum": round(acc["maacore_other_threads"] / dt, 3),
        "game": round(acc["game"] / dt, 3), "npu_at_1ghz_share": round(acc["npu_1ghz_s"] / dt, 3),
        "big_cluster_mhz_avg": round(acc["bigfreq_mhz"] / dt), "temp_max_c": acc["temp_max"],
        "top_processes_cores": top,
    }

def main():
    samples, clk, tz = parse(sys.argv[1])
    if not samples:
        print("no samples")
        return 1
    first = datetime.fromtimestamp(samples[0]["epoch"], tz)
    windows = [("all", samples[0]["epoch"], samples[-1]["epoch"] + 1)]
    for spec in sys.argv[2:]:
        label, _, span = spec.partition("=")
        a, _, b = span.partition("-")
        day = first.date()
        to_epoch = lambda hm: int(datetime.combine(day, datetime.strptime(hm, "%H:%M").time(), tz).timestamp())
        windows.append((label, to_epoch(a), to_epoch(b)))
    print(f"first={first:%Y-%m-%d %H:%M:%S %z} last={datetime.fromtimestamp(samples[-1]['epoch'], tz):%H:%M:%S} "
          f"samples={len(samples)}")
    for label, a, b in windows:
        print(label, json.dumps(summarise(samples, clk, a, b), ensure_ascii=False))
    return 0

if __name__ == "__main__":
    sys.exit(main())
