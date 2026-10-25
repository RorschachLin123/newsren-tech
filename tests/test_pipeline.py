"""Offline end-to-end run with a scripted LLM and fixed sources: alert -> editorial approval -> dedup.

python3 -m unittest discover tests
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from newsren import brief, follow, sources, store

VENDOR = {"id": "v1", "url": "https://openai.com/index/model-x", "title": "Introducing Model X",
          "published": "2026-10-24T10:00:00Z", "retrieved_at": "2026-10-25T10:00:00Z",
          "publisher": "OpenAI News", "domain": "openai.com", "source_type": "vendor",
          "summary": "Model X scores 80% on BenchY.", "via": "feed:openai"}
EVAL = {"id": "e1", "url": "https://lmarena.ai/blog/model-x", "title": "Model X on BenchY: our results",
        "published": "2026-10-25T08:00:00Z", "retrieved_at": "2026-10-25T10:00:00Z",
        "publisher": "lmarena.ai", "domain": "lmarena.ai", "source_type": "independent_eval",
        "summary": "Independent BenchY run for Model X.", "via": "search:hackernews"}
TEXT = {VENDOR["url"]: "Today we release Model X. Model X scores 80% on BenchY.",
        EVAL["url"]: "We ran BenchY ourselves. In our tests Model X scored 79.5% on BenchY."}


class ScriptedLLM:
    def __init__(self, max_calls=20, **_):
        self.calls, self.cost_usd = 0, 0.0

    def json(self, prompt):
        self.calls += 1
        if "Candidate items" in prompt:
            return {"items": [{"id": "v1", "relevant": True, "reason": "new model claim", "claims": [
                {"text": "Model X scores 80% on BenchY", "originator": "OpenAI", "originator_domain": "openai.com",
                 "importance": 3, "same_as": None, "search_queries": ["Model X BenchY"]}]}]}
        if "Evidence items" in prompt:
            return {"evidence": [
                {"id": "E1", "stance": "supports", "basis": "reports_originator", "same_org_as_originator": True,
                 "quote": "Model X scores 80% on BenchY."},
                {"id": "E2", "stance": "supports", "basis": "own_testing", "same_org_as_originator": False,
                 "quote": "In our tests Model X scored 79.5% on BenchY."}],
                "note": "An independent run reproduces the score within 0.5 points."}
        if "alerted only when" in prompt:
            return {"matches": [{"claim_id": json.loads(prompt.split("labels:\n", 1)[1].split("\n\nWhich")[0])[0]["id"],
                                 "why": "independent benchmark result"}]}
        if "news briefing" in prompt:
            return {"headline": "Independent BenchY run confirms Model X score",
                    "what_changed": "OpenAI says Model X scores 80% on BenchY [1]. LMArena measured 79.5% [2]. "
                                    "Analysts expect more [9].",
                    "why_it_matters": "The vendor number holds up [2].", "watch_next": ["Other benchmarks"]}
        raise AssertionError("unexpected prompt")


class Pipeline(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "topics").mkdir()
        topic = json.loads((store.TOPICS / "independent-model-evals.json").read_text())
        (tmp / "topics" / "independent-model-evals.json").write_text(json.dumps(topic))
        patches = [mock.patch.object(store, "DATA", tmp / "data"), mock.patch.object(store, "TOPICS", tmp / "topics"),
                   mock.patch.object(brief, "OUT", tmp / "out"), mock.patch.object(follow, "LLM", ScriptedLLM),
                   mock.patch.object(sources, "discover", lambda *a: [dict(VENDOR)]),
                   mock.patch.object(sources, "search_evidence", lambda *a: [dict(EVAL)]),
                   mock.patch.object(sources, "fetch_text", lambda url, **k: TEXT.get(url))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.out = tmp / "out"

    def test_alert_review_and_dedup(self):
        rec = follow.run("independent-model-evals", log=lambda *a: None)
        self.assertTrue(rec["alert"])
        report = Path(rec["report"]).read_text()
        self.assertIn("Independently verified", report)
        self.assertIn("DRAFT, pending editorial review", report)
        self.assertNotIn("[9]", report)  # invented citation stripped
        self.assertIn("https://lmarena.ai/blog/model-x", report)

        ledger = store.Ledger("independent-model-evals")
        (b,) = ledger.data["briefings"]
        self.assertEqual(b["status"], "pending_review")
        self.assertFalse((self.out / "alerts.jsonl").exists())  # nothing delivered before review
        brief.approve(ledger, "independent-model-evals", b["id"], "editor")
        self.assertEqual(len((self.out / "alerts.jsonl").read_text().splitlines()), 1)

        again = follow.run("independent-model-evals", log=lambda *a: None)
        self.assertEqual(again["new_items"], 0)  # same item is not news twice
        self.assertFalse(again["alert"])


if __name__ == "__main__":
    unittest.main()
