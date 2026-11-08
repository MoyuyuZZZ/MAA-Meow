#!/usr/bin/env python3
"""Compare MaaCore rec ONNX (onnxruntime, CPU reference) with the RKNN FP16 simulator (VM only).

Run with .cache/rk3588/rknn-venv/bin/python. Text lines come from MaaMeow debug screenshots:
  word: det ONNX (MaaCore preprocessing) finds lines, which are perspective-cropped;
  char: CharOcr ROIs logged with absolute coordinates for the analysed frame, plus the text
        MaaCore (ncnn) reported, giving a three-way check.
Rec preprocessing mirrors OcrPackNcnn::recognize_line; RKNN gets uint8 NHWC BGR because the
normalisation is folded into the model. Crops and per-line results stay in .cache (ignored).
"""
import argparse
import json
import math
from pathlib import Path
import re
import sys

import cv2
import numpy as np
import onnxruntime as ort
from rknn.api import RKNN

ROOT = Path(__file__).resolve().parents[2]
ONNX_DIR = ROOT / ".cache/rk3588/ocr-eval/onnx"
RESOURCE = ROOT / "app/src/main/assets/MaaSync/MaaResource"
OUT = ROOT / ".cache/rk3588/ocr-rknn"
MODELS = {"word": ("PaddleOCR", "WordOcr"), "char": ("PaddleCharOCR", "CharOcr")}
REC_H, REC_W = 48, 320
OCR_LINE = re.compile(r"^\[([\d\- :.]+)\].*asst::(\w+Ocr) \[\{ text: (.*?), rect: \[ \d+ \((\d+)\), \d+ \((\d+)\), "
                      r"(\d+), (\d+) \], score: ([\d.]+) \}\] by OCR Rec")

def charset(model_dir, classes):
    keys = (RESOURCE / model_dir / "rec/keys.txt").read_text(encoding="utf-8-sig").splitlines()
    chars = ["<blank>"] + keys
    if classes == len(chars) + 1:
        chars.append(" ")
    return chars

def decode(prob, chars):
    best = prob.argmax(axis=1)
    text, confs, prev = [], [], -1
    for t, c in enumerate(best):
        if c != 0 and c != prev and c < len(chars):
            text.append(chars[c])
            confs.append(float(prob[t, c]))
        prev = c
    return "".join(text), (sum(confs) / len(confs) if confs else 0.0)

def rec_canvas(line):
    ratio = line.shape[1] / max(1, line.shape[0])
    width = min(REC_W, max(1, math.ceil(REC_H * ratio)))
    canvas = np.full((REC_H, REC_W, 3), 127, np.uint8)
    canvas[:, :width] = cv2.resize(line, (width, REC_H))
    return canvas

def det_lines(session, image):
    h, w = image.shape[:2]
    ratio = min(1.0, 960 / max(h, w))
    align = lambda v: max(32, int(round(v / 32)) * 32)
    rh, rw = align(int(h * ratio)), align(int(w * ratio))
    x = (cv2.resize(image, (rw, rh)).astype(np.float32) - np.array([0.485, 0.456, 0.406]) * 255) / (
        np.array([0.229, 0.224, 0.225]) * 255)
    prob = session.run(None, {"x": x.transpose(2, 0, 1)[None].astype(np.float32)})[0][0, 0]
    contours, _ = cv2.findContours((prob > 0.3).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    lines = []
    for contour in contours:
        if len(contour) <= 2:
            continue
        mask = np.zeros_like(prob, np.uint8)
        cv2.fillPoly(mask, [contour], 1)
        if cv2.mean(prob, mask)[0] < 0.6:
            continue
        rect = cv2.minAreaRect(contour)
        (cx, cy), (bw, bh), angle = rect
        if max(bw, bh) < 3:
            continue
        area, length = bw * bh, 2 * (bw + bh)
        grow = area * 1.5 / length * 2
        box = cv2.boxPoints(((cx, cy), (bw + grow, bh + grow), angle))
        box[:, 0] = np.clip(box[:, 0] * w / rw, 0, w)
        box[:, 1] = np.clip(box[:, 1] * h / rh, 0, h)
        s = box.sum(1)
        d = np.diff(box, axis=1).ravel()
        tl, br, tr, bl = box[s.argmin()], box[s.argmax()], box[d.argmin()], box[d.argmax()]
        cw = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
        ch = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
        if cw <= 4 or ch <= 4:
            continue
        m = cv2.getPerspectiveTransform(np.float32([tl, tr, br, bl]), np.float32([[0, 0], [cw, 0], [cw, ch], [0, ch]]))
        crop = cv2.warpPerspective(image, m, (cw, ch), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        if ch >= cw * 1.5:
            crop = cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE)
        lines.append({"crop": crop, "box": [int(v) for v in cv2.boundingRect(box.astype(np.int32))], "ncnn": None})
    return lines

def char_lines(image_path, log_text):
    # The saved frame is the one analysed since the preceding analyze-enter line.
    image = cv2.imread(str(image_path))
    end = log_text.find(f"debug/oper/{image_path.name}")
    if end < 0:
        return []
    begin = log_text.rfind("OperBoxImageAnalyzer::analyze | enter", 0, end)
    lines = []
    for raw in log_text[begin:end].splitlines():
        match = OCR_LINE.match(raw)
        if match and match.group(2) == "CharOcr":
            x, y, w, h = map(int, match.group(4, 5, 6, 7))
            lines.append({"crop": image[y:y + h, x:x + w], "box": [x, y, w, h], "ncnn": match.group(3),
                          "ncnn_score": float(match.group(8))})
    return lines

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_dir", type=Path, help="Extracted maa_logs_* directory")
    parser.add_argument("--kind", choices=MODELS, default="word")
    args = parser.parse_args()
    model_dir, ocr_class = MODELS[args.kind]
    images = sorted((args.log_dir / "oper").glob("*_raw.png"))
    if args.kind == "word":
        det = ort.InferenceSession(str(ONNX_DIR / f"{model_dir}_det/inference.onnx"), providers=["CPUExecutionProvider"])
        lines = [dict(line, image=p.name) for p in images for line in det_lines(det, cv2.imread(str(p)))]
    else:
        log_text = (args.log_dir / "asst.log").read_text(encoding="utf-8", errors="replace")
        lines = [dict(line, image=p.name) for p in images for line in char_lines(p, log_text)]
    lines = [line for line in lines if line["crop"].size]
    onnx_path = ONNX_DIR / f"{model_dir}_rec/inference.onnx"
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    rknn = RKNN(verbose=False)
    rknn.config(mean_values=[[127.5] * 3], std_values=[[127.5] * 3], target_platform="rk3588")
    if rknn.load_onnx(model=str(onnx_path), inputs=["x"], input_size_list=[[1, 3, REC_H, REC_W]]) != 0 or \
            rknn.build(do_quantization=False) != 0 or rknn.init_runtime() != 0:
        print("RKNN simulator setup failed")
        return 1
    chars = None
    rows, canvases, refs = [], [], []
    for line in lines:
        canvas = rec_canvas(line["crop"])
        ref_in = ((canvas.astype(np.float32) - 127.5) / 127.5).transpose(2, 0, 1)[None]
        ref = session.run(None, {"x": ref_in})[0][0]
        canvases.append(canvas)
        refs.append(ref)
        sim = np.asarray(rknn.inference(inputs=[canvas[None]], data_format=["nhwc"])[0], np.float32).reshape(ref.shape)
        chars = chars or charset(model_dir, ref.shape[1])
        ref_text, ref_conf = decode(ref, chars)
        sim_text, sim_conf = decode(sim, chars)
        cosine = float((ref * sim).sum() / (np.linalg.norm(ref) * np.linalg.norm(sim) + 1e-12))
        rows.append({"image": line["image"], "box": line["box"], "onnx": ref_text, "onnx_conf": round(ref_conf, 6),
                     "rknn_sim": sim_text, "rknn_conf": round(sim_conf, 6), "cosine": round(cosine, 6),
                     "max_abs_diff": round(float(np.abs(ref - sim).max()), 6),
                     "argmax_agree": round(float((ref.argmax(1) == sim.argmax(1)).mean()), 4),
                     "ncnn": line["ncnn"]})
        if line["ncnn"] is not None:
            rows[-1]["ncnn_score"] = line["ncnn_score"]
    rknn.release()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"compare-{args.kind}.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
    # Device probe input (packed uint8 NHWC BGR canvases) and the ONNX reference it is checked against.
    np.stack(canvases).tofile(OUT / f"canvases-{args.kind}.u8")
    ref_all = np.stack(refs).astype(np.float32)
    np.savez_compressed(OUT / f"reference-{args.kind}.npz", argmax=ref_all.argmax(2).astype(np.int32),
                        maxprob=ref_all.max(2), full=ref_all[:8], chars=np.array(chars, dtype=object))
    n = len(rows)
    same = sum(r["onnx"] == r["rknn_sim"] for r in rows)
    nonempty = sum(bool(r["onnx"]) for r in rows)
    print(f"{ocr_class} lines={n} non_empty_onnx={nonempty} text_equal_onnx_vs_rknn_sim={same}/{n}")
    if n:
        cos = sorted(r["cosine"] for r in rows)
        print(f"cosine min={cos[0]:.6f} p10={cos[n // 10]:.6f} median={cos[n // 2]:.6f}; "
              f"max_abs_diff max={max(r['max_abs_diff'] for r in rows):.6f}; "
              f"argmax_agree min={min(r['argmax_agree'] for r in rows):.4f}; "
              f"mean |conf diff|={sum(abs(r['onnx_conf'] - r['rknn_conf']) for r in rows) / n:.6f}")
    ncnn_rows = [r for r in rows if r["ncnn"] is not None]
    if ncnn_rows:
        print(f"vs MaaCore ncnn log: onnx_equal={sum(r['onnx'] == r['ncnn'] for r in ncnn_rows)}/{len(ncnn_rows)} "
              f"rknn_sim_equal={sum(r['rknn_sim'] == r['ncnn'] for r in ncnn_rows)}/{len(ncnn_rows)}")
    for r in [r for r in rows if r["onnx"] != r["rknn_sim"]][:15]:
        print(f"DIFF {r['image']} box={r['box']} onnx={r['onnx']!r}({r['onnx_conf']:.3f}) "
              f"rknn_sim={r['rknn_sim']!r}({r['rknn_conf']:.3f}) cos={r['cosine']:.4f}")
    print(f"Per-line results: {(OUT / f'compare-{args.kind}.json').relative_to(ROOT)}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
