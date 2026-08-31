import tempfile
import unittest
from pathlib import Path

from state import StateStore, sha256_file


class StateStoreTest(unittest.TestCase):
    def test_roundtrip_persists(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(Path(d))
            store.set_processed("todo/a.md", "abc123")
            store2 = StateStore(Path(d))
            self.assertEqual(store2.hash_for("todo/a.md"), "abc123")

    def test_unknown_path_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(Path(d))
            self.assertIsNone(store.hash_for("todo/nope.md"))

    def test_remove_deletes_entry(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(Path(d))
            store.set_processed("todo/a.md", "h")
            store.remove("todo/a.md")
            self.assertIsNone(store.hash_for("todo/a.md"))

    def test_corrupt_state_recovers_empty(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "state.json").write_text("{not json", encoding="utf-8")
            store = StateStore(Path(d))
            self.assertIsNone(store.hash_for("todo/a.md"))


class Sha256Test(unittest.TestCase):
    def test_sha256_matches_hashlib(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "f.txt"
            p.write_text("hello", encoding="utf-8")
            self.assertEqual(
                sha256_file(p),
                "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
            )


if __name__ == "__main__":
    unittest.main()