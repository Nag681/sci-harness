"""Draws the README's pictures, each in a light and a dark version: the hero, the slips and how they are caught,
one evening's job as a timeline, how you use it, and the good-to-know tiles.

  python3 .github/readme/build.py

The visual idea is the project's name: a climbing harness. Every finished step is an anchor on the rope, so a
slip costs one step, never the whole climb. Pictures use system fonts only, because GitHub shows SVG files as
images, which cannot load web fonts.
"""
from pathlib import Path
from xml.sax.saxutils import escape

SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif"

# Granite neutrals with a slight blue bias; one bold accent, the rope. Green and red only mean pass and fail.
THEMES = {
    "light": dict(panel="#f4f6f8", card="#f6f8fa", line="#d8dee4", ink="#17212b", muted="#57626e", faint="#8b96a1",
                  rope="#e8590c", ropesoft="#fdeadb", ok="#1a7f37", oksoft="#dcf2e2", bad="#b42318",
                  badsoft="#fbe4e1", grey="#eaeef2", rock="#dfe5ea", rock2="#cdd5dd", rock3="#bcc6cf",
                  sky="#ffffff"),
    "dark": dict(panel="#11171e", card="#151b23", line="#2a333d", ink="#e6edf3", muted="#9ba6b1", faint="#6c7885",
                 rope="#ff8a3d", ropesoft="#3b2415", ok="#3fb950", oksoft="#14301d", bad="#f47067",
                 badsoft="#3a1d1c", grey="#1f2730", rock="#1c242d", rock2="#25303b", rock3="#2f3b47",
                 sky="#0d1117"),
}

# 24x24 line icons.
ICONS = {
    "forget": '<path d="M20 11.5a8 8 0 0 1-11.6 7.1L4 19.8l1.1-4.2A8 8 0 1 1 20 11.5z"/>'
              '<path d="M9.8 9.2a2.3 2.3 0 1 1 3.3 2.1c-.7.4-1.1.9-1.1 1.7M12 15.6h.01"/>',
    "lowered": '<path d="M5 3v18M19 3v18M5 16h14"/><path d="M12 5v6.5M9.5 9 12 11.5 14.5 9"/>',
    "circles": '<path d="M19.5 12a7.5 7.5 0 0 1-13.2 4.9M4.5 12a7.5 7.5 0 0 1 13.2-4.9"/>'
               '<path d="M18 3.5v4h-4M6 20.5v-4h4"/>',
    "broken": '<path d="M11 4H6.5A2.5 2.5 0 0 0 4 6.5v11A2.5 2.5 0 0 0 6.5 20H10M14 4h3.5A2.5 2.5 0 0 1 20 6.5v11'
              'a2.5 2.5 0 0 1-2.5 2.5H13"/><path d="M12.5 4 10.5 9l3 3-2.5 4 1.5 4"/>',
    "plan": '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V2.8h6V4"/>'
            '<path d="M8.5 10l1.3 1.3 2.2-2.3M14 10.5h2M8.5 15.5l1.3 1.3 2.2-2.3M14 16h2"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4M12 15v2"/>',
    "cause": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m20 20-4.8-4.8"/><path d="M10.5 7.5V11M10.5 13.6h.01"/>',
    "shield": '<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3z"/><path d="M9 12l2 2 4-4"/>',
    "talk": '<path d="M4 5h16v11H9l-5 4z"/><path d="M8 9.5h8M8 12.5h5"/>',
    "steps": '<path d="M4 20h4v-4h4v-4h4V8h4"/><circle cx="20" cy="5" r="1.6"/>',
    "report": '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4M10 12h5M10 15.5h5"/>',
    "home": '<path d="M4 11.5 12 4l8 7.5"/><path d="M6 10v10h12V10"/><path d="M10 20v-5h4v5"/>',
    "gift": '<rect x="3.5" y="8" width="17" height="4.5" rx="1"/><path d="M5.5 12.5V20h13v-7.5M12 8v12"/>'
            '<path d="M12 8C10.5 4.5 7 4 7 6.2S10 8 12 8zM12 8c1.5-3.5 5-4 5-1.8S14 8 12 8z"/>',
    "history": '<path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4v4h4"/><path d="M12 8v4.5l3 2"/>',
    "bolt": '<path d="M13 3 5 14h6l-1 7 8-11h-6z"/>',
    "plug": '<path d="M9 3v5M15 3v5"/><path d="M6 8h12v3a6 6 0 0 1-12 0z"/><path d="M12 17v4"/>',
}

SLIPS = [
    ("forget", "It forgets the plan.", ["Long chats get trimmed,", "and the details go with them."]),
    ("lowered", "It fakes the fix.", ["When a check fails, it quietly", "makes the check easier."]),
    ("circles", "It goes in circles.", ["The same broken fix,", "again and again."]),
    ("broken", "It breaks what worked.", ["A new change quietly", "undoes an old one."]),
]
CATCHES = [
    ("plan", "The plan is written down.", ["After any break, your agent picks", "up exactly where it left off."]),
    ("lock", "The checks are locked.", ["Making a check easier", "counts as a failure."]),
    ("cause", "Three misses, then it stops.", ["It has to find the cause", "before it tries again."]),
    ("shield", "Every step is re-checked.", ["Old features are tested again", "before anything is saved."]),
]
STEPS = [
    ("talk", "Describe the job", ["One sentence to your", "agent is all it takes."]),
    ("steps", "Your agent builds", ["Step by step, each one", "checked and saved."]),
    ("report", "You get the result", ["Finished work and a", "short report to read."]),
]
# One evening's job, as the report tells it. Each of the four slips above happens once and is caught.
STORY = [
    ("start", "6:00 PM", "The plan is written", ["Four steps, each with a check", "that proves it works."]),
    ("save", "6:40 PM", "Step 1 is saved", ["Items go into the cart.", "Checked, then saved."]),
    ("slip", "7:15 PM", "A fake fix is caught", ["A check was made easier.", "It is put back as it was."]),
    ("slip", "8:05 PM", "Three misses, then a stop", ["It stops guessing and tests", "its ideas to find the cause."]),
    ("save", "8:30 PM", "Step 2 is saved", ["Prices and totals add up.", "Checked, then saved."]),
    ("slip", "9:10 PM", "The chat is trimmed", ["It reads its notes and picks", "up exactly where it left off."]),
    ("slip", "10:20 PM", "Step 3 breaks step 1", ["Caught before it is saved,", "then fixed."]),
    ("done", "11:40 PM", "Done, with a report", ["4 steps saved, 4 slips caught", "and 1 question for you."]),
]
FACTS = [
    ("talk", "No coding needed", ["You describe the job.", "Your agent does the rest."]),
    ("home", "Stays on your computer", ["Your code is never", "sent anywhere."]),
    ("gift", "Free and open source", ["No account, no sign-up.", "MIT license."]),
    ("history", "Fine for messy projects", ["Old problems are noted first,", "so only new ones count."]),
    ("bolt", "Barely slower", ["It re-checks only", "what changed."]),
    ("plug", "Works with your agent", ["Claude Code, Codex, Gemini CLI,", "Antigravity, OpenCode and pi."]),
]


def svg(w, h, label, body):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img" '
            'aria-label="%s">\n%s\n</svg>\n' % (w, h, w, h, escape(label, {'"': "&quot;"}), "\n".join(body)))


def text(x, y, s, size, fill, weight=400, anchor="start", spacing=None):
    extra = ' letter-spacing="%s"' % spacing if spacing is not None else ""
    return ('<text x="%s" y="%s" font-family="%s" font-size="%s" font-weight="%s" fill="%s" text-anchor="%s"%s>%s'
            '</text>' % (x, y, SANS, size, weight, fill, anchor, extra, escape(s)))


def icon(name, x, y, size, color, width=1.8):
    k = size / 24.0
    return ('<g transform="translate(%s %s) scale(%.3f)" fill="none" stroke="%s" stroke-width="%s" '
            'stroke-linecap="round" stroke-linejoin="round">%s</g>' % (x, y, k, color, width, ICONS[name]))


def badge(cx, cy, good, t):
    fill = t["ok"] if good else t["bad"]
    mark = "M-4 0.5l2.8 2.8L4 -3" if good else "M-3.2 -3.2l6.4 6.4M3.2 -3.2l-6.4 6.4"
    return ('<g transform="translate(%s %s)"><circle r="12" fill="%s" stroke="%s" stroke-width="3"/>'
            '<path d="%s" fill="none" stroke="#ffffff" stroke-width="2.4" stroke-linecap="round" '
            'stroke-linejoin="round"/></g>' % (cx, cy, fill, t["card"], mark))


def cards(items, good, t, label):
    """A 2x2 grid of cards: an icon tile with a pass or fail badge, then a headline and two short lines.

    The icon sits above the words so the grid stays narrow: on a phone the picture shrinks to the screen, and a
    narrower picture keeps its words larger.
    """
    w, h, gap = 450, 246, 24
    out = []
    for i, (ico, head, lines) in enumerate(items):
        x, y = (i % 2) * (w + gap), (i // 2) * (h + gap)
        tile = t["ropesoft"] if good else t["grey"]
        out.append('<rect x="%d" y="%d" width="%d" height="%d" rx="20" fill="%s" stroke="%s" stroke-width="1.5"/>'
                   % (x + 1, y + 1, w - 2, h - 2, t["card"], t["line"]))
        out.append('<rect x="%d" y="%d" width="64" height="64" rx="16" fill="%s"/>' % (x + 28, y + 28, tile))
        out.append(icon(ico, x + 40, y + 40, 40, t["rope"] if good else t["muted"]))
        out.append(badge(x + 90, y + 32, good, t))
        out.append(text(x + 28, y + 136, head, 28, t["ink"], 700))
        for k, line in enumerate(lines):
            out.append(text(x + 28, y + 176 + 31 * k, line, 22, t["muted"]))
    return svg(2 * w + gap, 2 * h + gap, label, out)


def steps(t):
    """Three steps in a row, joined by the rope, with an anchor at each step. As wide as the card grids."""
    w, h, gap = 292, 236, 24
    out = []
    for i, (ico, head, lines) in enumerate(STEPS):
        x = i * (w + gap)
        out.append('<rect x="%d" y="1" width="%d" height="%d" rx="20" fill="%s" stroke="%s" stroke-width="1.5"/>'
                   % (x + 1, w - 2, h - 2, t["card"], t["line"]))
        out.append('<rect x="%d" y="28" width="64" height="64" rx="16" fill="%s"/>' % (x + 26, t["ropesoft"]))
        out.append(icon(ico, x + 38, 40, 40, t["rope"]))
        out.append(text(x + w - 28, 76, str(i + 1), 44, t["line"], 800, "end"))
        out.append(text(x + 26, 138, head, 26, t["ink"], 700))
        for k, line in enumerate(lines):
            out.append(text(x + 26, 176 + 29 * k, line, 21, t["muted"]))
        if i < len(STEPS) - 1:
            ax = x + w + gap / 2.0
            out.append('<path d="M%d 60H%d" stroke="%s" stroke-width="3" stroke-linecap="round"/>'
                       % (x + w - 6, x + w + gap + 6, t["rope"]))
            out.append('<circle cx="%s" cy="60" r="8" fill="%s" stroke="%s" stroke-width="3"/>'
                       % (ax, t["sky"], t["rope"]))
    return svg(3 * w + 2 * gap, h, "Three steps: describe the job in one sentence; your agent builds it step by "
               "step, each one checked and saved; you get the finished work and a short report.", out)


def robot(x, y, t, ghost=False):
    """The agent as a small robot head centered on (x, y)."""
    stroke, fill = (t["faint"], "none") if ghost else (t["ink"], t["sky"])
    dash = ' stroke-dasharray="3 4"' if ghost else ""
    eyes = "" if ghost else ('<circle cx="%d" cy="%d" r="3" fill="%s"/><circle cx="%d" cy="%d" r="3" fill="%s"/>'
                             % (x - 7, y + 1, t["ink"], x + 7, y + 1, t["ink"]))
    return ('<rect x="%d" y="%d" width="40" height="32" rx="10" fill="%s" stroke="%s" stroke-width="2.6"%s/>'
            '<path d="M%d %dv-7" stroke="%s" stroke-width="2.6" stroke-linecap="round"%s/>%s'
            % (x - 20, y - 15, fill, stroke, dash, x, y - 15, stroke, dash, eyes))


def hero(t):
    """The name, the promise, and the picture: a rope up a mountain, every anchor a checked and saved step."""
    W, H = 1200, 470
    out = ['<defs><clipPath id="frame"><rect x="1" y="1" width="%d" height="%d" rx="28"/></clipPath></defs>'
           % (W - 2, H - 2),
           '<rect x="1" y="1" width="%d" height="%d" rx="28" fill="%s" stroke="%s" stroke-width="2"/>'
           % (W - 2, H - 2, t["panel"], t["line"]),
           '<g clip-path="url(#frame)">']
    # The mountain: three layers of granite.
    out.append('<path d="M560 470 L700 330 L760 365 L860 230 L905 262 L985 120 L1040 175 L1085 150 L1200 260 '
               'L1200 470 Z" fill="%s"/>' % t["rock"])
    out.append('<path d="M640 470 L780 360 L830 380 L935 250 L985 120 L1010 200 L1090 300 L1200 340 L1200 470 Z" '
               'fill="%s"/>' % t["rock2"])
    out.append('<path d="M760 470 L880 400 L960 420 L1060 350 L1200 410 L1200 470 Z" fill="%s"/>' % t["rock3"])
    out.append("</g>")
    # The route: the rope runs through the anchors to the summit.
    anchors = [(700, 440), (790, 385), (865, 330), (925, 268), (955, 205), (985, 150)]
    d = "M%d %d" % anchors[0]
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        d += " Q%d %d %d %d" % ((x0 + x1) / 2.0 + (18 if y1 % 2 else -18), (y0 + y1) / 2.0, x1, y1)
    done = 4  # anchors passed so far
    out.append('<path d="%s" fill="none" stroke="%s" stroke-width="4" stroke-linecap="round" '
               'stroke-dasharray="1 0"/>' % (d, t["rope"]))
    # The flag at the summit.
    sx, sy = anchors[-1]
    out.append('<path d="M%d %dV%d" stroke="%s" stroke-width="3" stroke-linecap="round"/>' % (sx, sy - 14, sy - 76,
                                                                                            t["ink"]))
    out.append('<path d="M%d %d l46 14 -46 14z" fill="%s"/>' % (sx + 1, sy - 76, t["ok"]))
    out.append(text(sx + 58, sy - 52, "Done", 20, t["ink"], 700))
    for i, (x, y) in enumerate(anchors[:-1]):
        if i < done:
            out.append('<circle cx="%d" cy="%d" r="14" fill="%s" stroke="%s" stroke-width="3.5"/>' % (x, y, t["ok"],
                                                                                                    t["panel"]))
            out.append('<path d="M%d %d l4 4 7-8" fill="none" stroke="#ffffff" stroke-width="2.6" '
                       'stroke-linecap="round" stroke-linejoin="round"/>' % (x - 5.5, y + 0.5))
        else:
            out.append('<circle cx="%d" cy="%d" r="13" fill="%s" stroke="%s" stroke-width="4"/>' % (x, y, t["sky"],
                                                                                                  t["rope"]))
    # The climber, the agent: it slipped from just under the next anchor and hangs from the last one it passed.
    ax, ay = anchors[3]
    gx, gy = anchors[4][0] + 40, anchors[4][1] + 6  # where it slipped from
    cx, cy = ax + 28, ay + 60  # where the rope caught it
    out.append(robot(gx, gy, t, ghost=True))
    out.append('<path d="M%d %d Q%d %d %d %d" fill="none" stroke="%s" stroke-width="2.2" stroke-dasharray="3 6" '
               'stroke-linecap="round"/>' % (gx + 22, gy + 4, gx + 34, (gy + cy) / 2, cx + 24, cy - 6, t["faint"]))
    out.append('<path d="M%d %d L%d %d" stroke="%s" stroke-width="3" stroke-linecap="round"/>'
               % (ax, ay, cx, cy - 22, t["rope"]))
    out.append(robot(cx, cy, t))
    # Two notes on the picture.
    out.append(text(cx + 36, cy - 2, "A slip is caught", 18, t["ink"], 600))
    out.append(text(cx + 36, cy + 22, "at the last saved step.", 18, t["muted"]))
    lx, ly = anchors[1]
    out.append(text(lx + 30, ly + 26, "Every step checked", 18, t["ink"], 600))
    out.append(text(lx + 30, ly + 50, "and saved.", 18, t["muted"]))
    # The words.
    out.append(text(72, 132, "FOR CODING AGENTS", 15, t["rope"], 700, spacing=3))
    out.append(text(68, 214, "Sci-Harness", 78, t["ink"], 800, spacing=-2))
    out.append(text(72, 282, "Give your agent a big coding job.", 30, t["ink"], 500))
    out.append(text(72, 324, "Get it done right.", 30, t["ink"], 500))
    out.append('<path d="M74 350 H330" stroke="%s" stroke-width="4" stroke-linecap="round"/>' % t["rope"])
    out.append(text(72, 400, "Free · works offline · Claude Code, Codex, Gemini and more", 18, t["muted"]))
    return svg(W, H, "Sci-Harness, for coding agents. Give your agent a big coding job. Get it done right. "
               "A rope runs up a mountain through anchors; every step is checked and saved, and a slip is caught at "
               "the last saved step.", out)


def marker(kind, x, y, t):
    """A stop on the timeline: a ring for the start, a check for a saved step, a flag-green check at the end, and
    an exclamation mark for a slip that was caught."""
    if kind == "start":
        return '<circle cx="%d" cy="%d" r="15" fill="%s" stroke="%s" stroke-width="5"/>' % (x, y, t["panel"],
                                                                                          t["rope"])
    fill = {"save": t["ok"], "done": t["ok"], "slip": t["rope"]}[kind]
    ring = '<circle cx="%d" cy="%d" r="19" fill="%s" stroke="%s" stroke-width="5"/>' % (x, y, fill, t["panel"])
    if kind == "slip":
        return ring + ('<path d="M%d %dv9" stroke="#ffffff" stroke-width="3.4" stroke-linecap="round"/>'
                       '<circle cx="%d" cy="%d" r="2.1" fill="#ffffff"/>' % (x, y - 8, x, y + 7))
    return ring + ('<path d="M%d %d l5 5 9-10" fill="none" stroke="#ffffff" stroke-width="3.2" '
                   'stroke-linecap="round" stroke-linejoin="round"/>' % (x - 7, y + 0.5))


def story(t):
    """One evening's job as two columns of a timeline. The rope runs down the first column and on into the second."""
    W, H, pad, col = 1040, 668, 40, 480
    gapy, top = 152, 92
    out = ['<defs><clipPath id="panel"><rect x="1" y="1" width="%d" height="%d" rx="28"/></clipPath></defs>'
           % (W - 2, H - 2),
           '<rect x="1" y="1" width="%d" height="%d" rx="28" fill="%s" stroke="%s" stroke-width="2"/>'
           % (W - 2, H - 2, t["panel"], t["line"]),
           '<g clip-path="url(#panel)">']
    for c in range(2):
        rx = pad + c * col + 24
        y0 = top if c == 0 else 0
        y1 = H if c == 0 else top + 3 * gapy
        out.append('<path d="M%d %dV%d" stroke="%s" stroke-width="4" stroke-linecap="round"/>'
                   % (rx, y0, y1, t["rope"]))
    out.append("</g>")
    for i, (kind, when, head, lines) in enumerate(STORY):
        c, r = divmod(i, 4)
        rx, y = pad + c * col + 24, top + r * gapy
        tx = rx + 48
        out.append(marker(kind, rx, y, t))
        out.append(text(tx, y - 30, when, 16, t["rope"] if kind == "slip" else t["faint"], 700, spacing=1.5))
        out.append(text(tx, y + 9, head, 28, t["ink"], 700))
        for k, line in enumerate(lines):
            out.append(text(tx, y + 44 + 30 * k, line, 22, t["muted"]))
    label = "One evening's job, start to finish. " + " ".join(
        "%s: %s. %s" % (when, head, " ".join(lines)) for _, when, head, lines in STORY)
    return svg(W, H, label, out)


def tiles(items, t, label):
    """A 2x3 grid of tiles: an icon tile, a headline and two short lines."""
    w, h, gap = 450, 150, 24
    out = []
    for i, (ico, head, lines) in enumerate(items):
        x, y = (i % 2) * (w + gap), (i // 2) * (h + gap)
        out.append('<rect x="%d" y="%d" width="%d" height="%d" rx="20" fill="%s" stroke="%s" stroke-width="1.5"/>'
                   % (x + 1, y + 1, w - 2, h - 2, t["card"], t["line"]))
        out.append('<rect x="%d" y="%d" width="56" height="56" rx="14" fill="%s"/>' % (x + 26, y + 30, t["ropesoft"]))
        out.append(icon(ico, x + 37, y + 41, 34, t["rope"]))
        out.append(text(x + 104, y + 58, head, 25, t["ink"], 700))
        for k, line in enumerate(lines):
            out.append(text(x + 104, y + 92 + 28 * k, line, 20, t["muted"]))
    return svg(2 * w + gap, 3 * h + 2 * gap, label, out)


def draw(out_dir):
    for name, t in THEMES.items():
        files = {
            "hero": hero(t),
            "slips": cards(SLIPS, False, t, "Four ways agents slip on big jobs: it forgets the plan; it fakes the fix "
                           "by making a failing check easier; it goes in circles with the same broken fix; it breaks "
                           "what worked."),
            "catches": cards(CATCHES, True, t, "How Sci-Harness catches each slip: the plan is written down; the "
                             "checks are locked; after three misses it stops to find the cause; every step is "
                             "re-checked before anything is saved."),
            "story": story(t),
            "steps": steps(t),
            "facts": tiles(FACTS, t, "Good to know. " + " ".join(
                "%s: %s" % (head, " ".join(lines)) for _, head, lines in FACTS)),
        }
        for base, content in files.items():
            (out_dir / ("%s-%s.svg" % (base, name))).write_text(content, encoding="utf-8")


if __name__ == "__main__":
    draw(Path(__file__).resolve().parent)
