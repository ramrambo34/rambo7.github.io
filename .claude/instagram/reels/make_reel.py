#!/usr/bin/env python3
"""
make_reel.py - build a faceless photo Reel (1080x1920, 30fps) from a JSON spec.

Each shot is one photo with a slow zoom over a blurred, darkened copy of
itself, and one or more text cards shown in turn. Text sits in the top of the
Instagram safe zone (below y=230, left of the right-hand 230px action rail)
so nothing is hidden by the caption, buttons or audio strip.

The video has a silent audio track: add music inside Instagram, where the
library tracks are licensed.

Usage
  python3 make_reel.py reel.json
  python3 make_reel.py reel.json --preview     # half resolution, faster

Spec
  {
    "out": "reel.mp4",
    "shots": [
      {"photo": "photos/1.jpg",
       "cards": [{"text": "You don't need a better camera.", "dur": 2.6}]}
    ]
  }
Photo paths are relative to the spec file.
"""

import argparse
import json
import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

W, H, FPS = 1080, 1920, 30
SAFE_TOP, SAFE_RIGHT = 230, 230
TEXT_X, TEXT_Y = 72, 270
TEXT_W = W - TEXT_X - SAFE_RIGHT - 40   # margin for glyph overhang and shadow
PHOTO_BOX = (0, 560, W, 1660)          # where the sharp photo is fitted
ZOOM = 0.06                             # total slow zoom across a shot
FADE = 0.25                             # text fade-in, seconds

FONTS = [
    "/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf",
    "/usr/share/fonts/opentype/inter/Inter-Bold.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def font(size):
    for path in FONTS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def wrap(draw, text, fnt, width):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=fnt) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    lines.append(line)
    return lines


def text_layer(text):
    """Render one card as an RGBA layer the size of the frame."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    size = 76
    while True:
        fnt = font(size)
        lines = wrap(draw, text, fnt, TEXT_W)
        if len(lines) <= 3 or size <= 52:
            break
        size -= 4
    lh = int(size * 1.18)
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    for i, ln in enumerate(lines):
        sd.text((TEXT_X + 3, TEXT_Y + i * lh + 4), ln, font=fnt, fill=(0, 0, 0, 200))
    shadow = shadow.filter(ImageFilter.GaussianBlur(8))
    layer.alpha_composite(shadow)
    for i, ln in enumerate(lines):
        draw.text((TEXT_X, TEXT_Y + i * lh), ln, font=fnt, fill=(255, 255, 255, 255))
    return layer


def background(photo):
    bg = ImageOps.fit(photo, (W, H), Image.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(40))
    return Image.blend(bg, Image.new("RGB", (W, H), (0, 0, 0)), 0.55)


def fitted(photo):
    bx0, by0, bx1, by1 = PHOTO_BOX
    bw, bh = bx1 - bx0, by1 - by0
    scale = min(bw / photo.width, bh / photo.height)
    return photo.resize((round(photo.width * scale), round(photo.height * scale)), Image.LANCZOS)


def ease(t):
    return t * t * (3 - 2 * t)


def frames(spec, base):
    for shot in spec["shots"]:
        photo = ImageOps.exif_transpose(Image.open(os.path.join(base, shot["photo"]))).convert("RGB")
        bg, fg = background(photo), fitted(photo)
        cx, cy = W // 2, (PHOTO_BOX[1] + PHOTO_BOX[3]) // 2
        cards = [(c["dur"], text_layer(c["text"])) for c in shot["cards"]]
        total = sum(d for d, _ in cards)
        n_total = round(total * FPS)
        frame_i = 0
        for dur, layer in cards:
            n = round(dur * FPS)
            for k in range(n):
                z = 1 + ZOOM * ease(min(frame_i / max(n_total - 1, 1), 1))
                img = bg.copy()
                zw, zh = round(fg.width * z), round(fg.height * z)
                img.paste(fg.resize((zw, zh), Image.BILINEAR), (cx - zw // 2, cy - zh // 2))
                a = min(k / (FADE * FPS), 1)
                if a < 1:
                    lay = layer.copy()
                    lay.putalpha(lay.getchannel("A").point(lambda v: int(v * a)))
                    lay = lay.transform(lay.size, Image.AFFINE, (1, 0, 0, 0, 1, -int(18 * (1 - a))))
                else:
                    lay = layer
                img = img.convert("RGBA")
                img.alpha_composite(lay)
                yield img.convert("RGB")
                frame_i += 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--preview", action="store_true", help="half resolution, faster")
    args = ap.parse_args()

    with open(args.spec) as f:
        spec = json.load(f)
    base = os.path.dirname(os.path.abspath(args.spec))
    out = os.path.join(base, spec.get("out", "reel.mp4"))
    if args.preview:
        out = out.replace(".mp4", "-preview.mp4")
    scale = ["-vf", f"scale={W // 2}:{H // 2}"] if args.preview else []

    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
           *scale, "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-shortest", "-movflags", "+faststart", out]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    count = 0
    for img in frames(spec, base):
        proc.stdin.write(img.tobytes())
        count += 1
    proc.stdin.close()
    if proc.wait() != 0:
        sys.exit("ffmpeg failed")
    print(f"{out}  {count / FPS:.1f}s  {count} frames")


if __name__ == "__main__":
    main()
