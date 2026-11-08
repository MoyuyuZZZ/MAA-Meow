#!/usr/bin/env python3
"""Convert a MaaCore PP-OCR rec ONNX to an RK3588 FP16 RKNN model (VM only).

Run with .cache/rk3588/rknn-venv/bin/python. MaaCore pads every text line to a fixed
1x3x48x320 BGR tensor normalised as (x - 127.5) / 127.5; that normalisation is folded
into the RKNN model so the device feeds uint8 NHWC BGR directly. No quantisation.
"""
import argparse
import os
from pathlib import Path
import sys

from rknn.api import RKNN

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".cache/rk3588/ocr-rknn"

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="PaddleOCR_rec", help="Directory name under ocr-eval/onnx")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    os.chdir(OUT)  # RKNN-Toolkit writes check*.onnx intermediates into the working directory
    onnx = ROOT / ".cache/rk3588/ocr-eval/onnx" / args.model / "inference.onnx"
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{args.model}-rk3588-fp16-48x320.rknn"
    rknn = RKNN(verbose=True, verbose_file=str(OUT / f"{args.model}-convert.log"))
    rknn.config(mean_values=[[127.5, 127.5, 127.5]], std_values=[[127.5, 127.5, 127.5]],
                target_platform="rk3588")
    steps = (
        ("load_onnx", lambda: rknn.load_onnx(model=str(onnx), inputs=["x"], input_size_list=[[1, 3, 48, 320]])),
        ("build", lambda: rknn.build(do_quantization=False)),
        ("export_rknn", lambda: rknn.export_rknn(str(target))),
    )
    for name, step in steps:
        ret = step()
        print(f"{name}={'PASS' if ret == 0 else 'FAIL'} ret={ret}", flush=True)
        if ret != 0:
            rknn.release()
            return 1
    rknn.release()
    print(f"RKNN model: {target.relative_to(ROOT)}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
