#!/usr/bin/env python3
"""Aggregate MaaCore OCR timing from asst.log files (VM only; prints statistics, never OCR text).

Counts "by OCR Rec" / "by OCR Pipeline" lines per OCR class, their reported cost, the number
of text boxes per pipeline call, and the OCR share of each TaskChainStart..Completed window.
"""
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import re
import statistics
import sys

LINE = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3})\]\[\w+\]\[Px(\d+)\]\[Tx(\d+)\] (.*)$")
OCR = re.compile(r"asst::(\w+Ocr) \[(.*)\] by OCR (Rec|Pipeline) , cost (\d+) ms$")
CHAIN = re.compile(r"append_callback \| (TaskChainStart|TaskChainCompleted|TaskChainError|TaskChainStopped) "
                   r"\{\"taskchain\":\"(\w+)\",\"taskid\":(\d+)")

def percentiles(values):
    if not values:
        return {}
    ordered = sorted(values)
    pick = lambda q: ordered[min(len(ordered) - 1, int(q * len(ordered)))]
    return {"n": len(values), "sum_ms": sum(values), "mean": round(statistics.fmean(values), 1),
            "p50": pick(0.5), "p90": pick(0.9), "p99": pick(0.99), "max": ordered[-1]}

def main():
    calls = defaultdict(list)
    boxes = []
    events = []
    threads = defaultdict(int)
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8", errors="replace") as stream:
            for raw in stream:
                match = LINE.match(raw.rstrip("\n"))
                if not match:
                    continue
                stamp, pid, tid, body = match.groups()
                when = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S.%f").timestamp()
                if (ocr := OCR.search(body)) is not None:
                    cls, results, mode, cost = ocr.group(1), ocr.group(2), ocr.group(3), int(ocr.group(4))
                    calls[(cls, mode)].append(cost)
                    threads[tid] += 1
                    if mode == "Pipeline":
                        boxes.append(results.count("{ text:"))
                    events.append((pid, when, "ocr", (cls, mode, cost)))
                elif (chain := CHAIN.search(body)) is not None:
                    events.append((pid, when, chain.group(1), (chain.group(2), int(chain.group(3)))))
    events.sort(key=lambda e: (e[0], e[1]))
    chains = defaultdict(lambda: {"runs": 0, "wall_s": 0.0, "ocr_ms": 0, "rec_ms": 0, "pipe_ms": 0, "ocr_calls": 0})
    open_chain = {}
    for pid, when, kind, data in events:
        if kind == "TaskChainStart":
            open_chain[pid] = {"name": data[0], "id": data[1], "start": when, "ocr": 0, "rec": 0, "pipe": 0, "n": 0}
        elif kind == "ocr" and pid in open_chain:
            current = open_chain[pid]
            current["ocr"] += data[2]
            current["rec" if data[1] == "Rec" else "pipe"] += data[2]
            current["n"] += 1
        elif kind != "ocr" and pid in open_chain and open_chain[pid]["id"] == data[1]:
            current = open_chain.pop(pid)
            row = chains[current["name"]]
            row["runs"] += 1
            row["wall_s"] += when - current["start"]
            row["ocr_ms"] += current["ocr"]
            row["rec_ms"] += current["rec"]
            row["pipe_ms"] += current["pipe"]
            row["ocr_calls"] += current["n"]
    summary = {
        "calls": {f"{cls}/{mode}": percentiles(values) for (cls, mode), values in sorted(calls.items())},
        "pipeline_boxes": percentiles(boxes),
        "ocr_threads": len(threads),
        "chains": {},
    }
    total = {"wall_s": 0.0, "ocr_ms": 0, "rec_ms": 0, "pipe_ms": 0, "ocr_calls": 0}
    for name, row in sorted(chains.items(), key=lambda item: -item[1]["ocr_ms"]):
        for key in total:
            total[key] += row[key]
        summary["chains"][name] = dict(row, wall_s=round(row["wall_s"], 1),
                                       ocr_share=round(row["ocr_ms"] / 1000 / row["wall_s"], 4) if row["wall_s"] else 0)
    summary["chains_total"] = dict(total, wall_s=round(total["wall_s"], 1),
                                   ocr_share=round(total["ocr_ms"] / 1000 / total["wall_s"], 4) if total["wall_s"] else 0)
    print(json.dumps(summary, indent=1, ensure_ascii=False))

if __name__ == "__main__":
    main()
