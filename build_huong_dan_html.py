# build_huong_dan_html.py — one-off script: renders HUONG_DAN_DEMO.md into a
# styled, self-contained HUONG_DAN_DEMO.html so testers can open the guide
# directly in a browser (double-click, no markdown viewer needed) instead of
# reading raw markdown. Rerun this after editing the .md — the .html is a
# generated artifact, not a second source of truth.
import html
import os
import re

import markdown

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MD_PATH  = os.path.join(BASE_DIR, "HUONG_DAN_DEMO.md")
OUT_PATH = os.path.join(BASE_DIR, "HUONG_DAN_DEMO.html")

# Slugify the same way GitHub-flavored markdown does, so the Mục Lục's
# "#5-demo-hỏi-đáp..." anchors (written by hand in the .md, Vietnamese
# diacritics and all) actually land on the right heading — python-markdown's
# own `toc` extension normalizes headings differently (strips diacritics),
# which would silently break every one of the doc's existing internal links.
# GitHub's algorithm: lowercase, drop anything that isn't a word char/space/
# hyphen, then replace EACH space with a hyphen individually — deliberately
# NOT collapsing runs of spaces, because deleting a punctuation token that
# had a space on both sides (" & ", " — ", " / ") leaves two adjacent spaces,
# which GitHub renders as a double hyphen. Collapsing them (an earlier bug
# here used \s+ → single "-") produced e.g. "yêu-cầu-cài-đặt" instead of the
# hand-written "yêu-cầu--cài-đặt", breaking every TOC link with "&"/"—"/"/"
# in its heading text.
def _slug(text: str) -> str:
    text = html.unescape(text).strip().lower()
    text = re.sub(r"[^\w\s\-]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def _add_heading_ids(html: str) -> str:
    def repl(m):
        level, attrs_and_text = m.group(1), m.group(2)
        text = re.sub(r"<[^>]+>", "", attrs_and_text)
        return f'<h{level} id="{_slug(text)}">{attrs_and_text}</h{level}>'
    return re.sub(r"<h([1-6])>(.*?)</h\1>", repl, html, flags=re.DOTALL)


def build():
    with open(MD_PATH, encoding="utf-8") as f:
        md_text = f.read()

    body_html = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
    )
    body_html = _add_heading_ids(body_html)

    html = f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Hướng Dẫn Demo — RAG Legal Assistant</title>
<style>
  :root {{
    --navy:   #0B1F3A;
    --navy2:  #132843;
    --navy3:  #1B335A;
    --teal:   #0A8C7E;
    --teal2:  #0DB4A2;
    --gold:   #E8A020;
    --white:  #F7F9FC;
    --gray:   #8A97A8;
    --border: #2A4066;
    --green:  #1D9B6C;
    --red:    #E5484D;
  }}
  * {{ box-sizing: border-box; }}
  html {{ scroll-behavior: smooth; }}
  body {{
    margin: 0;
    background: var(--navy);
    color: var(--white);
    font-family: 'Segoe UI', 'DM Sans', Arial, sans-serif;
    line-height: 1.65;
  }}
  .page {{
    max-width: 900px;
    margin: 0 auto;
    padding: 40px 28px 100px;
  }}
  h1 {{
    font-size: 30px;
    font-weight: 700;
    border-bottom: 2px solid var(--teal2);
    padding-bottom: 14px;
    margin-bottom: 6px;
  }}
  h1 + blockquote {{
    color: var(--gray);
    font-style: italic;
    margin: 0 0 30px;
    border: none;
    padding: 0;
  }}
  h2 {{
    font-size: 22px;
    font-weight: 700;
    color: var(--teal2);
    margin-top: 48px;
    padding: 10px 16px;
    background: var(--navy2);
    border-left: 4px solid var(--teal2);
    border-radius: 6px;
  }}
  h3 {{
    font-size: 17px;
    font-weight: 700;
    color: var(--gold);
    margin-top: 30px;
    border-bottom: 1px solid var(--border);
    padding-bottom: 6px;
  }}
  h4 {{ color: var(--white); font-size: 15px; margin-top: 20px; }}
  p, li {{ font-size: 14.5px; color: #E4E9F2; }}
  a {{ color: var(--teal2); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  code {{
    background: rgba(255,255,255,0.08);
    color: #FFD98A;
    padding: 2px 6px;
    border-radius: 4px;
    font-family: 'Cascadia Code', Consolas, monospace;
    font-size: 13px;
  }}
  pre {{
    background: #081524;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
    overflow-x: auto;
  }}
  pre code {{ background: none; color: #C9D6E8; padding: 0; }}
  blockquote {{
    border-left: 4px solid var(--gold);
    background: rgba(232,160,32,0.08);
    margin: 16px 0;
    padding: 10px 18px;
    border-radius: 0 6px 6px 0;
    color: #F0D9A8;
  }}
  blockquote p {{ margin: 6px 0; color: inherit; }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin: 16px 0;
    font-size: 13.5px;
    overflow-x: auto;
    display: block;
  }}
  table thead {{ display: table; width: 100%; table-layout: fixed; }}
  table tbody {{ display: table; width: 100%; table-layout: fixed; }}
  th, td {{
    border: 1px solid var(--border);
    padding: 9px 12px;
    text-align: left;
    vertical-align: top;
  }}
  th {{ background: var(--navy3); color: var(--teal2); font-weight: 600; }}
  tr:nth-child(even) td {{ background: rgba(255,255,255,0.02); }}
  hr {{ border: none; border-top: 1px solid var(--border); margin: 36px 0; }}
  img {{ max-width: 100%; border-radius: 8px; border: 1px solid var(--border); margin: 12px 0; }}
  strong {{ color: #FFFFFF; }}

  /* Mục Lục — rendered from the doc's own list right after H1, styled as a card */
  h1 ~ ul:first-of-type {{
    background: var(--navy2);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px 24px 20px 40px;
    list-style: none;
  }}
  h1 ~ ul:first-of-type li {{ margin: 6px 0; }}
  h1 ~ ul:first-of-type::before {{
    content: "Mục Lục";
    display: block;
    font-weight: 700;
    color: var(--gold);
    font-size: 15px;
    margin: -4px 0 10px -16px;
    letter-spacing: 0.03em;
  }}

  .top-badge {{
    display: inline-block;
    background: rgba(13,180,162,0.15);
    color: var(--teal2);
    border: 1px solid rgba(13,180,162,0.35);
    padding: 4px 12px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
    margin-bottom: 18px;
  }}

  #back-to-top {{
    position: fixed;
    bottom: 24px;
    right: 24px;
    background: var(--teal2);
    color: var(--navy);
    border: none;
    border-radius: 999px;
    width: 46px;
    height: 46px;
    font-size: 20px;
    cursor: pointer;
    box-shadow: 0 6px 18px rgba(0,0,0,0.35);
    opacity: 0;
    pointer-events: none;
    transition: opacity .2s;
  }}
  #back-to-top.show {{ opacity: 1; pointer-events: auto; }}

  @media (max-width: 640px) {{
    .page {{ padding: 24px 16px 80px; }}
    h1 {{ font-size: 24px; }}
    h2 {{ font-size: 19px; }}
  }}
</style>
</head>
<body>
<div class="page">
<span class="top-badge">📄 Tự động render từ HUONG_DAN_DEMO.md — sửa file .md rồi chạy lại build_huong_dan_html.py</span>
{body_html}
</div>
<button id="back-to-top" onclick="window.scrollTo({{top:0,behavior:'smooth'}})" title="Lên đầu trang">↑</button>
<script>
  const btn = document.getElementById('back-to-top');
  window.addEventListener('scroll', () => {{
    btn.classList.toggle('show', window.scrollY > 500);
  }});
</script>
</body>
</html>
"""

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    return OUT_PATH


if __name__ == "__main__":
    path = build()
    print("Wrote:", path)
