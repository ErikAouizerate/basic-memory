import email.message
import tempfile
import unittest
from pathlib import Path

from notes import (
    ensure_result_section,
    extract_urls,
    fetch_text,
    has_ticked_checkbox,
    is_hub,
    list_todo_files,
    read_note,
    write_result_note,
)


class _FakeResponse:
    def __init__(self):
        self.headers = email.message.Message()
        self.headers["content-type"] = "text/plain; charset=utf-8"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=None):
        return b"<html>Agentic software factory summary</html>"


def _fake_urlopen(target, timeout=20.0):
    return _FakeResponse()


class ListTodoFilesTest(unittest.TestCase):
    def test_returns_only_md_and_skips_conflicts(self):
        with tempfile.TemporaryDirectory() as d:
            todo = Path(d) / "todo"
            todo.mkdir()
            (todo / "a.md").write_text("x", encoding="utf-8")
            (todo / "b.md.sync-conflict-20260831-123456-ABCDE-ABC").write_text("x", encoding="utf-8")
            (todo / "c.txt").write_text("x", encoding="utf-8")
            files = [p.name for p in list_todo_files(Path(d))]
            self.assertEqual(files, ["a.md"])

    def test_missing_todo_dir_returns_empty(self):
        self.assertEqual(list_todo_files(Path("/nonexistent")), [])


class ReadNoteTest(unittest.TestCase):
    def test_reads_frontmatter_and_body(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            p.write_text("---\ntitle: T\ntype: hub\n---\n# Body", encoding="utf-8")
            front, body, raw = read_note(p)
            self.assertEqual(front.get("type"), "hub")
            self.assertIn("# Body", body)
            self.assertTrue(raw.startswith("---"))
            self.assertTrue(raw.endswith("---\n"))

    def test_no_frontmatter(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            p.write_text("just text", encoding="utf-8")
            front, body, raw = read_note(p)
            self.assertEqual(front, {})
            self.assertEqual(body, "just text")
            self.assertEqual(raw, "")


class HubAndCheckboxTest(unittest.TestCase):
    def test_is_hub(self):
        self.assertTrue(is_hub({"type": "hub"}))
        self.assertFalse(is_hub({"type": "todo"}))
        self.assertFalse(is_hub({}))

    def test_ticked_checkbox_detected(self):
        self.assertTrue(has_ticked_checkbox("- [x] Traité — supprimable"))
        self.assertFalse(has_ticked_checkbox("- [ ] Traité — supprimable"))
        self.assertFalse(has_ticked_checkbox("- [x] other"))


class ExtractUrlsTest(unittest.TestCase):
    def test_extracts_urls(self):
        urls = extract_urls("see https://github.com/a/b and https://x.y/z.")
        self.assertEqual(urls, ["https://github.com/a/b", "https://x.y/z"])


class FetchTextTest(unittest.TestCase):
    def test_strips_html(self):
        fetched = fetch_text("https://github.com/disler/super-simple-software-factory", urlopen=_fake_urlopen)
        self.assertIn("Agentic software factory", fetched)
        self.assertNotIn("<", fetched)

    def test_fetch_failure_returns_empty(self):
        def boom(target, timeout=20.0):
            raise OSError("nope")

        self.assertEqual(fetch_text("https://x.y/z", urlopen=boom), "")


class WriteResultNoteTest(unittest.TestCase):
    def test_writes_file_in_folder(self):
        with tempfile.TemporaryDirectory() as d:
            result = {
                "folder": "tools",
                "title": "Super Simple Software Factory",
                "note_type": "tool",
                "url": "https://github.com/disler/super-simple-software-factory",
                "tags": ["ai"],
                "kind": "fact",
                "observations": ["[usage] Agentic factory #ai"],
                "relations": ["[[Tools Catalog — Software & Services]]"],
                "body": "## Summary\nFrench text.",
            }
            path = write_result_note(Path(d), result["folder"], result)
            self.assertTrue(path.exists())
            text = path.read_text(encoding="utf-8")
            self.assertIn("title: Super Simple Software Factory", text)
            self.assertIn("## Observations", text)
            self.assertIn("[[Tools Catalog — Software & Services]]", text)

    def test_sanitizes_title_in_filename(self):
        with tempfile.TemporaryDirectory() as d:
            result = {
                "folder": "tools",
                "title": "Foo/Bar: baz",
                "note_type": "tool",
                "url": None,
                "tags": [],
                "kind": "fact",
                "observations": [],
                "relations": [],
                "body": "",
            }
            path = write_result_note(Path(d), result["folder"], result)
            self.assertEqual(path.name, "FooBar: baz.md")
            self.assertEqual(path.parent, Path(d) / "tools")


class EnsureResultSectionTest(unittest.TestCase):
    def test_appends_section(self):
        out = ensure_result_section("# Title\nline", ["Foo"])
        self.assertIn("## Résultat", out)
        self.assertIn("[[Foo]]", out)
        self.assertIn("- [ ] Traité — supprimable", out)

    def test_replaces_existing_section(self):
        body = "# Title\n## Résultat\n- [[Old]]\n- [ ] Traité — supprimable\n"
        out = ensure_result_section(body, ["New"])
        self.assertNotIn("[[Old]]", out)
        self.assertIn("[[New]]", out)
        self.assertEqual(out.count("## Résultat"), 1)


if __name__ == "__main__":
    unittest.main()