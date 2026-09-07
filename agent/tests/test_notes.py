import email.message
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from notes import (
    _rewrite_if_unchanged,
    append_result,
    atomic_write,
    ensure_frontmatter,
    ensure_result_section,
    extract_urls,
    fetch_text,
    frontmatter_bool,
    frontmatter_is_complete,
    has_ticked_checkbox,
    inject_frontmatter_text,
    is_hub,
    is_stable,
    list_todo_files,
    read_note,
    set_frontmatter_value,
    split_note,
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

    def test_generated_result_has_memory_metadata_and_no_kind(self):
        with tempfile.TemporaryDirectory() as d:
            result = {
                "folder": "tools",
                "title": "Generated Tool",
                "note_type": "tool",
                "url": "https://example.com/tool",
                "tags": [],
                "kind": "fact",
                "body": "Generated body.",
            }
            path = write_result_note(Path(d), result["folder"], result)
            text = path.read_text(encoding="utf-8")
            self.assertIn("memory_class: semantic", text)
            self.assertIn("lifecycle: candidate", text)
            self.assertIn("source: external", text)
            self.assertNotIn("kind:", text)

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
        self.assertNotIn("Traité — supprimable", out)

    def test_replaces_existing_section(self):
        body = "# Title\n## Résultat\n- [[Old]]\n- [ ] Traité — supprimable\n"
        out = ensure_result_section(body, ["New"])
        self.assertNotIn("[[Old]]", out)
        self.assertIn("[[New]]", out)
        self.assertEqual(out.count("## Résultat"), 1)
        self.assertNotIn("Traité — supprimable", out)


class AppendResultTest(unittest.TestCase):
    def test_appends_section_and_resets_process(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            p.write_text("---\nprocess: true\n---\n# Body", encoding="utf-8")
            self.assertTrue(append_result(p, ["Foo"]))
            text = p.read_text(encoding="utf-8")
            self.assertIn("## Résultat", text)
            self.assertIn("[[Foo]]", text)
            self.assertIn("process: false", text)
            self.assertNotIn("process: true", text)

    def test_leaves_process_alone_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            p.write_text("# Body", encoding="utf-8")
            self.assertTrue(append_result(p, ["Foo"]))
            text = p.read_text(encoding="utf-8")
            self.assertIn("## Résultat", text)
            self.assertNotIn("process", text)


class SplitNoteTest(unittest.TestCase):
    def test_splits_frontmatter_and_body(self):
        front, body, raw = split_note("---\ntitle: T\n---\n# Body")
        self.assertEqual(front.get("title"), "T")
        self.assertEqual(body, "# Body")
        self.assertTrue(raw.startswith("---"))
        self.assertTrue(raw.endswith("---\n"))

    def test_no_frontmatter(self):
        front, body, raw = split_note("just text")
        self.assertEqual(front, {})
        self.assertEqual(body, "just text")
        self.assertEqual(raw, "")


class AtomicWriteTest(unittest.TestCase):
    def test_writes_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            atomic_write(p, "hello")
            self.assertEqual(p.read_text(encoding="utf-8"), "hello")
            self.assertFalse((Path(d) / "n.md.tmp").exists())


class IsStableTest(unittest.TestCase):
    def test_fresh_file_not_stable(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            p.write_text("x", encoding="utf-8")
            self.assertFalse(is_stable(p, min_age=10))

    def test_old_file_stable(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            p.write_text("x", encoding="utf-8")
            past = time.time() - 100
            os.utime(p, (past, past))
            self.assertTrue(is_stable(p, min_age=10))


class RewriteIfUnchangedTest(unittest.TestCase):
    def test_writes_when_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            p.write_text("old", encoding="utf-8")
            ok = _rewrite_if_unchanged(p, lambda t: t + "!")
            self.assertTrue(ok)
            self.assertEqual(p.read_text(encoding="utf-8"), "old!")

    def test_noop_transform_returns_true(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            p.write_text("old", encoding="utf-8")
            self.assertTrue(_rewrite_if_unchanged(p, lambda t: t))

    def test_never_clobbers_concurrent_save(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            p.write_text("old", encoding="utf-8")

            def merge(t):
                p.write_text("user edit", encoding="utf-8")  # user saves during our window
                return "---\nprocess: false\n---\n\n" + t  # built from what we read

            ok = _rewrite_if_unchanged(p, merge)
            self.assertTrue(ok)
            text = p.read_text(encoding="utf-8")
            self.assertIn("process: false", text)
            self.assertIn("user edit", text)  # the in-flight save landed before our write


class FrontmatterBoolTest(unittest.TestCase):
    def test_true_false_and_none(self):
        self.assertTrue(frontmatter_bool({"process": "true"}, "process"))
        self.assertTrue(frontmatter_bool({"process": "TRUE"}, "process"))
        self.assertFalse(frontmatter_bool({"process": "false"}, "process"))
        self.assertFalse(frontmatter_bool({"process": "no"}, "process"))
        self.assertIsNone(frontmatter_bool({}, "process"))


class FrontmatterCompleteTest(unittest.TestCase):
    def test_complete_when_all_present(self):
        front = {
            "title": "T",
            "type": "todo",
            "process": "false",
            "deletable": "false",
            "memory_class": "working",
            "lifecycle": "raw",
            "source": "human",
        }
        self.assertTrue(frontmatter_is_complete(front))

    def test_incomplete_when_missing(self):
        self.assertFalse(frontmatter_is_complete({"type": "todo"}))
        self.assertFalse(frontmatter_is_complete({}))


class InjectFrontmatterTest(unittest.TestCase):
    def test_adds_frontmatter_to_bare_note(self):
        out = inject_frontmatter_text("Ma demande", "Contenu de la demande")
        self.assertIn("title: Ma demande", out)
        self.assertIn("type: todo", out)
        self.assertIn("process: false", out)
        self.assertIn("deletable: false", out)
        self.assertIn("memory_class: working", out)
        self.assertIn("lifecycle: raw", out)
        self.assertIn("source: human", out)
        self.assertIn("- todo", out)
        self.assertIn("Contenu de la demande", out)
        self.assertLess(out.index("---"), out.index("Contenu"))

    def test_adds_memory_metadata_to_existing_todo_frontmatter(self):
        text = "---\ntitle: T\ntype: todo\nprocess: false\ndeletable: false\n---\nBody"
        out = inject_frontmatter_text("T", text)
        self.assertIn("memory_class: working", out)
        self.assertIn("lifecycle: raw", out)
        self.assertIn("source: human", out)

    def test_merges_missing_keys_only(self):
        out = inject_frontmatter_text("foo", "---\ntype: todo\npermalink: main/todo/zz\n---\n# Body")
        self.assertIn("permalink: main/todo/zz", out)
        self.assertIn("process: false", out)
        self.assertEqual(out.count("type: todo"), 1)

    def test_keeps_existing_values(self):
        out = inject_frontmatter_text("foo", "---\ntitle: Mon titre\nprocess: true\n---\nB")
        self.assertIn("title: Mon titre", out)
        self.assertIn("process: true", out)
        self.assertNotIn("title: foo", out)

    def test_idempotent(self):
        text = "---\ntitle: T\ntype: todo\ntags:\n- todo\nprocess: false\ndeletable: false\nmemory_class: working\nlifecycle: raw\nsource: human\n---\nBody"
        self.assertEqual(inject_frontmatter_text("T", text), text)

    def test_skips_hub(self):
        text = "---\ntype: hub\n---\n# Hub"
        self.assertEqual(inject_frontmatter_text("hub", text), text)

    def test_strips_stray_result_checkbox(self):
        text = "x\n\n## Résultat\n- [[Foo]]\n- [ ] Traité — supprimable\n"
        out = inject_frontmatter_text("x", text)
        self.assertNotIn("Traité — supprimable", out)
        self.assertIn("[[Foo]]", out)


class EnsureFrontmatterTest(unittest.TestCase):
    def test_injects_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            p.write_text("demande", encoding="utf-8")
            self.assertTrue(ensure_frontmatter(p))
            text = p.read_text(encoding="utf-8")
            self.assertIn("process: false", text)
            self.assertIn("demande", text)

    def test_noop_on_complete_note(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            text = "---\ntitle: T\ntype: todo\nprocess: false\ndeletable: false\nmemory_class: working\nlifecycle: raw\nsource: human\n---\nB"
            p.write_text(text, encoding="utf-8")
            self.assertTrue(ensure_frontmatter(p))
            self.assertEqual(p.read_text(encoding="utf-8"), text)


class SetFrontmatterValueTest(unittest.TestCase):
    def test_replaces_value(self):
        raw = "---\nprocess: true\n---\n"
        self.assertEqual(set_frontmatter_value(raw, "process", "false"), "---\nprocess: false\n---\n")

    def test_inserts_missing_key_before_closing(self):
        raw = "---\ntype: todo\n---\n"
        out = set_frontmatter_value(raw, "process", "false")
        self.assertEqual(out.count("---"), 2)
        self.assertLess(out.index("process: false"), out.index("---\n", out.index("process: false")))

    def test_single_replacement(self):
        out = set_frontmatter_value("---\nprocess: true\n---\n", "process", "false")
        self.assertEqual(out.count("process: false"), 1)


if __name__ == "__main__":
    unittest.main()
