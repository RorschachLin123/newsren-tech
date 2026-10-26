"""Offline tests for the Paper Decoder and the Evolution Timeline (no network, scripted LLM).

python3 -m unittest discover tests
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from newsren import papers, store, timeline, web


def ev(url, publisher, stance, basis, same_org, published, source_type="independent_eval"):
    return {"url": url, "title": publisher + " page", "publisher": publisher, "domain": url.split("/")[2],
            "source_type": source_type, "published": published, "retrieved_at": "2026-10-25T18:00:00Z",
            "stance": stance, "basis": basis, "same_org": same_org, "quote": "q"}


class FakeLedger:
    def __init__(self, data):
        self.data = data


LEDGER = {"seen": {}, "runs": [], "claims": {
    "C1": {"text": "Epoch measured X at 0.9", "developer": "Lab", "label": "disputed", "first_seen": "2026-10-20T10:00:00Z",
           "history": [{"at": "2026-10-20T10:00:00Z", "label": "originator_claim_only"},
                       {"at": "2026-10-22T10:00:00Z", "label": "originator_claim_only"},
                       {"at": "2026-10-24T10:00:00Z", "label": "disputed"}],
           "evidence": [ev("https://lab.com/blog", "Lab", "supports", "reports_originator", True, "2026-10-15T09:00:00Z", "vendor"),
                        ev("https://lab.com/card", "Lab", "irrelevant", "unclear", True, "2026-10-14T09:00:00Z", "vendor"),
                        ev("https://epoch.ai/x", "Epoch AI", "contradicts", "own_testing", False, "2026-10-18T00:00:00Z"),
                        ev("https://news.com/a", "News", "supports", "reports_originator", False, "2026-10-16T00:00:00Z", "press"),
                        ev("https://other.ai/y", "Other", "irrelevant", "own_testing", False, None)]},
    "C2": {"text": "Lab says X is fast", "developer": "Lab", "label": "originator_claim_only",
           "first_seen": "2026-10-20T10:00:00Z", "history": [{"at": "2026-10-20T10:00:00Z", "label": "originator_claim_only"}],
           "evidence": [ev("https://lab.com/blog", "Lab", "supports", "reports_originator", True, "2026-10-15T09:00:00Z", "vendor")]}},
    "briefings": [{"id": "B1", "path": "/x/out/t/2026_ALERT.md", "claims": ["C1"], "status": "approved",
                   "created_at": "2026-10-24T10:00:01Z", "approved_at": "2026-10-24T12:00:00Z", "approved_by": "Ed"}]}


class Timeline(unittest.TestCase):
    def setUp(self):
        self.story = timeline.build(FakeLedger(LEDGER))[0]
        self.kinds = [(e["kind"], e.get("url") or e["title"]) for e in self.story["events"]]

    def test_order_kinds_and_dedup(self):
        urls = [u for k, u in self.kinds if u.startswith("http")]
        self.assertEqual(urls, ["https://lab.com/card", "https://lab.com/blog", "https://news.com/a", "https://epoch.ai/x"])
        kinds = dict((u, k) for k, u in self.kinds)
        self.assertEqual(kinds["https://lab.com/card"], "developer")  # the release itself, kept as context
        self.assertEqual(kinds["https://news.com/a"], "repeat")
        self.assertEqual(kinds["https://epoch.ai/x"], "independent")
        self.assertNotIn("https://other.ai/y", urls)  # irrelevant and not the developer: dropped
        blog = next(e for e in self.story["events"] if e.get("url") == "https://lab.com/blog")
        self.assertEqual(sorted(blog["supports"]), ["C1", "C2"])  # one node for a source used by two claims

    def test_label_changes_only_when_the_label_changes(self):
        labels = [t for k, t in self.kinds if k == "label"]
        self.assertEqual(len(labels), 3)  # C1 tracked, C1 changed, C2 tracked; the repeated label is skipped
        self.assertTrue(any("→ Disputed" in t for t in labels))

    def test_briefing_events_and_lag(self):
        self.assertIn(("briefing", "Briefing B1 approved by Ed and delivered"), self.kinds)
        self.assertEqual(self.story["summary"]["lag_days"], 3.6)  # 14 Oct 09:00 -> 18 Oct 00:00
        self.assertIn("3.6 days later", timeline.summary_line(self.story))
        md = timeline.markdown({"question": "Q?"}, [self.story])
        self.assertIn("[Lab page](https://lab.com/card)", md)
        self.assertIn("**2026-10-18** · Independent evaluation", md)  # day-only dates shown without a time

    def test_no_llm(self):
        with mock.patch("newsren.llm.LLM", side_effect=AssertionError("no LLM")):
            timeline.build(FakeLedger(LEDGER))


PAPER = {"id": "2610.00001", "url": "https://arxiv.org/abs/2610.00001", "title": "Testing Benchmarks",
         "authors": ["A", "B", "C", "D"], "published": "2026-10-23T00:00:00Z", "updated": "2026-10-24T00:00:00Z",
         "abstract": "We test.", "categories": ["cs.CL"], "journal_ref": None, "comment": None,
         "retrieved_at": "2026-10-25T00:00:00Z"}


class ScriptedLLM:
    prompts = []

    def __init__(self, max_calls=20, **_):
        self.max_calls, self.calls, self.cost_usd = max_calls, 0, 0.0
        self.backend, self.model = "test", "scripted"

    def json(self, prompt):
        self.calls += 1
        ScriptedLLM.prompts.append(prompt)
        return {"one_line": "Tests benchmarks [1].", "hypothesis": "Benchmarks leak [1].", "method": "Ran 3 models [2].",
                "findings": ["Scores drop 5 points [2]", "Invented [9]"], "limitations": ["(authors) small [2]"],
                "possible_applications": ["(our reading) audits"], "relevance_to_topic": "Relevant [1].",
                "terms": [{"term": "Leakage", "meaning": "Test data seen in training."}]}


class PaperDecoder(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for p in [mock.patch.object(papers, "OUT", self.tmp), mock.patch.object(papers, "LLM", ScriptedLLM),
                  mock.patch.object(papers, "fetch", lambda pid: dict(PAPER)),
                  mock.patch.object(papers, "full_text", lambda pid: "full text " * 400)]:
            p.start()
            self.addCleanup(p.stop)
        ScriptedLLM.prompts = []
        self.topic = {"id": "t", "question": "Do independent evals confirm claims?", "arxiv_queries": ["q"]}

    def test_parse_id(self):
        for s in ("2610.00001", "arXiv:2610.00001v2", "https://arxiv.org/abs/2610.00001v3",
                  "https://arxiv.org/pdf/2610.00001", "https://www.arxiv.org/html/2610.00001v1"):
            self.assertEqual(papers.parse_id(s), "2610.00001", s)
        self.assertEqual(papers.parse_id("https://arxiv.org/abs/hep-th/9901001v1"), "hep-th/9901001")
        self.assertIsNone(papers.parse_id("https://evil.example/abs/2610.00001"))
        self.assertIsNone(papers.parse_id("hello"))

    def test_decode_one_call_cited_and_marked_preprint(self):
        path, out = papers.decode(self.topic, "https://arxiv.org/abs/2610.00001", "student", log=lambda *a: None)
        text = path.read_text()
        self.assertEqual(len(ScriptedLLM.prompts), 1)
        self.assertIn("high-school", ScriptedLLM.prompts[0])  # audience reaches the prompt
        self.assertIn("[2] Full text", ScriptedLLM.prompts[0])
        self.assertNotIn("[9]", text)  # invented citation stripped
        self.assertIn("preprint, not peer reviewed", text)
        for h in ("### Hypothesis", "### Method", "### Findings", "### Limitations", "### Possible applications"):
            self.assertIn(h, text)
        self.assertIn("https://arxiv.org/html/2610.00001", text)
        self.assertTrue(path.name.endswith("_paper_2610.00001.md"))
        self.assertEqual(web._kind(path.name), "paper")

    def test_abstract_only_and_journal_reference(self):
        with mock.patch.object(papers, "full_text", lambda pid: None), \
                mock.patch.object(papers, "fetch", lambda pid: PAPER | {"journal_ref": "ICSE 2027"}):
            path, _ = papers.decode(self.topic, "2610.00001", log=lambda *a: None)
        text = path.read_text()
        self.assertIn("Only the abstract is available", ScriptedLLM.prompts[0])
        self.assertIn("abstract only", text)
        self.assertNotIn("arxiv.org/html", text)
        self.assertIn("published version (ICSE 2027)", text)
        self.assertIn("preprint; the authors' comment", papers.review_status({"comment": "Accepted at NeurIPS"}))

    def test_bad_link_stops_before_any_call(self):
        with self.assertRaises(LookupError):
            papers.decode(self.topic, "https://example.com/paper.pdf", log=lambda *a: None)
        self.assertEqual(ScriptedLLM.prompts, [])

    def test_recommend_merges_ledger_and_search_without_llm(self):
        papers._cache.clear()
        ledger = FakeLedger({"claims": {"C": {"evidence": [
            ev("https://arxiv.org/abs/2609.11111v1", "arXiv preprint (Z)", "supports", "own_testing", False,
               "2026-09-30T00:00:00Z", "paper")]}}})
        hits = [{"url": "http://arxiv.org/abs/2610.22222v1", "title": "New", "published": "2026-10-23T00:00:00Z",
                 "publisher": "arXiv preprint (Y)", "summary": "s"},
                {"url": "http://arxiv.org/abs/2609.11111v2", "title": "Dup", "published": "2026-09-30T00:00:00Z",
                 "publisher": "arXiv preprint (Z)", "summary": "s"}]
        with mock.patch.object(papers.sources, "search_arxiv", lambda q, limit=8: hits), \
                mock.patch.object(papers, "LLM", side_effect=AssertionError("no LLM")):
            recs = papers.recommend(self.topic, ledger, log=lambda *a: None)
        self.assertEqual([r["id"] for r in recs], ["2610.22222", "2609.11111"])
        self.assertEqual(recs[1]["why"], "cited in a checked claim")


if __name__ == "__main__":
    unittest.main()
