"""Writes docs/USER_GUIDE.md from the in-app guide (osv360craft/ui/help.py), so both say the same."""
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from osv360craft.ui.help import GUIDE_HTML  # noqa: E402


def to_md(h: str) -> str:
    h = re.sub(r"\s*\n\s*", " ", h)
    h = re.sub(r'<a href="([^"]+)">(.*?)</a>', r"[\2](\1)", h)
    for tag, md in (("b", "**"), ("i", "*"), ("code", "`")):
        h = re.sub(rf"<{tag}>(.*?)</{tag}>", rf"{md}\1{md}", h)
    h = re.sub(r"<h1>(.*?)</h1>", r"\n# \1\n\n", h)
    h = re.sub(r"<h2>(.*?)</h2>", r"\n## \1\n\n", h)
    h = re.sub(r"<p>(.*?)</p>", r"\1\n\n", h)
    out, counters = [], []
    for tok in re.split(r"(<ol>|</ol>|<ul>|</ul>|<li>|</li>)", h):
        if tok in ("<ol>", "<ul>"):
            counters.append(0 if tok == "<ol>" else None)
        elif tok in ("</ol>", "</ul>"):
            counters.pop()
            out.append("\n")
        elif tok == "<li>":
            if counters and counters[-1] is not None:
                counters[-1] += 1
                out.append(f"{counters[-1]}. ")
            else:
                out.append("- ")
        elif tok == "</li>":
            out.append("\n")
        else:
            out.append(tok.strip(" ") if counters else tok)
    md = html.unescape("".join(out)).replace("\\\\", "\\")
    md = "\n".join(line.strip() for line in md.splitlines())
    return re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"


if __name__ == "__main__":
    (ROOT / "docs" / "USER_GUIDE.md").write_text(to_md(GUIDE_HTML), encoding="utf-8")
    print("docs/USER_GUIDE.md written")
