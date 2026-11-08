#!/usr/bin/env python3
"""Fetch the pinned official MobileNet V1 sample and decode its JPEGs to raw RGB on the VM."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bootstrap import CACHE, download

LOCK = json.loads(Path(__file__).with_name("rknn-mobilenet.lock.json").read_text())
OUT = CACHE / "rknn-infer"
WIDTH = HEIGHT = 224

def git_blob_sha1(path):
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()

def main():
    files = {}
    for item in LOCK["files"]:
        path = download(item, OUT / "official")
        if path.stat().st_size != item["bytes"] or git_blob_sha1(path) != item["git_blob_sha1"]:
            raise SystemExit(f"Upstream blob mismatch: {path.name}")
        files[path.name] = path
    wheel = download(LOCK["jpeg_decoder"], OUT / "downloads")
    site = OUT / f"pillow-{LOCK['jpeg_decoder']['version']}"
    if not (site / "PIL/__init__.py").is_file():
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(site)
    sys.path.insert(0, str(site))
    from PIL import Image
    manifest = {"decoder": f"Pillow {Image.__version__}", "inputs": {}}
    for name in ("dog_224x224.jpg", "cat_224x224.jpg"):
        with Image.open(files[name]) as image:
            if image.size != (WIDTH, HEIGHT):
                raise SystemExit(f"Unexpected size for {name}: {image.size}")
            rgb = image.convert("RGB").tobytes()
        if len(rgb) != WIDTH * HEIGHT * 3:
            raise SystemExit(f"Unexpected RGB length for {name}")
        target = OUT / name.replace(".jpg", ".rgb")
        target.write_bytes(rgb)
        manifest["inputs"][target.name] = {
            "source": name, "layout": "NHWC uint8 RGB 1x224x224x3",
            "bytes": len(rgb), "sha256": hashlib.sha256(rgb).hexdigest()}
    (OUT / "inputs.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))

if __name__ == "__main__":
    main()
