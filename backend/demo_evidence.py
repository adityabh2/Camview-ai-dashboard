"""
demo_evidence.py — DEMO MODE ONLY: CCTV-style evidence stills.

Each demo alarm gets deterministic, synthetic "camera frames": an overhead
exam-hall view (rows of desks, candidates, invigilator), a detection box for
the alarm type, and the burned-in camera overlay (camera, room, timestamp).
Every frame carries a visible DEMO watermark — it is never real evidence.
"""

import hashlib
import random
from xml.sax.saxutils import escape

W, H = 960, 540

TYPE_LABEL = {
    "1": "MOBILE PHONE", "2": "FACE MISMATCH", "3": "UNAUTHORIZED PERSON", "4": "STRONGROOM MOTION",
    "5": "COMMUNICATION", "6": "RESTRICTED DOOR", "7": "ITEM LEFT BEHIND", "8": "VIEW OBSTRUCTED",
}


def render(name, cam="", room="", ts="", alarm_type="", frame=0):
    seed = int(hashlib.sha1(f"{name}".encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    tone = rng.choice(["#2b3036", "#2e3330", "#312f2c", "#2a2f36"])
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">',
           "<defs><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2' seed='%d'/>"
           "<feColorMatrix values='0 0 0 0 .5  0 0 0 0 .5  0 0 0 0 .5  0 0 0 .09 0'/></filter>"
           "<radialGradient id='v' cx='50%%' cy='50%%' r='75%%'><stop offset='60%%' stop-color='#000' stop-opacity='0'/>"
           "<stop offset='100%%' stop-color='#000' stop-opacity='.55'/></radialGradient></defs>" % (seed % 1000),
           f'<rect width="{W}" height="{H}" fill="{tone}"/>']
    # floor tiles
    for x in range(0, W, 60):
        out.append(f'<line x1="{x}" y1="0" x2="{x - 120}" y2="{H}" stroke="#ffffff" stroke-opacity=".035"/>')
    # rows of desks with candidates (slight perspective)
    desks = []
    rows, cols = 5, 7
    for r in range(rows):
        y = 70 + r * 88
        scale = 0.75 + r * 0.07
        for c in range(cols):
            x = 60 + c * 128 + (r % 2) * 10
            dw, dh = 78 * scale, 34 * scale
            out.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{dw:.0f}" height="{dh:.0f}" rx="3" fill="#6d5a45" '
                       f'fill-opacity=".85" stroke="#000" stroke-opacity=".35"/>')
            if rng.random() < 0.88:   # candidate seated behind the desk
                hx, hy = x + dw / 2 + rng.uniform(-6, 6), y + dh + 12 * scale
                out.append(f'<ellipse cx="{hx:.0f}" cy="{hy + 14 * scale:.0f}" rx="{20 * scale:.0f}" ry="{12 * scale:.0f}" '
                           f'fill="{rng.choice(["#3d4f6b", "#5b4a3a", "#44603f", "#6b3d3d", "#4a4a55"])}"/>')
                out.append(f'<circle cx="{hx:.0f}" cy="{hy:.0f}" r="{10 * scale:.0f}" '
                           f'fill="{rng.choice(["#1c1714", "#2a211b", "#3a2d22"])}"/>')
                out.append(f'<rect x="{x + 18 * scale:.0f}" y="{y + 8 * scale:.0f}" width="{30 * scale:.0f}" '
                           f'height="{16 * scale:.0f}" fill="#e8e4da" fill-opacity=".8"/>')
                desks.append((hx, hy, scale))
    # invigilator walking the aisle
    ix, iy = rng.uniform(120, W - 120), rng.uniform(120, H - 90)
    out.append(f'<ellipse cx="{ix:.0f}" cy="{iy + 16:.0f}" rx="22" ry="14" fill="#1f3b5c"/><circle cx="{ix:.0f}" cy="{iy:.0f}" r="12" fill="#221a15"/>')
    # detection box around one candidate (or area)
    if desks:
        hx, hy, s = desks[(seed + frame * 7) % len(desks)]
        bw, bh = 90 * s, 80 * s
        bx, by = hx - bw / 2 + frame * 4, hy - bh / 2 + frame * 3
        label = TYPE_LABEL.get(str(alarm_type), "ALERT")
        out.append(f'<rect x="{bx:.0f}" y="{by:.0f}" width="{bw:.0f}" height="{bh:.0f}" fill="none" stroke="#ff4d4d" stroke-width="3"/>')
        out.append(f'<rect x="{bx:.0f}" y="{by - 20:.0f}" width="{len(label) * 8 + 44}" height="18" fill="#ff4d4d"/>')
        out.append(f'<text x="{bx + 5:.0f}" y="{by - 6:.0f}" font-family="Consolas,monospace" font-size="12" '
                   f'fill="#fff" font-weight="700">{escape(label)} {0.71 + (seed % 25) / 100:.2f}</text>')
    out.append(f'<rect width="{W}" height="{H}" filter="url(#n)"/><rect width="{W}" height="{H}" fill="url(#v)"/>')
    # CCTV overlay
    t = (ts or "").replace("T", " ").replace("Z", "")[:19]
    out.append(f'<rect x="0" y="0" width="{W}" height="30" fill="#000" fill-opacity=".55"/>')
    out.append(f'<text x="12" y="20" font-family="Consolas,monospace" font-size="15" fill="#f2f2f2">'
               f'{escape(cam or "CAM")}  ·  {escape(room or "")}</text>')
    out.append(f'<text x="{W - 12}" y="20" text-anchor="end" font-family="Consolas,monospace" font-size="15" fill="#f2f2f2">'
               f'{escape(t)} UTC  ·  F{frame + 1}</text>')
    out.append(f'<circle cx="{W - 24}" cy="48" r="7" fill="#ff3b3b"/><text x="{W - 36}" y="53" text-anchor="end" '
               f'font-family="Consolas,monospace" font-size="13" fill="#ff6b6b">REC</text>')
    out.append(f'<text x="{W / 2}" y="{H - 18}" text-anchor="middle" font-family="Arial,sans-serif" font-size="14" '
               f'fill="#ffd24d" fill-opacity=".9" letter-spacing="4">DEMO EVIDENCE — SYNTHETIC FRAME, NOT A REAL RECORDING</text>')
    out.append("</svg>")
    return "".join(out)
