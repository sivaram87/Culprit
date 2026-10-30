"""Draws the README architecture diagram: python3 docs/architecture.py -> architecture-{light,dark}.svg"""
import pathlib
import sys

OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).parent

THEMES = {
    "light": dict(panel="#f6f8fa", pstroke="#d0d7de", card="#ffffff", cstroke="#d0d7de", text="#1f2328",
                  muted="#59636e", accent="#c93434", asoft="#fdecea", arrow="#6e7781", pill="#eef2f7",
                  pillstroke="#afb8c1", badge="#1f2328", badgetext="#ffffff"),
    "dark": dict(panel="#161b22", pstroke="#30363d", card="#0d1117", cstroke="#3d444d", text="#e6edf3",
                 muted="#9198a1", accent="#ff7b72", asoft="#3a1d1b", arrow="#8b949e", pill="#1c2330",
                 pillstroke="#3d444d", badge="#e6edf3", badgetext="#0d1117"),
}
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
W, L, R = 980, 40, 940  # canvas width, panel left/right


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, s, size=13, weight=400, fill="text", anchor="start", mono=False):
    return (f'<text x="{x}" y="{y}" font-family="{MONO if mono else SANS}" font-size="{size}" '
            f'font-weight="{weight}" fill="{{{fill}}}" text-anchor="{anchor}">{esc(s)}</text>')


def card(x, y, w, h, title, lines, mono_title=False, danger=False, num=None):
    fill, stroke = ("asoft", "accent") if danger else ("card", "cstroke")
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{{{fill}}}" stroke="{{{stroke}}}" stroke-width="{2 if danger else 1}"/>']
    tx = x + 16
    if num is not None:
        out.append(f'<circle cx="{x + 24}" cy="{y + 24}" r="11" fill="{{{"accent" if danger else "badge"}}}"/>')
        out.append(text(x + 24, y + 28.5, str(num), 12, 700, "badgetext", "middle"))
        tx = x + 44
    out.append(text(tx, y + 29, title, 14, 700, "accent" if danger else "text", mono=mono_title))
    for i, line in enumerate(lines):
        out.append(text(x + 16, y + 52 + i * 18, line, 12.5, 400, "muted"))
    return out


def arrow(d, color="arrow", dash=False, width=1.6, head="h"):
    return (f'<path d="{d}" fill="none" stroke="{{{color}}}" stroke-width="{width}" '
            f'{"stroke-dasharray=\"6 5\" " if dash else ""}marker-end="url(#{head})"/>')


def build():
    s = []
    # ---- App panel
    ay, ah = 24, 212
    s.append(f'<rect x="{L}" y="{ay}" width="{R - L}" height="{ah}" rx="14" fill="{{panel}}" stroke="{{pstroke}}"/>')
    s.append(text(L + 20, ay + 30, "App", 16, 700))
    s.append(text(L + 58, ay + 30, "iOS / Android · Flags.swift / Flags.kt", 13, 400, "muted"))
    cy = ay + 112  # row centre
    s += card(L + 20, cy - 42, 200, 84, 'Flags.variant(…)', ["the only way code", "reads a watched flag"], mono_title=True)
    # killswitch hexagon
    hx, hw, hh = L + 252, 132, 56
    pts = f"{hx},{cy} {hx + 16},{cy - hh / 2} {hx + hw - 16},{cy - hh / 2} {hx + hw},{cy} {hx + hw - 16},{cy + hh / 2} {hx + 16},{cy + hh / 2}"
    s.append(f'<polygon points="{pts}" fill="{{card}}" stroke="{{cstroke}}"/>')
    s.append(text(hx + hw / 2, cy - 3, "killswitch_", 12.5, 600, mono=True, anchor="middle"))
    s.append(text(hx + hw / 2, cy + 14, "<flag> on?", 12.5, 600, mono=True, anchor="middle"))
    s.append(arrow(f"M{L + 220},{cy} L{hx - 4},{cy}"))
    # branches
    bx, bw = hx + hw + 56, 196
    s.append(f'<rect x="{bx}" y="{cy - 62}" width="{bw}" height="44" rx="10" fill="{{asoft}}" stroke="{{accent}}" stroke-width="2"/>')
    s.append(text(bx + 16, cy - 35, "control value", 14, 700, "accent"))
    s.append(text(bx + bw - 14, cy - 35, "known good", 12, 400, "muted", "end"))
    s.append(f'<rect x="{bx}" y="{cy + 18}" width="{bw}" height="44" rx="10" fill="{{card}}" stroke="{{cstroke}}"/>')
    s.append(text(bx + 16, cy + 45, "Remote Config / Statsig", 13, 600))
    s.append(arrow(f"M{hx + hw - 8},{cy - 14} C{hx + hw + 24},{cy - 40} {bx - 30},{cy - 40} {bx - 4},{cy - 40}"))
    s.append(arrow(f"M{hx + hw - 8},{cy + 14} C{hx + hw + 24},{cy + 40} {bx - 30},{cy + 40} {bx - 4},{cy + 40}"))
    s.append(text(hx + hw + 14, cy - 46, "yes", 12, 600, "accent"))
    s.append(text(hx + hw + 14, cy + 56, "no", 12, 600, "muted"))
    # tag
    tx = bx + bw + 52
    s += card(tx, cy - 42, R - 20 - tx, 84, "Flags.tag()", ["ff_new_checkout=on on", "every crash and session"], mono_title=True)
    s.append(arrow(f"M{bx + bw},{cy - 40} C{tx - 20},{cy - 40} {tx - 30},{cy - 10} {tx - 4},{cy - 8}"))
    s.append(arrow(f"M{bx + bw},{cy + 40} C{tx - 20},{cy + 40} {tx - 30},{cy + 10} {tx - 4},{cy + 8}"))

    # ---- Data
    mid = (L + R) / 2
    py = ay + ah + 44
    s.append(arrow(f"M{mid},{ay + ah} L{mid},{py - 4}"))
    s.append(text(mid + 12, ay + ah + 26, "crashes · ANRs · errors · sessions, tagged by variant", 12.5, 400, "muted"))
    pw = 420
    s.append(f'<rect x="{mid - pw / 2}" y="{py}" width="{pw}" height="44" rx="22" fill="{{pill}}" stroke="{{pillstroke}}"/>')
    s.append(text(mid, py + 27, "Crashlytics → BigQuery export   or   Sentry API", 13.5, 600, anchor="middle"))

    # ---- Job panel
    jy = py + 44 + 40
    s.append(arrow(f"M{mid},{py + 44} L{mid},{jy - 4}"))
    rw = R - L - 40  # usable row width
    r1y, r2y, chh = jy + 52, jy + 52 + 96 + 40, 96
    jh = (r2y + chh + 24) - jy
    s.insert(0, f'<rect x="{L}" y="{jy}" width="{R - L}" height="{jh}" rx="14" fill="{{panel}}" stroke="{{pstroke}}"/>')
    s.append(text(L + 20, jy + 30, "Culprit job", 16, 700))
    s.append(text(L + 120, jy + 30, "Cloud Run · every 15 minutes · last 7 days", 13, 400, "muted"))
    g = 28
    w3 = (rw - 2 * g) / 3
    row1 = [("Count", ["affected and active users per flag,", "variant, version and signal"]),
            ("Detect", ["each variant vs control: ≥ 2×,", "sequential test (mSPRT)"]),
            ("De-duplicate", ["skip anything already", "reported in the last 7 days"])]
    for i, (t, ls) in enumerate(row1):
        x = L + 20 + i * (w3 + g)
        s += card(x, r1y, w3, chh, t, ls, num=i + 1)
        if i:
            s.append(arrow(f"M{x - g + 2},{r1y + chh / 2} L{x - 4},{r1y + chh / 2}"))
    w4 = (rw - 3 * 20) / 4
    row2 = [("Kill switch", ["turn on killswitch_<flag>", "if auto-kill is allowed"], True),
            ("Breakdown", ["where errors pile up:", "OS and device"], False),
            ("Slack alert", ["numbers, action taken", "and how to undo"], False),
            ("Audit row", ["BigQuery, behind the", "Looker / Grafana dashboards"], False)]
    for i, (t, ls, danger) in enumerate(row2):
        x = L + 20 + i * (w4 + 20)
        s += card(x, r2y, w4, chh, t, ls, danger=danger, num=i + 4)
        if i:
            s.append(arrow(f"M{x - 18},{r2y + chh / 2} L{x - 4},{r2y + chh / 2}"))
    # row 1 -> row 2 elbow
    ex = L + 20 + 2 * (w3 + g) + w3 / 2
    sx = L + 20 + w4 / 2
    ym = r1y + chh + 20
    s.append(arrow(f"M{ex},{r1y + chh} L{ex},{ym} L{sx},{ym} L{sx},{r2y - 4}"))

    # ---- Feedback: kill switch -> app (dashed, accent), along the left margin
    fx = 18
    ky = r2y + chh / 2
    s.append(arrow(f"M{L + 20},{ky} L{fx},{ky} L{fx},{ay + ah - 18} L{hx + hw / 2},{ay + ah - 18} L{hx + hw / 2},{cy + hh / 2 + 4}",
                   "accent", dash=True, width=1.8, head="ha"))
    s.append(text(hx + hw / 2 + 12, ay + ah - 6, "next flag fetch: users get control", 12.5, 600, "accent"))

    H = jy + jh + 20
    return s, H


def render(theme):
    body, H = build()
    c = THEMES[theme]
    defs = (f'<defs>'
            f'<marker id="h" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{c["arrow"]}"/></marker>'
            f'<marker id="ha" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{c["accent"]}"/></marker>'
            f'</defs>')
    svg = "\n".join(body)
    for k, v in c.items():
        svg = svg.replace("{" + k + "}", v)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H:.0f}" width="{W}" height="{H:.0f}" '
            f'role="img" aria-labelledby="t d">\n<title id="t">How Culprit works</title>\n'
            f'<desc id="d">The app reads flags through Flags.variant, which serves the control value when killswitch_flag is on, '
            f'and tags every crash and session with the variant. Crash data reaches Culprit through the Crashlytics BigQuery export '
            f'or the Sentry API. Every 15 minutes the Culprit job counts users per variant, detects regressions against control with a sequential test, '
            f'skips repeats, optionally turns on the kill switch, breaks errors down by OS and device, alerts Slack and writes an '
            f'audit row. On the next flag fetch, users get the control value.</desc>\n{defs}\n{svg}\n</svg>\n')


for theme in THEMES:
    (OUT / f"architecture-{theme}.svg").write_text(render(theme))
print("ok")
