#!/usr/bin/env python3
"""Check a device rknn-rec result file against the ONNX reference (VM only, rknn-venv python)."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".cache/rk3588/ocr-rknn"

def decode(classes, probs, chars):
    text, confs, prev = [], [], -1
    for c, p in zip(classes, probs):
        if c != 0 and c != prev and c < len(chars):
            text.append(chars[c])
            confs.append(float(p))
        prev = c
    return "".join(text), (sum(confs) / len(confs) if confs else 0.0)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("word", "char"))
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    ref = np.load(OUT / f"reference-{args.kind}.npz", allow_pickle=True)
    rows = json.loads((OUT / f"compare-{args.kind}.json").read_text())
    chars = list(ref["chars"])
    lines, steps = ref["argmax"].shape
    full_lines, classes = ref["full"].shape[0], ref["full"].shape[2]
    data = args.result.read_bytes()
    expected = lines * steps * 8 + full_lines * steps * classes * 4
    if len(data) != expected:
        print(f"FAIL result size {len(data)} != {expected}")
        return 1
    best = np.frombuffer(data, np.int32, lines * steps).reshape(lines, steps)
    prob = np.frombuffer(data, np.float32, lines * steps, lines * steps * 4).reshape(lines, steps)
    full = np.frombuffer(data, np.float32, offset=lines * steps * 8).reshape(full_lines, steps, classes)
    texts = [decode(best[i], prob[i], chars) for i in range(lines)]
    equal = [texts[i][0] == rows[i]["onnx"] for i in range(lines)]
    sim_equal = sum(texts[i][0] == rows[i]["rknn_sim"] for i in range(lines))
    agree = (best == ref["argmax"]).mean(axis=1)
    cosine = [float((full[i] * ref["full"][i]).sum() / (np.linalg.norm(full[i]) * np.linalg.norm(ref["full"][i])))
              for i in range(full_lines)]
    print(f"{args.kind}: lines={lines} device_text_equal_onnx={sum(equal)}/{lines} "
          f"device_text_equal_rknn_sim={sim_equal}/{lines}")
    print(f"timestep argmax agreement min={agree.min():.4f} mean={agree.mean():.4f}; "
          f"max prob |diff| max={np.abs(prob - ref['maxprob']).max():.6f}; "
          f"full-output cosine (first {full_lines}) min={min(cosine):.6f}")
    ncnn = [r for r in rows if r.get("ncnn") is not None]
    if ncnn:
        print(f"vs MaaCore ncnn log: device_equal={sum(texts[i][0] == rows[i]['ncnn'] for i in range(lines))}/{len(ncnn)}")
    for i in [i for i in range(lines) if not equal[i]][:15]:
        print(f"DIFF line={i} image={rows[i]['image']} box={rows[i]['box']} onnx={rows[i]['onnx']!r} "
              f"device={texts[i][0]!r}({texts[i][1]:.3f})")
    ok = sum(equal) >= lines - sum(r["onnx"] != r["rknn_sim"] for r in rows)
    print("REC_DEVICE_CHECK=" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
