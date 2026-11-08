#!/usr/bin/env python3
"""Static RKNN suitability check for MaaCore ONNX models (VM only, no third-party deps).

Fetches the pinned upstream OCR ONNX sources, reads them with a minimal protobuf parser
(opsets, graph I/O shapes, op histogram) and cross-checks each op against the pinned
RKNN-Toolkit2 ONNX op support table. Being listed there does not prove NPU execution.
"""
import collections
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bootstrap import CACHE, ROOT, download

LOCK = json.loads(Path(__file__).with_name("ocr-onnx.lock.json").read_text())
OUT = CACHE / "ocr-eval"
ASSET_ONNX = ROOT / "app/src/main/assets/MaaSync/MaaResource/onnx"

def varint(buf, i):
    shift = value = 0
    while True:
        b = buf[i]
        i += 1
        value |= (b & 0x7F) << shift
        if b < 0x80:
            return value, i
        shift += 7

def fields(buf):
    i = 0
    while i < len(buf):
        key, i = varint(buf, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = varint(buf, i)
        elif wire == 1:
            value, i = buf[i:i + 8], i + 8
        elif wire == 2:
            length, i = varint(buf, i)
            value, i = buf[i:i + length], i + length
        elif wire == 5:
            value, i = buf[i:i + 4], i + 4
        else:
            raise ValueError(f"Unsupported protobuf wire type {wire}")
        yield number, value

def value_info(buf):
    name, dims = "", []
    for n, v in fields(buf):
        if n == 1:
            name = v.decode()
        elif n == 2:
            for tn, tv in fields(v):
                if tn != 1:
                    continue
                for sn, sv in fields(tv):
                    if sn != 2:
                        continue
                    for dn, dv in fields(sv):
                        if dn == 1:
                            dim = "?"
                            for xn, xv in fields(dv):
                                dim = xv if xn == 1 else xv.decode() if xn == 2 else dim
                            dims.append(dim)
    return {"name": name, "dims": dims}

def inspect(path):
    data = path.read_bytes()
    report = {"file": path.name if path.name != "inference.onnx" else path.parent.name + ".onnx",
              "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "producer": "",
              "opsets": {}, "inputs": [], "outputs": [], "ops": collections.Counter()}
    for n, v in fields(data):
        if n == 2:
            report["producer"] = v.decode()
        elif n == 8:
            domain, version = "", 0
            for on, ov in fields(v):
                domain = ov.decode() if on == 1 else domain
                version = ov if on == 2 else version
            report["opsets"][domain or "ai.onnx"] = version
        elif n == 7:
            initializers, graph_inputs = set(), []
            for gn, gv in fields(v):
                if gn == 1:
                    for nn, nv in fields(gv):
                        if nn == 4:
                            report["ops"][nv.decode()] += 1
                elif gn == 5:
                    for tn, tv in fields(gv):
                        if tn == 8:
                            initializers.add(tv.decode())
                elif gn == 11:
                    graph_inputs.append(value_info(gv))
                elif gn == 12:
                    report["outputs"].append(value_info(gv))
            report["inputs"] = [x for x in graph_inputs if x["name"] not in initializers]
    report["ops"] = dict(report["ops"].most_common())
    return report

def fetch(item, directory):
    path = download(item, directory)
    data = path.read_bytes()
    if len(data) != item["bytes"] or hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() != item["git_blob_sha1"]:
        raise SystemExit(f"Upstream blob mismatch: {item['name']}")
    return path

def main():
    support_md = fetch(LOCK["op_support"], OUT).read_text()
    support = {}
    for line in support_md.splitlines():
        match = re.match(r"\|\s*([A-Za-z0-9_]+)\s*\|\s*(.*?)\s*\|\s*$", line)
        if match and match.group(1) not in ("Operators",):
            support.setdefault(match.group(1), match.group(2))
    models = [fetch(item, OUT / "onnx" / item["name"].removesuffix(".onnx")) for item in LOCK["files"]]
    models += sorted(ASSET_ONNX.glob("*.onnx"))
    reports = []
    for path in models:
        report = inspect(path)
        report["op_support"] = {op: support.get(op, "NOT LISTED") or "listed" for op in report["ops"]}
        report["unsupported"] = [op for op, note in report["op_support"].items()
                                 if note == "NOT LISTED" or "Not Supported" in note]
        reports.append(report)
        dims = lambda items: "; ".join(f"{x['name']}={x['dims']}" for x in items)
        print(f"== {report['file']} opsets={report['opsets']} producer={report['producer'] or '-'}")
        print(f"   in: {dims(report['inputs'])}  out: {dims(report['outputs'])}")
        notes = {op: note for op, note in report["op_support"].items() if note != "listed"}
        print(f"   ops={len(report['ops'])} unsupported={report['unsupported'] or 'none'} notes={notes or 'none'}")
    (OUT / "onnx-inspect.json").write_text(json.dumps(reports, indent=1, ensure_ascii=False) + "\n")
    print(f"Report: {(OUT / 'onnx-inspect.json').relative_to(ROOT)}")

if __name__ == "__main__":
    main()
