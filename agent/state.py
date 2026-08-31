"""Persistent per-note processing state (sha256 hash bookkeeping)."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class StateStore:
    """JSON file mapping relative note paths to {hash, processed_at}."""

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.state_dir / "state.json"
        self._data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)
        tmp.replace(self.path)

    def hash_for(self, rel_path: str) -> str | None:
        entry = self._data.get(rel_path)
        return entry["hash"] if entry else None

    def set_processed(self, rel_path: str, file_hash: str) -> None:
        self._data[rel_path] = {
            "hash": file_hash,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
        self.save()

    def remove(self, rel_path: str) -> None:
        if rel_path in self._data:
            del self._data[rel_path]
            self.save()