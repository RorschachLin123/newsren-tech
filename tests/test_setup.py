"""Offline tests for the reproducibility pieces: .env loading, provider selection, the OpenAI-compatible
backend (DeepSeek) and the local web page. No network, no LLM calls.

python3 -m unittest discover tests
"""
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from newsren import llm, store, web


class EnvTest(unittest.TestCase):
    def test_load_env_reads_values_and_shell_wins(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("# comment\nNEWSREN_LLM=deepseek\nDEEPSEEK_API_KEY='sk-test'\nEMPTY=\nNEWSREN_MODEL=from-file\n")
            with mock.patch.dict(os.environ, {"NEWSREN_MODEL": "from-shell"}, clear=True):
                self.assertTrue(store.load_env(p))
                self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "sk-test")  # quotes stripped
                self.assertEqual(os.environ["NEWSREN_MODEL"], "from-shell")
                self.assertNotIn("EMPTY", os.environ)

    def test_missing_env_file_is_fine(self):
        self.assertFalse(store.load_env(Path("/nonexistent/.env")))


class ProviderTest(unittest.TestCase):
    def test_deepseek_is_picked_when_its_key_is_set(self):
        cfg = llm.settings({"DEEPSEEK_API_KEY": "k"})
        self.assertEqual((cfg["provider"], cfg["model"], cfg["base_url"]),
                         ("deepseek", "deepseek-flash", "https://api.deepseek.com"))

    def test_defaults_and_aliases(self):
        self.assertEqual(llm.settings({})["provider"], "claude-cli")
        self.assertEqual(llm.settings({"NEWSREN_LLM": "anthropic-api"})["provider"], "anthropic")
        with self.assertRaises(SystemExit):
            llm.settings({"NEWSREN_LLM": "nope"})

    def test_overrides(self):
        cfg = llm.settings({"NEWSREN_LLM": "openai-compatible", "NEWSREN_BASE_URL": "https://x.test/v1/",
                            "NEWSREN_API_KEY": "k", "NEWSREN_MODEL": "m", "NEWSREN_PRICE_IN": "1", "NEWSREN_PRICE_OUT": "2"})
        self.assertEqual((cfg["base_url"], cfg["model"], cfg["price"]), ("https://x.test/v1", "m", (1.0, 2.0)))

    def test_missing_settings_stop_with_a_clear_message(self):
        with mock.patch.dict(os.environ, {"NEWSREN_LLM": "openai-compatible"}, clear=True):
            with self.assertRaises(SystemExit) as e:
                llm.LLM().json("hi")
        self.assertIn("NEWSREN_API_KEY", str(e.exception))

    def test_deepseek_request_and_cost_meter(self):
        sent = {}

        def fake_post(url, body, headers):
            sent.update(url=url, body=body, headers=headers)
            return {"choices": [{"message": {"content": '```json\n{"ok": true}\n```'}}],
                    "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 500_000}}

        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}, clear=True), \
                mock.patch.object(llm, "_post", fake_post):
            client = llm.LLM()
            self.assertEqual(client.json("prompt"), {"ok": True})
        self.assertEqual(sent["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(sent["headers"]["Authorization"], "Bearer sk-test")
        self.assertEqual(sent["body"]["thinking"], {"type": "disabled"})
        self.assertEqual(sent["body"]["messages"][0]["role"], "system")
        self.assertAlmostEqual(client.cost_usd, 0.30 + 0.60)
        self.assertEqual(llm.label(client), "deepseek/deepseek-flash")

    def test_anthropic_cost_meter(self):
        def fake_post(url, body, headers):
            self.assertEqual(headers["x-api-key"], "k")
            return {"content": [{"text": "[1, 2]"}], "usage": {"input_tokens": 1_000_000, "output_tokens": 100_000}}

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}, clear=True), \
                mock.patch.object(llm, "_post", fake_post):
            client = llm.LLM()
            self.assertEqual(client.json("prompt"), [1, 2])
        self.assertAlmostEqual(client.cost_usd, 2.0 + 1.0)


class MarkdownTest(unittest.TestCase):
    def test_tables_lists_and_allowed_tags(self):
        out = web.markdown("# T\n\n| A | B |\n|---|---|\n| **x** | y |\n\n4. first\n5. second\n"
                           "   <details><summary>Answer</summary>yes</details>\n\n<script>alert(1)</script>")
        self.assertIn("<h1>T</h1>", out)
        self.assertIn("<th>A</th>", out)
        self.assertIn("<td><strong>x</strong></td>", out)
        self.assertIn('<li value=4>first</li>', out)  # source numbers kept for [n] citations
        self.assertIn("<details><summary>Answer</summary>yes</details>", out)
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)


class WebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "out" / "t").mkdir(parents=True)
        (root / "out" / "t" / "2026-10-25_000000_ALERT.md").write_text("# Hello\n")
        (root / "secret.md").write_text("nope")
        topic = {"id": "t", "question": "Q?", "alert_when": "W", "min_evidence": "independently_verified"}
        self.patches = [mock.patch.object(web, "OUT", root / "out"),
                        mock.patch.object(store, "DATA", root / "data"),
                        mock.patch.object(web, "list_topics", lambda: ["t"]),
                        mock.patch.object(web, "load_topic", lambda t: topic),
                        mock.patch.dict(os.environ, {}, clear=True)]
        for p in self.patches:
            p.start()
        self.server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        self.server.RequestHandlerClass.log_message = lambda *a: None
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_topic_page_and_report(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("Q?", body)
        self.assertIn("2026-10-25_000000_ALERT.md", body)
        status, body = self.get("/view?topic=t&file=2026-10-25_000000_ALERT.md")
        self.assertEqual(status, 200)
        self.assertIn("<h1>Hello</h1>", body)

    def test_only_listed_reports_can_be_read(self):
        self.assertEqual(self.get("/view?topic=t&file=../../secret.md")[0], 404)
        self.assertEqual(self.get("/view?topic=../t&file=x.md")[0], 404)


if __name__ == "__main__":
    unittest.main()
