"""Draws the README's images and checks its quoted output.

Without arguments, it draws the banner in a light and a dark version. Given the transcript of demo.sh, it also
draws the terminal picture of `resume`, and it fails if a line in one of the README's console blocks is not in
the transcript, so every quote stays real output.

  sh .github/readme/demo.sh > demo.txt && python3 .github/readme/build.py demo.txt
"""
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace"
NO_LIGATURES = "font-variant-ligatures:none;font-feature-settings:'liga' 0,'calt' 0"  # Fira Code draws === as one glyph

THEMES = {
    "light": dict(top="#ffffff", bottom="#f6f8fa", border="#d0d7de", fg="#1f2328", muted="#57606a",
                  line="#afb8c1", accent="#0969da", wordmark=("#0969da", "#8250df"), tint=0.09,
                  steps=["#57606a", "#cf222e", "#9a6700", "#0969da", "#1a7f37", "#8250df"]),
    "dark": dict(top="#0d1117", bottom="#161b22", border="#30363d", fg="#e6edf3", muted="#8b949e",
                 line="#484f58", accent="#58a6ff", wordmark=("#58a6ff", "#a371f7"), tint=0.16,
                 steps=["#8b949e", "#f85149", "#d29922", "#58a6ff", "#3fb950", "#a371f7"]),
}

# 24x24 line icons, drawn for this banner.
ICONS = {
    "plan": '<path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01"/>',
    "test": '<path d="M9 3h6M10 3v6.2L4.7 18.4A1.75 1.75 0 0 0 6.2 21h11.6a1.75 1.75 0 0 0 1.5-2.6L14 9.2V3'
            'M6.7 15h10.6"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4M12 15v2"/>',
    "fix": '<path d="M17 3l4 4L8 20H4v-4L17 3zM14 6l4 4"/>',
    "verify": '<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3zM9 12l2 2 4-4"/>',
    "commit": '<circle cx="12" cy="12" r="3.2"/><path d="M3 12h5.8M15.2 12H21"/>',
}
STEPS = [
    ("plan", "Plan", "small tasks,", "each with a test"),
    ("test", "Test first", "it must fail", "before the work"),
    ("lock", "Lock", "the test can't", "be made easier"),
    ("fix", "Fix", "3 misses in a row", "and it says STOP"),
    ("verify", "Verify", "what passed before", "still passes"),
    ("commit", "Commit", "one commit", "per task"),
]


def banner(t):
    w, h, y = 1200, 440, 304
    xs = [150 + 180 * i for i in range(len(STEPS))]
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img" '
           'aria-label="Sci-Harness: plan, test first, lock, fix, verify, commit, then the next task">' % (w, h, w, h),
           '<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="%s"/>'
           '<stop offset="1" stop-color="%s"/></linearGradient>' % (t["top"], t["bottom"]),
           '<linearGradient id="mark" gradientUnits="userSpaceOnUse" x1="350" y1="0" x2="480" y2="0">'
           '<stop offset="0" stop-color="%s"/><stop offset="1" stop-color="%s"/></linearGradient>' % t["wordmark"],
           '<marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" '
           'orient="auto-start-reverse"><path d="M1 1l8 4-8 4" fill="none" stroke="%s" stroke-width="1.8" '
           'stroke-linecap="round" stroke-linejoin="round"/></marker>' % t["line"],
           '<marker id="loop" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" '
           'orient="auto-start-reverse"><path d="M1 1l8 4-8 4" fill="none" stroke="%s" stroke-width="1.8" '
           'stroke-linecap="round" stroke-linejoin="round"/></marker></defs>' % t["accent"],
           '<rect x="1" y="1" width="%d" height="%d" rx="24" fill="url(#bg)" stroke="%s" stroke-width="2"/>'
           % (w - 2, h - 2, t["border"]),
           '<text x="600" y="78" text-anchor="middle" font-family="%s" font-size="15" font-weight="600" '
           'letter-spacing="3" fill="%s">AN AGENT SKILL FOR LONG CODING TASKS</text>' % (SANS, t["accent"]),
           '<text x="600" y="150" text-anchor="middle" font-family="%s" font-size="76" font-weight="800" '
           'letter-spacing="-1.5" fill="%s"><tspan fill="url(#mark)">Sci</tspan>-Harness</text>' % (SANS, t["fg"]),
           '<text x="600" y="196" text-anchor="middle" font-family="%s" font-size="21" fill="%s">It remembers the '
           'plan, runs the tests and refuses shortcuts.</text>' % (SANS, t["muted"])]
    # The way back from Commit to Test: every task repeats the loop.
    out.append('<path d="M%d %d C%d 212 %d 212 %d %d" fill="none" stroke="%s" stroke-width="2" '
               'stroke-dasharray="6 6" marker-end="url(#loop)"/>' % (xs[5], y - 33, xs[5], xs[1], xs[1], y - 34,
                                                                     t["accent"]))
    mid = (xs[1] + xs[5]) // 2
    out.append('<rect x="%d" y="214" width="112" height="28" rx="14" fill="%s" stroke="%s" stroke-width="1.5"/>'
               % (mid - 56, t["top"], t["accent"]))
    out.append('<text x="%d" y="233" text-anchor="middle" font-family="%s" font-size="14" font-weight="600" '
               'fill="%s">next task</text>' % (mid, SANS, t["accent"]))
    for a, b in zip(xs, xs[1:]):
        out.append('<path d="M%d %dH%d" stroke="%s" stroke-width="2" marker-end="url(#arrow)"/>'
                   % (a + 38, y, b - 40, t["line"]))
    for x, color, (icon, label, cap1, cap2) in zip(xs, t["steps"], STEPS):
        out.append('<circle cx="%d" cy="%d" r="31" fill="%s" fill-opacity="%s" stroke="%s" stroke-width="2"/>'
                   % (x, y, color, t["tint"], color))
        out.append('<g transform="translate(%d %d) scale(1.25)" fill="none" stroke="%s" stroke-width="2" '
                   'stroke-linecap="round" stroke-linejoin="round">%s</g>' % (x - 15, y - 15, color, ICONS[icon]))
        out.append('<text x="%d" y="%d" text-anchor="middle" font-family="%s" font-size="20" font-weight="700" '
                   'fill="%s">%s</text>' % (x, y + 64, SANS, t["fg"], label))
        for i, cap in enumerate((cap1, cap2)):
            out.append('<text x="%d" y="%d" text-anchor="middle" font-family="%s" font-size="16" fill="%s">%s'
                       '</text>' % (x, y + 89 + 20 * i, SANS, t["muted"], escape(cap)))
    out.append("</svg>")
    return "\n".join(out) + "\n"


# Terminal colors (GitHub's dark palette), applied in order: a later rule paints over an earlier one.
TERM = dict(bg="#0d1117", bar="#161b22", border="#30363d", fg="#e6edf3", muted="#8b949e", head="#d2a8ff",
            accent="#79c0ff", red="#ff7b72", green="#3fb950", amber="#e3b341")
RULES = [
    (r"^=== .* ===$", "muted"),
    (r"^ {2}(verify|scope |invariants|locked|last check|failing):", "muted"),
    (r"^ {2}N\d+ \[\w+\]", "muted"),
    (r"^(OBJECTIVE|PROGRESS|ACTIVE T\d+|NOTES \([^)]*\)|GOLDENS|BASELINE):", "head"),
    (r"\[ \]", "muted"),
    (r"\bFAIL\b", "red"),
    (r"\bOK\b|\bPASS\b", "green"),
    (r"\[>\]|<- ACTIVE|[Ss]trikes? \d/3", "amber"),
    (r"^NEXT:", "accent"),
    (r"^\$", "muted"),
]


def spans(line):
    paint = ["fg"] * len(line)
    for pat, color in RULES:
        for m in re.finditer(pat, line):
            paint[m.start():m.end()] = [color] * (m.end() - m.start())
    i = 0
    while i < len(line):
        j = i
        while j < len(line) and paint[j] == paint[i]:
            j += 1
        yield i, line[i:j], paint[i]
        i = j


def terminal(lines, title):
    cw, lh, pad, top = 9.0, 22, 24, 62
    w = int(max(880, pad * 2 + 40 + cw * max(len(s) for s in lines)))  # 40: room for fonts wider than 0.6em
    h = top + lh * len(lines) + 16
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img" '
           'aria-label="Terminal output of %s">' % (w, h, w, h, escape(lines[0].lstrip("$ "))),
           '<rect x="1" y="1" width="%d" height="%d" rx="12" fill="%s" stroke="%s" stroke-width="2"/>'
           % (w - 2, h - 2, TERM["bg"], TERM["border"]),
           '<path d="M2 38V13A11 11 0 0 1 13 2H%d A11 11 0 0 1 %d 13V38z" fill="%s"/>' % (w - 13, w - 2, TERM["bar"]),
           '<path d="M2 38H%d" stroke="%s" stroke-width="1"/>' % (w - 2, TERM["border"])]
    for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        out.append('<circle cx="%d" cy="20" r="6" fill="%s"/>' % (24 + 20 * i, c))
    out.append('<text x="%d" y="25" text-anchor="middle" font-family="%s" font-size="13" fill="%s">%s</text>'
               % (w // 2, SANS, TERM["muted"], escape(title)))
    for n, line in enumerate(lines):
        y = top + lh * n + 14
        parts = []
        for col, text, color in spans(line):
            weight = ' font-weight="700"' if color in ("head", "accent") or (n == 0 and col > 0) else ""
            parts.append('<tspan x="%.1f" fill="%s"%s>%s</tspan>' % (pad + cw * col, TERM[color], weight,
                                                                      escape(text)))
        out.append('<text y="%d" font-family="%s" font-size="15" xml:space="preserve" style="%s">%s</text>'
                   % (y, MONO, NO_LIGATURES, "".join(parts)))
    out.append("</svg>")
    return "\n".join(out) + "\n"


def block(transcript, command):
    """The first `$ command` in the demo transcript and its output, up to the blank line after it."""
    lines = transcript.splitlines()
    start = lines.index("$ " + command)
    end = lines.index("", start)
    return lines[start:end]


def unquoted(readme, transcript):
    """Lines of the README's console blocks that the demo did not print (`...` marks a cut)."""
    printed = set(transcript.splitlines())
    missing, inside = [], False
    for line in readme.splitlines():
        if line.startswith("```"):
            inside = line == "```console"
        elif inside and line.strip() not in ("", "...") and line not in printed:
            missing.append(line)
    return missing


def main(argv):
    for name, theme in THEMES.items():
        (HERE / ("banner-%s.svg" % name)).write_text(banner(theme), encoding="utf-8")
    if len(argv) < 2:
        return 0
    transcript = Path(argv[1]).read_text(encoding="utf-8")
    (HERE / "resume.svg").write_text(terminal(block(transcript, ".sci/sci resume"), "~/bills"), encoding="utf-8")
    missing = unquoted((HERE.parent.parent / "README.md").read_text(encoding="utf-8"), transcript)
    for line in missing:
        print("README quotes a line the demo did not print: " + line)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
