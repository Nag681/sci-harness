"""Draws the README's banner in a light and a dark version.

  python3 .github/readme/build.py

The picture is the project's name: a climbing harness. The rope runs up a mountain through anchors, each a
checked and saved step, and the agent, a small robot, hangs on the rope where its slip was caught. It uses
system fonts only, because GitHub shows SVG files as images, which cannot load web fonts.
"""
from pathlib import Path
from xml.sax.saxutils import escape

SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif"

# Granite neutrals with a slight blue bias and one bold accent, the rope. Green means checked and saved.
THEMES = {
    "light": dict(panel="#f4f6f8", line="#d8dee4", ink="#17212b", faint="#8b96a1", rope="#e8590c", ok="#1a7f37",
                  rock="#dfe5ea", rock2="#cdd5dd", rock3="#bcc6cf", sky="#ffffff"),
    "dark": dict(panel="#11171e", line="#2a333d", ink="#e6edf3", faint="#6c7885", rope="#ff8a3d", ok="#3fb950",
                 rock="#1c242d", rock2="#25303b", rock3="#2f3b47", sky="#0d1117"),
}
W, H = 1200, 300
ART = 0.62  # the mountain is drawn on a 1200x470 grid and scaled into the right of the banner
LABEL = ("Sci-Harness, for coding agents. Give your agent a big coding job. Get it done right. A rope runs up a "
         "mountain through anchors, each a checked and saved step, and a robot hangs on the rope where its slip "
         "was caught.")


def text(x, y, s, size, fill, weight=400, spacing=None):
    extra = ' letter-spacing="%s"' % spacing if spacing is not None else ""
    return ('<text x="%s" y="%s" font-family="%s" font-size="%s" font-weight="%s" fill="%s"%s>%s</text>'
            % (x, y, SANS, size, weight, fill, extra, escape(s)))


def robot(x, y, t, ghost=False):
    """The agent as a small robot head centered on (x, y)."""
    stroke, fill = (t["faint"], "none") if ghost else (t["ink"], t["sky"])
    dash = ' stroke-dasharray="3 4"' if ghost else ""
    eyes = "" if ghost else ('<circle cx="%d" cy="%d" r="3.4" fill="%s"/><circle cx="%d" cy="%d" r="3.4" fill="%s"/>'
                             % (x - 8, y + 1, t["ink"], x + 8, y + 1, t["ink"]))
    return ('<rect x="%d" y="%d" width="46" height="36" rx="11" fill="%s" stroke="%s" stroke-width="3"%s/>'
            '<path d="M%d %dv-8" stroke="%s" stroke-width="3" stroke-linecap="round"%s/>%s'
            % (x - 23, y - 17, fill, stroke, dash, x, y - 17, stroke, dash, eyes))


def mountain(t):
    """The mountain, the rope, the anchors and the agent, on a 1200x470 grid."""
    out = ['<path d="M560 470 L700 330 L760 365 L860 230 L905 262 L985 120 L1040 175 L1085 150 L1200 260 '
           'L1200 470 Z" fill="%s"/>' % t["rock"],
           '<path d="M640 470 L780 360 L830 380 L935 250 L985 120 L1010 200 L1090 300 L1200 340 L1200 470 Z" '
           'fill="%s"/>' % t["rock2"],
           '<path d="M760 470 L880 400 L960 420 L1060 350 L1200 410 L1200 470 Z" fill="%s"/>' % t["rock3"]]
    anchors = [(700, 440), (790, 385), (865, 330), (925, 268), (955, 205), (985, 150)]
    d = "M%d %d" % anchors[0]
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        d += " Q%d %d %d %d" % ((x0 + x1) / 2.0 + (18 if y1 % 2 else -18), (y0 + y1) / 2.0, x1, y1)
    out.append('<path d="%s" fill="none" stroke="%s" stroke-width="6" stroke-linecap="round"/>' % (d, t["rope"]))
    sx, sy = anchors[-1]
    out.append('<path d="M%d %dV%d" stroke="%s" stroke-width="4" stroke-linecap="round"/>'
               % (sx, sy - 14, sy - 80, t["ink"]))
    out.append('<path d="M%d %d l54 16 -54 16z" fill="%s"/>' % (sx + 2, sy - 80, t["ok"]))
    for i, (x, y) in enumerate(anchors[:-1]):
        if i < 4:  # passed: checked and saved
            out.append('<circle cx="%d" cy="%d" r="17" fill="%s" stroke="%s" stroke-width="4"/>'
                       % (x, y, t["ok"], t["panel"]))
            out.append('<path d="M%s %s l5 5 9-10" fill="none" stroke="#ffffff" stroke-width="3.4" '
                       'stroke-linecap="round" stroke-linejoin="round"/>' % (x - 7, y + 0.5))
        else:
            out.append('<circle cx="%d" cy="%d" r="15" fill="%s" stroke="%s" stroke-width="5"/>'
                       % (x, y, t["sky"], t["rope"]))
    # The agent slipped from just under the next anchor and hangs from the last one it passed.
    ax, ay = anchors[3]
    gx, gy = anchors[4][0] + 44, anchors[4][1] + 8
    cx, cy = ax + 30, ay + 66
    out.append(robot(gx, gy, t, ghost=True))
    out.append('<path d="M%d %d Q%d %d %d %d" fill="none" stroke="%s" stroke-width="2.6" stroke-dasharray="3 7" '
               'stroke-linecap="round"/>' % (gx + 26, gy + 4, gx + 38, (gy + cy) / 2, cx + 28, cy - 6, t["faint"]))
    out.append('<path d="M%d %d L%d %d" stroke="%s" stroke-width="4" stroke-linecap="round"/>'
               % (ax, ay, cx, cy - 25, t["rope"]))
    out.append(robot(cx, cy, t))
    return out


def banner(t):
    out = ['<defs><clipPath id="frame"><rect x="1" y="1" width="%d" height="%d" rx="24"/></clipPath></defs>'
           % (W - 2, H - 2),
           '<rect x="1" y="1" width="%d" height="%d" rx="24" fill="%s" stroke="%s" stroke-width="2"/>'
           % (W - 2, H - 2, t["panel"], t["line"]),
           '<g clip-path="url(#frame)"><g transform="translate(%.1f %.1f) scale(%s)">'
           % (W - 1200 * ART, H - 470 * ART, ART)]
    out += mountain(t)
    out.append("</g></g>")
    out.append(text(64, 82, "FOR CODING AGENTS", 15, t["rope"], 700, spacing=3))
    out.append(text(60, 152, "Sci-Harness", 72, t["ink"], 800, spacing=-2))
    out.append(text(64, 208, "Give your agent a big coding job.", 28, t["ink"], 500))
    out.append(text(64, 246, "Get it done right.", 28, t["ink"], 500))
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img" '
            'aria-label="%s">\n%s\n</svg>\n' % (W, H, W, H, escape(LABEL, {'"': "&quot;"}), "\n".join(out)))


def draw(out_dir):
    for name, t in THEMES.items():
        (out_dir / ("hero-%s.svg" % name)).write_text(banner(t), encoding="utf-8")


if __name__ == "__main__":
    draw(Path(__file__).resolve().parent)
