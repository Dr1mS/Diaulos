#!/usr/bin/env python3
"""Render docs/images/hero.gif: an illustrated Diaulos exchange between two idle sessions.

A scripted illustration, not a screen recording. It shows what the bridge does:
both sessions are idle, a message wakes the receiver, its reply wakes the sender.
Needs Pillow (development only): python3 docs/images/make_demo.py
"""
import math
import os

from PIL import Image, ImageDraw, ImageFont

W, H = 1040, 510
FPS = 12
BG = (13, 17, 23)
PANEL = (22, 27, 34)
BORDER = (48, 54, 61)
TEXT = (230, 237, 243)
DIM = (125, 133, 144)
CLAUDE = (217, 119, 87)   # warm orange
AGY = (88, 166, 255)      # blue
GREEN = (63, 185, 80)
YELLOW = (210, 153, 34)
PURPLE = (188, 140, 255)

MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
SANS_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
f_mono = ImageFont.truetype(MONO, 14)
f_mono_b = ImageFont.truetype(MONO_B, 14)
f_title = ImageFont.truetype(SANS_B, 13)
f_small = ImageFont.truetype(SANS, 12)
f_caption = ImageFont.truetype(SANS, 15)
f_env = ImageFont.truetype(SANS_B, 11)

LEFT = (24, 56, 456, 400)     # x0, y0, x1, y1 of each terminal
RIGHT = (584, 56, 1016, 400)
LINE_H = 21

# Timeline, in seconds: (start, kind, panel, payload)
PROMPT = "ask the agy session in ~/api to review my last commit"
SCRIPT = [
    (0.6, "type", "L", ("› ", PROMPT, TEXT)),
    (4.2, "line", "L", ("● ", "agent-bridge · send → agy@api", CLAUDE)),
    (4.6, "line", "L", ("  ", "sent 7c1e… to agy:api-review", DIM)),
    (4.8, "fly", "LR", "Review commit 3f2a1c7"),
    (6.4, "wake", "R", "woken by relay"),
    (6.6, "line", "R", ("", '<bridge-message from="claude:review">', PURPLE)),
    (6.9, "line", "R", ("", "Review commit 3f2a1c7 and send me your findings.", TEXT)),
    (7.5, "line", "R", ("● ", "Reading src/config.py", AGY)),
    (8.1, "line", "R", ("● ", "Running pytest -q … 41 passed", AGY)),
    (8.8, "line", "R", ("✓ ", "2 issues found", GREEN)),
    (9.3, "line", "R", ("● ", "send → claude:review", AGY)),
    (9.6, "fly", "RL", "2 issues found"),
    (11.2, "wake", "L", "woken by MCP channel"),
    (11.4, "line", "L", ("", '<channel from="agy:api-review">', PURPLE)),
    (11.7, "line", "L", ("", "1. parse_config() returns None unchecked", TEXT)),
    (12.0, "line", "L", ("", "2. no test for an empty config file", TEXT)),
    (12.7, "type", "L", ("● ", "Fixed the None check. Add the missing test?", CLAUDE)),
    (15.2, "caption", "", ""),
]
END = 19.0
FLY_SECONDS = 1.5


def rounded(d, box, r, fill, outline=None):
    d.rounded_rectangle(box, r, fill=fill, outline=outline, width=1)


def terminal(d, box, title, who, color, status, status_color):
    x0, y0, x1, y1 = box
    rounded(d, box, 10, PANEL, BORDER)
    d.rounded_rectangle((x0, y0, x1, y0 + 30), 10, fill=(31, 36, 44))
    d.rectangle((x0, y0 + 20, x1, y0 + 30), fill=(31, 36, 44))
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse((x0 + 12 + i * 18, y0 + 10, x0 + 22 + i * 18, y0 + 20), fill=c)
    d.text((x0 + 70, y0 + 8), title, font=f_title, fill=TEXT)
    w = d.textlength(who, font=f_mono_b)
    d.text((x1 - 14 - w, y0 + 8), who, font=f_mono_b, fill=color)
    # status pill under the window
    pill = f"● {status}"
    pw = d.textlength(pill, font=f_small) + 20
    rounded(d, (x0, y1 + 10, x0 + pw, y1 + 34), 12, (31, 36, 44), BORDER)
    d.text((x0 + 10, y1 + 15), pill, font=f_small, fill=status_color)


def wrap(text, width_px, font):
    words, lines, cur = text.split(" "), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if cur and ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(test, font=font) > width_px:
            lines.append(cur)
            cur = w
        else:
            cur = test
    lines.append(cur)
    return lines


def envelope(d, cx, cy, label, color, alpha_scale=1.0):
    w, h = 176, 40
    x0, y0 = cx - w / 2, cy - h / 2
    glow = tuple(int(c * 0.35) for c in color)
    rounded(d, (x0 - 4, y0 - 4, x0 + w + 4, y0 + h + 4), 12, glow)
    rounded(d, (x0, y0, x0 + w, y0 + h), 9, (240, 242, 246))
    d.line((x0 + 6, y0 + 6, cx, cy + 4, x0 + w - 6, y0 + 6), fill=color, width=2)
    tw = d.textlength(label, font=f_env)
    d.text((cx - tw / 2, y0 + h - 16), label, font=f_env, fill=(40, 44, 52))


def frame(t):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((24, 18), "Diaulos", font=ImageFont.truetype(SANS_B, 18), fill=TEXT)
    d.text((112, 22), "two idle sessions, one message each way", font=f_small, fill=DIM)

    panels = {"L": [], "R": []}
    status = {"L": ("idle · 0 tokens", DIM), "R": ("idle · 0 tokens", DIM)}
    flights, caption = [], False
    for start, kind, panel, payload in SCRIPT:
        if t < start:
            continue
        if kind == "type":
            prefix, text, color = payload
            n = min(len(text), int((t - start) * 28))
            cursor = "▌" if n < len(text) else ""
            panels[panel].append((prefix, text[:n] + cursor, color))
            if panel == "L" and start < 4:
                status["L"] = ("you are typing", TEXT)
        elif kind == "line":
            panels[panel].append(payload)
        elif kind == "fly":
            p = (t - start) / FLY_SECONDS
            if p < 1:
                flights.append((panel, p, payload))
        elif kind == "wake":
            status[panel] = (payload, GREEN)
        elif kind == "caption":
            caption = True
    if t >= 4.2 and t < 11.2:
        status["L"] = ("idle · waiting, 0 tokens", DIM)
    if t >= 9.6:
        status["R"] = ("idle again · 0 tokens", DIM)

    terminal(d, LEFT, "Claude Code  ~/web", "claude:review", CLAUDE, *status["L"])
    terminal(d, RIGHT, "agy  ~/api", "agy:api-review", AGY, *status["R"])

    for key, box in (("L", LEFT), ("R", RIGHT)):
        x0, y0, x1, y1 = box
        rows = []
        for prefix, text, color in panels[key]:
            for i, part in enumerate(wrap(text, x1 - x0 - 50, f_mono)):
                rows.append(((prefix if i == 0 else " " * len(prefix)), part, color))
        rows = rows[-14:]
        y = y0 + 42
        for prefix, text, color in rows:
            d.text((x0 + 14, y), prefix, font=f_mono_b, fill=color)
            d.text((x0 + 14 + d.textlength(prefix, font=f_mono_b), y), text, font=f_mono, fill=color)
            y += LINE_H
        if not panels[key]:
            d.text((x0 + 14, y0 + 42), "› ", font=f_mono_b, fill=DIM)
            if key == "R":
                z = "z" * (1 + int(t * 2) % 3)
                d.text((x1 - 60, y1 - 40), z, font=f_mono_b, fill=DIM)

    # the lane between the windows
    lx0, lx1, ly = LEFT[2] + 8, RIGHT[0] - 8, 228
    for x in range(int(lx0), int(lx1), 10):
        d.line((x, ly, x + 4, ly), fill=BORDER, width=2)
    d.text(((lx0 + lx1) / 2 - 26, ly + 14), "Diaulos", font=f_title, fill=DIM)
    for direction, p, label in flights:
        ease = 0.5 - 0.5 * math.cos(math.pi * p)
        sx, ex = (LEFT[2] - 60, RIGHT[0] + 60) if direction == "LR" else (RIGHT[0] + 60, LEFT[2] - 60)
        cx = sx + (ex - sx) * ease
        cy = ly - 70 * math.sin(math.pi * p)
        envelope(d, cx, cy, label, CLAUDE if direction == "LR" else AGY)

    if caption:
        msg = "Both sessions were idle. Diaulos woke each one: no polling, no copy-paste."
        tw = d.textlength(msg, font=f_caption)
        rounded(d, ((W - tw) / 2 - 16, H - 44, (W + tw) / 2 + 16, H - 12), 14, (31, 36, 44), BORDER)
        d.text(((W - tw) / 2, H - 37), msg, font=f_caption, fill=TEXT)
    return img


def main():
    frames, durations, last = [], [], None
    for i in range(int(END * FPS)):
        img = frame(i / FPS)
        data = img.tobytes()
        if data == last:
            durations[-1] += int(1000 / FPS)
            continue
        frames.append(img.quantize(colors=96, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
        durations.append(int(1000 / FPS))
        last = data
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hero.gif")
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True,
                   disposal=1)
    print(f"{out}: {len(frames)} frames, {os.path.getsize(out) // 1024} KiB")
    frame(14.5).save(os.path.join(os.path.dirname(out), "hero-still.png"))


if __name__ == "__main__":
    main()
