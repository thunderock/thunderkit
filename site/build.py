#!/usr/bin/env python3
"""Build public skill documentation: python3 site/build.py [--out DIR]."""
import argparse
from collections.abc import Callable
import html
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import quote, unquote, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.skill_frontmatter import Frontmatter, parse_skill_file, validate_thunderkit

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


def safe_url(url: str) -> str:
    decoded = html.unescape(url)
    parts = urlsplit(decoded)
    if (any(ord(c) < 33 or ord(c) == 127 for c in decoded) or "\\" in decoded
            or decoded.startswith("/") or parts.scheme not in ("", "https", "http", "mailto")
            or (parts.scheme in ("http", "https") and not parts.netloc)):
        raise ValueError("unsafe link")
    return decoded


def md_to_html(md: str, link: Callable[[str], str] = safe_url) -> str:
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
            out.append(render_table(rows, link))
            continue
        # headings
        h = re.match(r"^(#{1,4})\s+(.*)$", line)
        if h:
            lvl = len(h.group(1))
            anchor = re.sub(r"[^\w -]", "", h.group(2)).lower().replace(" ", "-")
            out.append(f'<h{lvl} id="{html.escape(anchor)}">{inline(h.group(2), link)}</h{lvl}>')
            i += 1
            continue
        # list block
        if re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append("<li>" + inline(re.sub(r"^\s*[-*]\s+", "", lines[i]), link) + "</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        # ordered list
        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append("<li>" + inline(re.sub(r"^\s*\d+\.\s+", "", lines[i]), link) + "</li>")
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
        out.append("<p>" + inline(" ".join(para), link) + "</p>")
    return "\n".join(out)


def render_table(rows: list[str], link: Callable[[str], str]) -> str:
    def cells(r: str) -> list[str]:
        return [c.strip() for c in r.strip().strip("|").split("|")]
    head = cells(rows[0])
    body = [cells(r) for r in rows[2:]]
    h = "".join(f"<th>{inline(c, link)}</th>" for c in head)
    b = "".join("<tr>" + "".join(f"<td>{inline(c, link)}</td>" for c in r) + "</tr>" for r in body)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>"


def inline(s: str, link: Callable[[str], str] = safe_url) -> str:
    out = []
    end = 0
    for m in re.finditer(r"`([^`]+)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)]+)\)", s):
        out.append(html.escape(s[end:m.start()]))
        if m[1] is not None:
            out.append(f"<code>{html.escape(m[1])}</code>")
        elif m[2] is not None:
            out.append(f"<strong>{inline(m[2], link)}</strong>")
        else:
            out.append(f'<a href="{html.escape(link(m[4]))}">{inline(m[3], link)}</a>')
        end = m.end()
    return "".join(out) + html.escape(s[end:])


def page(title: str, body_html: str, public: Path = Path("index.html")) -> str:
    prefix = "../" * (len(public.parts) - 1)
    nav = (f'<a href="{prefix}index.html">Home</a><a href="{prefix}north-star.html">North Star</a>'
           f'<a href="{prefix}roster.html">Model Roster</a>'
           '<a href="https://github.com/thunderock/thunderkit">GitHub</a>')
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} — thunderkit</title><style>{CSS}</style></head>
<body><div class="wrap"><header class="site"><h1><a href="{prefix}index.html">⏩ thunderkit</a></h1>
<p class="mut">Opinionated multi-model delegation for very large repos.</p>
<nav class="top">{nav}</nav></header>
{body_html}
<footer>Generated from <code>skills/*/SKILL.md</code> — do not edit by hand. MIT.</footer>
</div></body></html>"""


def build(out: str | Path, root: Path = ROOT) -> list[str]:
    root = root.resolve()
    sources = {root / "NORTH_STAR.md": Path("north-star.html"),
               root / "skills/references/model-roster.md": Path("roster.html")}
    skills: dict[Path, Frontmatter] = {}
    for d in sorted((root / "skills").iterdir()):
        if d.is_dir() and d.name != "references" and not d.name.startswith("."):
            source = d / "SKILL.md"
            if source.resolve() != source:
                raise ValueError("skill is not a regular public source")
            fm = parse_skill_file(source)
            validate_thunderkit(fm, d.name)
            skills[source] = fm
            sources[source] = Path(f"{fm.name}.html")

    pending = list(sources)
    def link_from(source: Path, url: str) -> str:
        parts = urlsplit(safe_url(url))
        if parts.scheme or not parts.path:
            return safe_url(url)
        target = Path(os.path.abspath(source.parent / unquote(parts.path)))
        if target not in sources:
            rel = target.relative_to(root) if target.is_relative_to(root) else Path(".")
            common = len(rel.parts) == 3 and rel.parts[:2] == ("skills", "references")
            local = (len(rel.parts) == 4 and rel.parts[0] == "skills"
                     and root / "skills" / rel.parts[1] / "SKILL.md" in skills
                     and rel.parts[2] in ("references", "scripts"))
            if not (common or local) or target.suffix not in (".md", ".json", ".py") or target.name.startswith("."):
                raise ValueError(f"link does not name a public source: {url}")
            sources[target] = Path(str(rel) + ".html")
            pending.append(target)
        if not target.is_file() or target.resolve() != target:
            raise ValueError(f"link does not name a regular public source: {url}")
        mapped = quote(os.path.relpath(sources[target], sources[source].parent).replace(os.sep, "/"))
        return urlunsplit(("", "", mapped, parts.query, parts.fragment))

    pages: dict[Path, str] = {}
    for source in pending:
        if not source.is_file() or source.resolve() != source:
            raise ValueError(f"not a regular public source: {source.relative_to(root)}")
        fm = skills.get(source)
        text = fm.body if fm else source.read_text(encoding="utf-8")
        body = (md_to_html(text, lambda url: link_from(source, url)) if source.suffix == ".md"
                else "<pre><code>" + html.escape(text) + "</code></pre>")
        head = ""
        if fm:
            tags = "".join(f'<span class="tag">{html.escape(fm.metadata["thunderkit-" + key])}</span>'
                           for key in ("role", "tier"))
            head = (f'{tags}<h2>{html.escape(fm.name)}</h2><p class="mut">{html.escape(fm.description)}</p>'
                    f'<p>Delegates: {html.escape(fm.metadata["thunderkit-delegates"])}</p>'
                    f'<p>Contract: {html.escape(fm.metadata["thunderkit-contract"])}</p>'
                    f'<p>{html.escape(fm.compatibility or "")}</p>'
                    f'<p class="mut">Install: <code>npx skills add thunderock/thunderkit -s {html.escape(fm.name)} -g</code></p><hr>')
        pages[sources[source]] = page(fm.name if fm else source.stem, head + body, sources[source])

    # index
    cards = []
    for s in skills.values():
        cards.append(f'<div class="card"><h3><a href="{html.escape(s.name)}.html">{html.escape(s.name)}</a></h3>'
                     f'<p>{html.escape(s.description)}</p></div>')
    idx = ("<h2>The thesis</h2><p>Big work in big repos is won by <strong>decomposition + "
           "heterogeneity</strong>, not by one smart model. thunderkit turns a large change into "
           "disjoint parallel lanes and routes each to the best model and harness — asking you to "
           "pick the load-bearing ones.</p>"
           "<h2>Install</h2><pre><code>npx skills add thunderock/thunderkit -s '*' -g</code></pre>"
           "<p class='mut'>Or one skill: <code>npx skills add thunderock/thunderkit -s tk-router -g</code></p>"
           "<h2>Skills</h2>" + "".join(cards))
    pages[Path("index.html")] = page("Home", idx)
    names = sorted(s.name for s in skills.values())
    pages[Path("skills.json")] = json.dumps(names, indent=2)
    for name, content in pages.items():
        destination = Path(out) / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    print(f"built {len(skills)} skill pages; {len(pages)} public files → {out}")
    return names


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=ROOT / "site/_site")
    try:
        build(ap.parse_args().out)
    except (OSError, ValueError) as error:
        ap.exit(1, f"site build failed: {error}\n")
