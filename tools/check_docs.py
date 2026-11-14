"""Doc check: dead links and missing header blocks in the Markdown docs.

Run ``python tools/check_docs.py`` (report only) or with ``--strict`` (exit 1 on any finding).

What it checks:
- every relative Markdown link points to a file that exists;
- every ``docs/....md`` path cited in a code comment or a doc exists;
- every doc outside ``design-system/``, ``dist/``, ``build/`` and ``docs/private/`` opens with the
  header block (Audience / Scope / Kept current).

It does not check that a doc is accurate.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
DOC_PATH = re.compile(r"docs/[A-Za-z0-9_./-]+\.md")
HEADER_FIELDS = ("**Audience:**", "**Scope:**", "**Kept current:**")
# Files that are not docs in the sense of the header rule.
HEADER_EXEMPT = {"LICENSE", "THIRD_PARTY_NOTICES.md", "CLAUDE.md"}
HEADER_EXEMPT_DIRS = ("design-system/", "dist/", "build/", "docs/private/", "homebound/theme/", "packaging/icons/")


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if (ROOT / line).exists()]


def strip_code(text: str) -> str:
    """Drop fenced code blocks and inline code so example links are not checked."""
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    return re.sub(r"`[^`\n]*`", "", text)


def check_links(path: str, text: str) -> list[str]:
    problems = []
    base = (ROOT / path).parent
    for target in LINK.findall(strip_code(text)) + IMAGE.findall(strip_code(text)):
        if re.match(r"^[a-z]+:", target) or target.startswith("#"):
            continue
        file_part = target.split("#", 1)[0]
        if file_part and not (base / file_part).exists():
            problems.append(f"{path}: dead link {target}")
    return problems


def check_cited_paths(path: str, text: str) -> list[str]:
    problems = []
    for cited in sorted(set(DOC_PATH.findall(text))):
        if "<" in cited or "*" in cited or "..." in cited or cited.startswith("docs/private/"):
            continue   # placeholders, and the gitignored private folder, which a clean checkout lacks
        if not (ROOT / cited).exists():
            problems.append(f"{path}: cited path does not exist: {cited}")
    return problems


def check_header(path: str, text: str) -> list[str]:
    head = "\n".join(text.splitlines()[:40])
    missing = [field for field in HEADER_FIELDS if field not in head]
    return [f"{path}: header block lacks {', '.join(missing)}"] if missing else []


def is_doc_needing_header(path: str) -> bool:
    if not path.endswith(".md") or path in HEADER_EXEMPT or Path(path).name in HEADER_EXEMPT:
        return False
    return not path.startswith(HEADER_EXEMPT_DIRS)


def main() -> int:
    problems: list[str] = []
    for path in tracked_files():
        full = ROOT / path
        if path.endswith(".md") and not path.startswith(("dist/", "build/", "docs/private/")):
            text = full.read_text(encoding="utf-8")
            if not path.startswith("design-system/"):
                problems += check_links(path, text)
            if is_doc_needing_header(path):
                problems += check_header(path, text)
            if path.startswith(("docs/plans/done/", "docs/investigations/")):
                continue
            problems += check_cited_paths(path, text)
        elif path.endswith((".py", ".yml", ".sh", ".iss", ".spec")) and not path.startswith(("tools/check_docs", "tests/test_check_docs")):
            try:
                text = full.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            problems += check_cited_paths(path, text)
    for problem in problems:
        print(problem)
    print(f"{len(problems)} finding(s)")
    return 1 if problems and "--strict" in sys.argv else 0


if __name__ == "__main__":
    raise SystemExit(main())
