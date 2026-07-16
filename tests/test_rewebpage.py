from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from parsers.rewebpage_common import (
    make_snapshot,
    output_dir_for_url,
    validate_urls,
    write_page_bundle,
)
from parsers.rewebpage_craw import to_snapshot as crawl4ai_snapshot
from parsers.rewebpage_firecrawl import to_snapshot as firecrawl_snapshot


class RewebpageCommonTests(unittest.TestCase):
    def test_single_url_output_is_flat_and_batch_uses_slug(self) -> None:
        root = Path("/tmp/web")
        url = "https://example.com/docs?id=1"
        self.assertEqual(output_dir_for_url(root, url, url_count=1), root)
        self.assertEqual(
            output_dir_for_url(root, url, url_count=2),
            root / "example.com_docs_id_1",
        )

    def test_snapshot_normalizes_links_headings_and_cjk_count(self) -> None:
        snapshot = make_snapshot(
            provider="test",
            source_url="https://example.com/docs",
            final_url="https://example.com/docs",
            markdown="# 标题\n\n中文 test text",
            links=["/a#part", "https://other.example/b", "mailto:a@example.com"],
            images=["/image.png"],
        )
        self.assertEqual(snapshot.headings, ["标题"])
        self.assertGreaterEqual(snapshot.word_count, 6)
        self.assertEqual(snapshot.links_internal, ["https://example.com/a"])
        self.assertEqual(snapshot.links_external, ["https://other.example/b"])
        self.assertEqual(snapshot.images, ["https://example.com/image.png"])
        self.assertTrue(snapshot.markdown.endswith("\n"))

    def test_invalid_url_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_urls(["example.com/no-scheme"])

    def test_page_bundle_has_consistent_snapshot_envelope(self) -> None:
        snapshot = make_snapshot(
            provider="test",
            source_url="https://example.com",
            final_url="https://example.com",
            markdown="# Example",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_page_bundle(Path(temp_dir), snapshot, raw={"ok": True})
            self.assertEqual({path.name for path in paths}, {"page.json", "page.md"})
            payload = json.loads(
                (Path(temp_dir) / "page.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["snapshot"]["provider"], "test")
            self.assertEqual(payload["raw"], {"ok": True})


class RewebpageAdapterTests(unittest.TestCase):
    def test_firecrawl_dict_is_normalized(self) -> None:
        snapshot = firecrawl_snapshot(
            {
                "markdown": "# Firecrawl\n",
                "links": ["/docs"],
                "images": ["/hero.png"],
                "metadata": {
                    "url": "https://example.com/final",
                    "status_code": 200,
                    "description": "description",
                },
            },
            "https://example.com/start",
        )
        self.assertEqual(snapshot.provider, "firecrawl")
        self.assertEqual(str(snapshot.url), "https://example.com/final")
        self.assertEqual(snapshot.links_internal, ["https://example.com/docs"])

    def test_crawl4ai_object_is_normalized(self) -> None:
        result = SimpleNamespace(
            markdown=SimpleNamespace(raw_markdown="# Raw", fit_markdown="# Fit"),
            metadata={"title": "Page"},
            links={
                "internal": [{"href": "https://example.com/a"}],
                "external": [{"href": "https://other.example/b"}],
            },
            media={"images": [{"src": "https://example.com/image.png"}]},
            redirected_url="https://example.com/final",
            url="https://example.com/start",
            status_code=200,
        )
        snapshot = crawl4ai_snapshot(
            result,
            "https://example.com/start",
            raw_markdown=False,
        )
        self.assertEqual(snapshot.provider, "crawl4ai")
        self.assertEqual(snapshot.markdown, "# Fit\n")
        self.assertEqual(snapshot.title, "Page")


if __name__ == "__main__":
    unittest.main()
