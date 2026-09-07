"""Reading, scanning and writing markdown notes on the notes volume."""
import os
import re
import time
import urllib.request
from pathlib import Path

TODO_DIR = "todo"
RESULT_MARKER = "Traité — supprimable"
RESULT_SECTION = "## Résultat"
STABLE_SECONDS = 3
REQUIRED_FRONTMATTER = (
    "title",
    "type",
    "process",
    "deletable",
    "memory_class",
    "lifecycle",
    "source",
)

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


def split_note(text: str) -> tuple[dict, str, str]:
    """Parse a note's text into (frontmatter dict, body str, raw block str).

    The raw block includes the leading/trailing ``---`` delimiters and a
    trailing newline, or ``""`` when the note has no frontmatter.
    """
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return _parse_frontmatter(parts[1]), parts[2].lstrip("\n"), "---" + parts[1] + "---\n"
    return {}, text, ""


def read_note(path: Path) -> tuple[dict, str, str]:
    """Return (frontmatter dict, body str, raw frontmatter block str).

    The raw block is preserved verbatim on write so list-valued fields
    (e.g. ``tags``) survive a rewrite.
    """
    return split_note(path.read_text(encoding="utf-8"))


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


def is_stable(path: Path, min_age: float = STABLE_SECONDS) -> bool:
    """True when the file's mtime is at least ``min_age`` seconds old, so the
    agent does not touch a note mid-typing."""
    try:
        return time.time() - path.stat().st_mtime >= min_age
    except OSError:
        return False


def atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file + ``os.replace`` so neither
    the basic-memory watcher nor Syncthing ever sees a torn write."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _rewrite_if_unchanged(path: Path, transform) -> bool:
    """Apply ``transform(text) -> new_text``, writing atomically only if the
    file did not change between our read and the write (optimistic
    concurrency). Retries a few times so an in-flight user save is picked up
    rather than clobbered. Returns True when the file now has the transformed
    content (or the transform was a no-op), False when it stayed busy.
    """
    for _ in range(3):
        try:
            current = path.read_text(encoding="utf-8")
        except OSError:
            return False
        new_text = transform(current)
        if new_text == current:
            return True
        try:
            if path.read_text(encoding="utf-8") == current:
                atomic_write(path, new_text)
                return True
        except OSError:
            return False
    return False


def frontmatter_bool(frontmatter: dict, key: str) -> bool | None:
    """Interpret a frontmatter key as a boolean: True/False, or None when the
    key is absent."""
    value = frontmatter.get(key)
    if value is None:
        return None
    return str(value).strip().lower() in ("true", "1", "yes")


def frontmatter_is_complete(frontmatter: dict) -> bool:
    """True when all action keys the agent manages are present."""
    return all(frontmatter.get(k) is not None for k in REQUIRED_FRONTMATTER)


def _strip_result_checkbox(body: str) -> str:
    """Remove the legacy ``- [ ] Traité — supprimable`` line; the flag now
    lives in the frontmatter as ``deletable``."""
    return re.sub(
        r"(?im)^- \[[ xX]\]\s*" + re.escape(RESULT_MARKER) + r"\s*$\n?",
        "",
        body,
    )


def set_frontmatter_value(raw: str, key: str, value: str) -> str:
    """Return the raw frontmatter block with ``key`` set to ``value``,
    inserting the key before the closing delimiter when absent. Only touches
    the block, never the body."""
    pattern = rf"(?im)^({re.escape(key)}):\s*.*$"
    if re.search(pattern, raw):
        return re.sub(pattern, rf"\1: {value}", raw, count=1)
    lines = raw.splitlines()
    end = len(lines)
    while end > 0 and lines[end - 1].strip() == "":
        end -= 1
    lines.insert(end - 1, f"{key}: {value}")
    return "\n".join(lines) + "\n"


def inject_frontmatter_text(stem: str, text: str) -> str:
    """Return ``text`` with the action frontmatter ensured (merge only:
    existing keys are preserved, missing ones are added). Hubs are left
    untouched. Any stray legacy checkbox is stripped from the body. Returns
    ``text`` unchanged when nothing to do."""
    front, body, raw = split_note(text)
    if is_hub(front) or frontmatter_is_complete(front):
        return text
    body = _strip_result_checkbox(body)
    if raw == "":
        block = (
            "---\n"
            f"title: {_yaml_scalar(stem)}\n"
            "type: todo\n"
            "tags:\n"
            "- todo\n"
            "process: false\n"
            "deletable: false\n"
            "memory_class: working\n"
            "lifecycle: raw\n"
            "source: human\n"
            "---\n"
        )
        return block + ("\n" + body if body else "")
    lines = raw.splitlines()
    inserts = []
    for key, value in (
        ("title", _yaml_scalar(stem)),
        ("type", "todo"),
        ("process", "false"),
        ("deletable", "false"),
        ("memory_class", "working"),
        ("lifecycle", "raw"),
        ("source", "human"),
    ):
        if front.get(key) is None:
            inserts.append(f"{key}: {value}")
    if not re.search(r"(?im)^tags:", raw):
        inserts.append("tags:")
        inserts.append("- todo")
    if not inserts:
        return text
    end = len(lines)
    while end > 0 and lines[end - 1].strip() == "":
        end -= 1
    lines[end - 1 : end - 1] = inserts
    return "\n".join(lines) + "\n" + ("\n" + body if body else "")


def ensure_frontmatter(path: Path) -> bool:
    """Inject/migrate the action frontmatter atomically, without clobbering a
    concurrent user save. Returns True when the note now has it (or already
    did), False when the file stayed busy."""
    return _rewrite_if_unchanged(path, lambda text: inject_frontmatter_text(path.stem, text))


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
    path.write_text(_render_note(result, folder), encoding="utf-8")
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


def _render_note(result: dict, folder: str) -> str:
    lines = ["---", f"title: {_yaml_scalar(result['title'])}", f"type: {result.get('note_type', 'note')}"]
    if result.get("url"):
        lines.append(f"url: {result['url']}")
    tags = result.get("tags") or []
    if tags:
        lines.append("tags:")
        lines.extend(f"- {t}" for t in tags)
    memory_class = "procedural" if folder in {"guidelines", "persona"} else "working" if folder == "todo" else "semantic"
    source = "external" if result.get("url") else "agent"
    lines.extend([
        f"memory_class: {memory_class}",
        "lifecycle: candidate",
        f"source: {source}",
    ])
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
    prefix = re.split(rf"\n?{re.escape(RESULT_SECTION)}\n", body, maxsplit=1)[0]
    prefix = prefix.rstrip("\n")
    return prefix + "\n\n" + section.rstrip("\n") + "\n"


def append_result(path: Path, result_titles: list[str]) -> bool:
    """Append/replace the ``## Résultat`` section and reset ``process: false``
    on fresh content, never clobbering a concurrent user edit. Returns True on
    success, False when the file stayed busy."""

    def _apply(text: str) -> str:
        front, body, raw = split_note(text)
        new_body = ensure_result_section(body, result_titles)
        if front.get("process") is not None:
            raw = set_frontmatter_value(raw, "process", "false")
        return raw + new_body

    return _rewrite_if_unchanged(path, _apply)
