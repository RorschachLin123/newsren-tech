"""Evaluation lookups must not let one source crowd out another (offline, no network or model calls).

Regression for the 2026-10-25 live Ask run: four Epoch rows for Claude Opus 5.5 filled the cap, so the
Artificial Analysis row for Opus 5.5 was dropped and the answer said no Opus figures were available.

python3 -m unittest discover tests
"""
import unittest
from unittest import mock

from newsren import evals, sources, verify


def row(src, model, n=0):
    return {"url": f"https://{src}.test/{evals.norm(model)}/{n}", "title": f"{src}: {model} #{n}",
            "summary": f"{model} result", "text": f"{src} measured {model} (row {n}).",
            "source_type": "independent_eval", "via": f"data:{src}"}


FAKE = {  # source -> model -> number of rows it holds
    "epoch": {"Claude Opus 5.5": 4},
    "lmarena": {"Claude Opus 5.5": 1},
    "artificialanalysis": {"Kimi K3.1 Pro": 1, "Claude Opus 5.5": 2},
}


def fake_lookups():
    return tuple((src, lambda m, src=src: [row(src, m, n) for n in range(FAKE[src].get(m, 0))]) for src in FAKE)


class Lookup(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(evals, "LOOKUPS", fake_lookups())
        p.start()
        self.addCleanup(p.stop)

    def test_one_row_per_model_per_source_comes_first(self):
        rows = evals.lookup(["Kimi K3.1 Pro", "Claude Opus 5.5"], print, extra=0)
        titles = [r["title"] for r in rows]
        self.assertEqual(titles, ["artificialanalysis: Kimi K3.1 Pro #0", "epoch: Claude Opus 5.5 #0",
                                  "lmarena: Claude Opus 5.5 #0", "artificialanalysis: Claude Opus 5.5 #0"])

    def test_extra_rows_are_round_robin(self):
        rows = evals.lookup(["Claude Opus 5.5"], print, extra=2)
        self.assertEqual([r["title"] for r in rows[3:]], ["epoch: Claude Opus 5.5 #1",
                                                         "artificialanalysis: Claude Opus 5.5 #1"])

    def test_failing_source_is_skipped(self):
        def boom(m):
            raise OSError("HTTP 500")
        errors = []
        with mock.patch.object(evals, "LOOKUPS", fake_lookups()[:2] + (("artificialanalysis", boom),)):
            rows = evals.lookup(["Claude Opus 5.5"], errors.append)
        self.assertEqual(len(rows), 4)
        self.assertIn("HTTP 500", errors[0])

    def test_verifier_keeps_every_eval_row_despite_many_other_candidates(self):
        claim = {"text": "Kimi K3.1 Pro matches Claude Opus 5.5", "models": ["Kimi K3.1 Pro", "Claude Opus 5.5"],
                 "search_queries": []}
        origin = {"url": "https://kimi.ai/blog", "title": "Kimi K3.1 Pro", "summary": "", "text": "Kimi K3.1 Pro.",
                  "source_type": "vendor"}
        pool = [{"url": f"https://simonwillison.net/{i}", "title": "Kimi K3.1 Pro vs Claude Opus 5.5",
                 "summary": "kimi opus", "source_type": "independent_commentary"} for i in range(10)]
        with mock.patch.object(sources, "fetch_text", lambda url, **k: "page"):
            ev = verify.gather_evidence(claim, origin, pool, print, limit=3)
        urls = [e["url"] for e in ev]
        self.assertIn("https://artificialanalysis.test/claudeopus55/0", urls)
        self.assertIn("https://artificialanalysis.test/kimik31pro/0", urls)
        self.assertEqual(sum("simonwillison" in u for u in urls), 3)  # the cap still applies to the rest


if __name__ == "__main__":
    unittest.main()
