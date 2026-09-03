#!/usr/bin/env python3
"""thunderkit static site generator. Stdlib only, no deps.

Builds one HTML page per skill (from SKILL.md frontmatter + body), plus a north-star page and
a roster page, into --out (default site/_site). The published skill set is written to
_site/skills.json so the drift test can assert it equals skills/ on disk.

Usage: python3 site/build.py [--out DIR]
"""
import argparse
import html
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "skills")

CSS = """
:root{--bg:#0b0f17;--fg:#e6edf3;--mut:#8b949e;--acc:#f0b429;--card:#111725;--brd:#222b3a}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:820px;margin:0 auto;padding:2.5rem 1.25rem}
header.site{border-bottom:1px solid var(--brd);margin-bottom:2rem}
h1{font-size:2rem;margin:.2rem 0}h2{margin-top:2rem;border-bottom:1px solid var(--brd);padding-bottom:.3rem}
.tag{display:inline-block;font-size:.72rem;color:var(--acc);border:1px solid var(--acc);border-radius:99px;padding:.05rem .55rem;margin-right:.4rem}
.mut{color:var(--mut)}
.card{background:var(--card);border:1px solid var(--brd);border-radius:10px;padding:1rem 1.25rem;margin:.8rem 0}
.card h3{margin:.1rem 0}
pre,code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
pre{background:#0d1220;border:1px solid var(--brd);border-radius:8px;padding:1rem;overflow:auto;font-size:.85rem}
code{background:#0d1220;border-radius:4px;padding:.08rem .35rem;font-size:.88em}
table{border-collapse:collapse;width:100%;margin:1rem 0;font-size:.9rem}
th,td{border:1px solid var(--brd);padding:.45rem .6rem;text-align:left;vertical-align:top}
th{background:#0d1220}
nav.top a{margin-right:1rem}
footer{margin-top:3rem;padding-top:1rem;border-top:1px solid var(--brd);color:var(--mut);font-size:.85rem}
"""


def parse(text):
    """Split SKILL.md into (frontmatter dict, markdown body)."""
    fm = {}
    body = text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            km = re.match(r"^([a-zA-Z_]+):\s*(.*)$", line)
            if km:
                fm[km.group(1)] = km.group(2).strip().strip('"').strip("'")
        body = m.group(2)
    return fm, body


def md_to_html(md):
    """Tiny, safe markdown subset: headings, code fences, inline code, tables, bold, lists, paragraphs."""
    lines = md.splitlines()
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # fenced code
        if line.startswith("```"):
            code = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
            continue
        # table block
        if "|" in line and i + 1 < len(lines) and re.match(r"^\s*\|?[\s:|-]+\|", lines[i + 1]):
            rows = []
            while i < len(lines) and "|" in lines[i]:
                rows.append(lines[i])
                i += 1
            out.append(render_table(rows))
            continue
        # headings
        h = re.match(r"^(#{1,4})\s+(.*)$", line)
        if h:
            lvl = len(h.group(1))
            out.append(f"<h{lvl}>{inline(h.group(2))}</h{lvl}>")
            i += 1
            continue
        # list block
        if re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append("<li>" + inline(re.sub(r"^\s*[-*]\s+", "", lines[i])) + "</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        # ordered list
        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append("<li>" + inline(re.sub(r"^\s*\d+\.\s+", "", lines[i])) + "</li>")
                i += 1
            out.append("<ol>" + "".join(items) + "</ol>")
            continue
        if line.strip() == "":
            i += 1
            continue
        # paragraph (gather until blank)
        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,4}\s|```|\s*[-*]\s|\s*\d+\.\s)", lines[i]) and "|" not in lines[i]:
            para.append(lines[i])
            i += 1
        out.append("<p>" + inline(" ".join(para)) + "</p>")
    return "\n".join(out)


def render_table(rows):
    def cells(r):
        return [c.strip() for c in r.strip().strip("|").split("|")]
    head = cells(rows[0])
    body = [cells(r) for r in rows[2:]]
    h = "".join(f"<th>{inline(c)}</th>" for c in head)
    b = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>"


def inline(s):
    s = html.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    return s


def page(title, nav, body_html):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} — thunderkit</title><style>{CSS}</style></head>
<body><div class="wrap"><header class="site"><h1><a href="index.html">thunderkit</a></h1>
<p class="mut">Opinionated multi-model delegation for very large repos.</p>
<nav class="top">{nav}</nav></header>
{body_html}
<footer>Generated from <code>skills/*/SKILL.md</code> — do not edit by hand. MIT.</footer>
</div></body></html>"""


def build(out):
    os.makedirs(out, exist_ok=True)
    skills = []
    for name in sorted(os.listdir(SKILLS)):
        d = os.path.join(SKILLS, name)
        if not os.path.isdir(d) or name == "references":
            continue
        fm, body = parse(open(os.path.join(d, "SKILL.md"), encoding="utf-8").read())
        skills.append({"name": name, "desc": fm.get("description", ""),
                       "role": fm.get("role", ""), "body": body})

    nav = ('<a href="index.html">Home</a><a href="north-star.html">North Star</a>'
           '<a href="roster.html">Model Roster</a>'
           '<a href="https://github.com/thunderock/thunderkit">GitHub</a>')

    # index
    cards = []
    for s in skills:
        cards.append(f'<div class="card"><h3><a href="{s["name"]}.html">{s["name"]}</a></h3>'
                     f'<p>{html.escape(s["desc"])}</p></div>')
    idx = ("<h2>The thesis</h2><p>Big work in big repos is won by <strong>decomposition + "
           "heterogeneity</strong>, not by one smart model. thunderkit turns a large change into "
           "disjoint parallel lanes and routes each to the best model and harness — asking you to "
           "pick the load-bearing ones.</p>"
           "<h2>Install</h2><pre><code>npx skills add thunderock/thunderkit -s '*' -g</code></pre>"
           "<p class='mut'>Or one skill: <code>npx skills add thunderock/thunderkit -s thunderkit -g</code></p>"
           "<h2>Skills</h2>" + "".join(cards))
    write(out, "index.html", page("Home", nav, idx))

    # per-skill
    for s in skills:
        head = (f'<span class="tag">{html.escape(s["role"] or "skill")}</span>'
                f'<h2>{s["name"]}</h2><p class="mut">{html.escape(s["desc"])}</p>'
                f'<p class="mut">Install: <code>npx skills add thunderock/thunderkit -s {s["name"]} -g</code></p><hr>')
        write(out, f"{s['name']}.html", page(s["name"], nav, head + md_to_html(s["body"])))

    # north star + roster from source files
    ns = open(os.path.join(ROOT, "NORTH_STAR.md"), encoding="utf-8").read()
    write(out, "north-star.html", page("North Star", nav, md_to_html(ns)))
    roster = open(os.path.join(SKILLS, "references", "model-roster.md"), encoding="utf-8").read()
    write(out, "roster.html", page("Model Roster", nav, md_to_html(roster)))

    # machine-readable published set for the drift gate
    write(out, "skills.json", json.dumps(sorted(s["name"] for s in skills), indent=2))
    print(f"built {len(skills)} skill pages + index + north-star + roster → {out}")
    return [s["name"] for s in skills]


def write(out, name, content):
    with open(os.path.join(out, name), "w", encoding="utf-8") as f:
        f.write(content)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "site", "_site"))
    build(ap.parse_args().out)
