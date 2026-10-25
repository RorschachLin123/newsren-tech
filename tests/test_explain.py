"""Offline tests for Ask Newsren and News-to-Learning with a scripted LLM and a fixed ledger.

python3 -m unittest discover tests
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from newsren import evals, explain, sources, store


def evidence(url, publisher, stance, basis, quote):
    return {"eid": "E1", "url": url, "via": "feed", "title": publisher + " page", "publisher": publisher,
            "domain": sources.domain_of(url), "source_type": "independent_eval", "published": "2026-10-24T00:00:00Z",
            "retrieved_at": "2026-10-25T00:00:00Z", "stance": stance, "basis": basis, "same_org": False,
            "quote": quote, "quote_found": True, "approved_domain": True}


LEDGER = {
    "seen": {}, "runs": [],
    "claims": {
        "C1": {"text": "Epoch AI measured Model X at 0.93 on GPQA Diamond", "originator": "Epoch AI",
               "developer": "Lab", "label": "independently_verified", "note": "",
               "evidence": [evidence("https://logs.epoch.ai/1", "Epoch AI", "supports", "own_testing", "0.93")]},
        "C2": {"text": "Lab says Model X beats everyone", "originator": "Lab", "developer": "Lab",
               "label": "originator_claim_only", "note": "only the lab says so",
               "evidence": [evidence("https://openai.com/x", "Lab", "supports", "reports_originator", "beats")]},
    },
    "briefings": [{"id": "B1", "path": "x", "claims": ["C1", "C2"], "status": "approved", "created_at": "2026"}],
}


class ScriptedLLM:
    prompts = []
    lookup = False

    def __init__(self, max_calls=20, **_):
        self.calls, self.cost_usd = 0, 0.0

    def json(self, prompt):
        self.calls += 1
        ScriptedLLM.prompts.append(prompt)
        if prompt.startswith("A reader asks"):
            return {"claim_ids": ["C1", "C2", "C9"], "needs_lookup": ScriptedLLM.lookup,
                    "models": ["Model X", "Model Y"], "search_queries": ["model x"]}
        if prompt.startswith("Answer the reader"):
            return {"answer": "Epoch measured 0.93 [1]. The lab's own claim is unverified [2]. Rumour [7].",
                    "could_not_verify": ["Whether the lab's claim holds"], "follow_up_questions": []}
        if prompt.startswith("Turn verified"):
            return {"title": "What a benchmark is", "explainer": "Epoch measured 0.93 [1]. Also [2].",
                    "glossary": [{"term": "Benchmark", "meaning": "A fixed test."}],
                    "check_your_understanding": [{"question": "Who ran the test?", "answer": "Epoch AI [1]"}],
                    "summary_for_teacher": "Summary [1].", "discussion_questions": ["Why test independently?"],
                    "technical_summary": "Tech [1].", "implications": ["Supported: ..."], "caveats": ["One run"]}
        raise AssertionError("unexpected prompt")


class Explain(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "topics").mkdir()
        (tmp / "topics" / "t.json").write_text(json.dumps({"id": "t", "question": "Q?", "audience": "professional"}))
        (tmp / "data" / "t").mkdir(parents=True)
        (tmp / "data" / "t" / "ledger.json").write_text(json.dumps(LEDGER))
        for p in [mock.patch.object(store, "DATA", tmp / "data"), mock.patch.object(store, "TOPICS", tmp / "topics"),
                  mock.patch.object(explain, "OUT", tmp / "out"), mock.patch.object(explain, "LLM", ScriptedLLM),
                  mock.patch.object(evals, "lookup", lambda *a: [])]:
            p.start()
            self.addCleanup(p.stop)
        ScriptedLLM.prompts = []
        ScriptedLLM.lookup = False
        self.ledger_file = tmp / "data" / "t" / "ledger.json"

    def test_ask_cites_only_real_sources_and_keeps_labels(self):
        path, out = explain.ask("t", "Is Model X really that good?", log=lambda *a: None)
        text = path.read_text()
        self.assertIn("[1]", out["answer"])
        self.assertNotIn("[7]", text)  # invented citation stripped
        self.assertIn("Only the originator's claim", ScriptedLLM.prompts[1])  # label passed to the model
        self.assertIn("https://logs.epoch.ai/1", text)
        self.assertEqual(json.loads(self.ledger_file.read_text()), LEDGER)  # ask never writes the ledger

    def test_ask_lookup_keeps_every_eval_row(self):
        ScriptedLLM.lookup = True
        rows = [{"url": f"https://artificialanalysis.ai/models/m{i}", "title": f"AA row {i}", "summary": "",
                 "text": f"row {i}", "publisher": "Artificial Analysis", "domain": "artificialanalysis.ai",
                 "source_type": "independent_eval", "published": None, "retrieved_at": "2026-10-25T00:00:00Z",
                 "via": "data:artificialanalysis"} for i in range(8)]
        hits = [dict(r, url=f"https://news.ycombinator.com/item?id={i}", title=f"HN {i}", via="search:hackernews",
                     source_type="discussion") for i, r in enumerate(rows)]
        with mock.patch.object(evals, "lookup", lambda *a, **k: rows), \
                mock.patch.object(sources, "search_evidence", lambda *a: hits), \
                mock.patch.object(sources, "fetch_text", lambda url, **k: "page"):
            path, _ = explain.ask("t", "How does Model X compare to Model Y?", log=lambda *a: None)
        text = path.read_text()
        self.assertEqual(sum(f"AA row {i}]" in text for i in range(8)), 8)  # none cut
        self.assertEqual(text.count("HN "), 3)  # search hits still capped (3 per query)

    def test_learn_uses_only_verified_claims(self):
        paths = explain.learn("t", "B1", log=lambda *a: None)
        self.assertEqual(len(paths), 3)
        prompt = ScriptedLLM.prompts[0]
        self.assertIn("Epoch AI measured Model X", prompt)
        self.assertNotIn("beats everyone", prompt)  # unverified claim is not taught as fact
        student = paths[0].read_text()
        self.assertIn("**Benchmark**", student)
        self.assertNotIn("[2]", student)  # [2] would point at the excluded claim's source
        self.assertIn("<details>", student)

    def test_learn_prompt_reads_error_bars_as_standard_errors(self):
        explain.learn("t", "B1", levels=("student",), log=lambda *a: None)
        prompt = ScriptedLLM.prompts[0]
        self.assertIn("one standard error", prompt)
        self.assertIn("two times the combined error", prompt)
        self.assertIn("ranges do not overlap", prompt)  # the loose reading is named and ruled out


if __name__ == "__main__":
    unittest.main()
