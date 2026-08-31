"""Reading, scanning and writing markdown notes on the notes volume."""
import re
import urllib.request
from pathlib import Path

TODO_DIR = "todo"
RESULT_MARKER = "Traité — supprimable"
RESULT_SECTION = "## Résultat"

CONFLICT_RE = re.compile(r"\.sync-conflict-\d+-\d+.*\.md$", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s)\]}>\"']+")
GITHUB_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+?)(/.*)?$")
FRONTMATTER_LINE_RE = re.compile(r"^([A-Za-z_][\w-]*):\s*(.*)$")

MAX_FETCH = 40_000


def list_todo_files(notes_dir: Path) -> list[Path]:
    todo = Path(notes_dir) / TODO_DIR
    if not todo.is_dir():
        return []
    return sorted(p for p in todo.glob("*.md") if not CONFLICT_RE.search(p.name))


def read_note(path: Path) -> tuple[dict, str, str]:
    """Return (frontmatter dict, body str, raw frontmatter block str).

    The raw block includes the leading/trailing ``---`` delimiters and a
    trailing newline, or ``""`` when the note has no frontmatter. It is
    preserved verbatim on write so list-valued fields (e.g. ``tags``)
    survive a rewrite.
    """
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return _parse_frontmatter(parts[1]), parts[2].lstrip("\n"), "---" + parts[1] + "---\n"
    return {}, text, ""


def _parse_frontmatter(block: str) -> dict:
    """Parse scalar `key: value` lines only (we only need `type`/`title`)."""
    front: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        m = FRONTMATTER_LINE_RE.match(line)
        if not m:
            continue
        value = m.group(2).strip()
        if not value or value.startswith(("#", "{", "[", "&", "*", ">", "|", "!!")):
            continue
        front[m.group(1)] = value.strip().strip('"').strip("'")
    return front


def is_hub(frontmatter: dict) -> bool:
    return frontmatter.get("type") == "hub"


def has_ticked_checkbox(body: str) -> bool:
    return bool(re.search(r"- \[x\]\s*" + re.escape(RESULT_MARKER), body, re.IGNORECASE))


def extract_urls(text: str) -> list[str]:
    urls = []
    for u in URL_RE.findall(text):
        u = u.rstrip(".,;:!?)")
        if u and u not in urls:
            urls.append(u)
    return urls


def fetch_text(url: str, urlopen=None, timeout: float = 20.0) -> str:
    opener = urlopen or urllib.request.urlopen
    candidates = [url]
    m = GITHUB_RE.match(url)
    if m:
        owner, repo, _ = m.groups()
        candidates.insert(0, f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md")
    text = ""
    for target in candidates:
        try:
            with opener(target, timeout=timeout) as resp:
                raw = resp.read(MAX_FETCH)
            charset = resp.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
            break
        except Exception:
            continue
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_FETCH]


def write_result_note(notes_dir: Path, folder: str, result: dict) -> Path:
    folder_dir = Path(notes_dir) / folder
    folder_dir.mkdir(parents=True, exist_ok=True)
    path = folder_dir / f"{_safe_filename(result['title'])}.md"
    path.write_text(_render_note(result), encoding="utf-8")
    return path


def _safe_filename(title: str) -> str:
    """Strip path separators/control chars so an LLM title cannot escape the folder."""
    cleaned = re.sub(r"[/\\\x00-\x1f]", "", title).strip().rstrip(".")
    return cleaned or "Untitled"


def _yaml_scalar(value: str) -> str:
    """Quote a YAML scalar when leaving it plain would break the frontmatter."""
    if value == "" or value != value.strip() or ":" in value or "#" in value or '"' in value or "'" in value:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _render_note(result: dict) -> str:
    lines = ["---", f"title: {_yaml_scalar(result['title'])}", f"type: {result.get('note_type', 'note')}"]
    if result.get("url"):
        lines.append(f"url: {result['url']}")
    tags = result.get("tags") or []
    if tags:
        lines.append("tags:")
        lines.extend(f"- {t}" for t in tags)
    if result.get("kind"):
        lines.append(f"kind: {result['kind']}")
    lines.append("---")
    if result.get("body"):
        lines.extend(["", result["body"].strip()])
    if result.get("observations"):
        lines.extend(["", "## Observations"])
        lines.extend(f"- {obs}" for obs in result["observations"])
    if result.get("relations"):
        lines.extend(["", "## Relations"])
        lines.extend(f"- {rel}" for rel in result["relations"])
    return "\n".join(lines) + "\n"


def ensure_result_section(body: str, result_titles: list[str]) -> str:
    section = RESULT_SECTION + "\n"
    section += "".join(f"- [[{t}]]\n" for t in result_titles)
    section += f"- [ ] {RESULT_MARKER}\n"
    prefix = re.split(rf"\n?{re.escape(RESULT_SECTION)}\n", body, maxsplit=1)[0]
    prefix = prefix.rstrip("\n")
    return prefix + "\n\n" + section.rstrip("\n") + "\n"