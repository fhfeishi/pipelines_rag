from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from parsers.redox_mineru import (
    build_command,
    build_environment,
    normalize_outputs,
    parse_args,
)
from parsers.static_structurer import choose_tool


class MinerUCommandTests(unittest.TestCase):
    def test_command_is_forced_to_cpu_pipeline(self) -> None:
        args = parse_args(
            [
                "sample.pdf",
                "--method",
                "txt",
                "--start",
                "1",
                "--end",
                "2",
                "--no-formula",
                "--no-table",
            ]
        )
        command = build_command(
            args,
            Path("/tmp/sample.pdf"),
            Path("/tmp/out/raw"),
            "/tmp/bin/mineru",
        )
        self.assertIn("pipeline", command)
        self.assertEqual(command[command.index("-b") + 1], "pipeline")
        self.assertEqual(command[command.index("-m") + 1], "txt")
        self.assertEqual(command[command.index("-f") + 1], "false")
        self.assertEqual(command[command.index("-t") + 1], "false")
        self.assertEqual(command[command.index("-s") + 1], "1")
        self.assertEqual(command[command.index("-e") + 1], "2")

    def test_cpu_environment_limits_concurrency(self) -> None:
        args = parse_args(
            [
                "sample.pdf",
                "--threads",
                "3",
                "--render-threads",
                "1",
                "--processing-window-size",
                "2",
                "--model-source",
                "modelscope",
            ]
        )
        _env, settings = build_environment(args)
        self.assertEqual(settings["CUDA_VISIBLE_DEVICES"], "")
        self.assertEqual(settings["MINERU_INTRA_OP_NUM_THREADS"], "3")
        self.assertEqual(settings["MINERU_PDF_RENDER_THREADS"], "1")
        self.assertEqual(settings["MINERU_PROCESSING_WINDOW_SIZE"], "2")
        self.assertEqual(settings["MINERU_API_MAX_CONCURRENT_REQUESTS"], "1")
        self.assertEqual(settings["MINERU_MODEL_SOURCE"], "modelscope")

    def test_static_structurer_registers_mineru_for_pdf(self) -> None:
        self.assertEqual(choose_tool("pdf", "mineru"), "mineru")


class MinerUNormalizationTests(unittest.TestCase):
    def test_raw_mineru_artifacts_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            document = root / "sample.pdf"
            document.write_bytes(b"%PDF-synthetic")
            out_dir = root / "outputs" / "sample" / "mineru"
            provider_dir = out_dir / "raw" / "sample" / "auto"
            provider_images = provider_dir / "images"
            provider_images.mkdir(parents=True)
            (provider_images / "figure.png").write_bytes(b"synthetic-image")
            (provider_dir / "sample.md").write_text(
                "# Sample\n\n![figure](images/figure.png)\n",
                encoding="utf-8",
            )
            content = [
                {
                    "type": "text",
                    "text": "Hello MinerU",
                    "page_idx": 0,
                    "bbox": [1, 2, 3, 4],
                },
                {
                    "type": "image",
                    "img_path": "images/figure.png",
                    "image_caption": ["Figure 1"],
                    "page_idx": 0,
                    "bbox": [10, 20, 30, 40],
                },
            ]
            (provider_dir / "sample_content_list.json").write_text(
                json.dumps(content), encoding="utf-8"
            )
            (provider_dir / "sample_middle.json").write_text(
                json.dumps({"_version_name": "3.test"}), encoding="utf-8"
            )

            normalized = normalize_outputs(
                document,
                out_dir,
                out_dir / "raw",
                parsed_at="2026-07-16T00:00:00+00:00",
            )

            self.assertEqual(normalized["element_count"], 2)
            self.assertEqual(normalized["image_element_count"], 1)
            self.assertEqual(normalized["copied_image_count"], 1)
            self.assertEqual(normalized["mineru_output_version"], "3.test")
            self.assertTrue((out_dir / "images" / "figure.png").is_file())
            markdown = (out_dir / "document.md").read_text(encoding="utf-8")
            self.assertIn("parser: mineru", markdown)
            self.assertIn("![figure](images/figure.png)", markdown)
            elements = (out_dir / "elements.jsonl").read_text(encoding="utf-8")
            self.assertIn("Hello MinerU", elements)

            normalized_again = normalize_outputs(
                document,
                out_dir,
                out_dir / "raw",
                parsed_at="2026-07-16T00:01:00+00:00",
            )
            self.assertEqual(normalized_again["copied_image_count"], 1)
            self.assertEqual(len(list((out_dir / "images").iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
