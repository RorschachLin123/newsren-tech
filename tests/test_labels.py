"""The verification label is rule-based, so it can be tested without an LLM.

python3 -m unittest discover tests
"""
import unittest

from newsren.verify import label


def ev(domain, stance="supports", basis="own_testing", same_org=False, quote_found=True,
       source_type="independent_eval", approved=True):
    return {"domain": domain, "stance": stance, "basis": basis, "same_org": same_org,
            "quote_found": quote_found, "source_type": source_type, "approved_domain": approved}


class LabelRules(unittest.TestCase):
    def test_vendor_only(self):
        self.assertEqual(label([ev("openai.com", same_org=True, source_type="vendor")], "openai.com"),
                         "originator_claim_only")

    def test_press_repeating_vendor_is_not_verification(self):
        e = [ev("openai.com", same_org=True, source_type="vendor"),
             ev("theverge.com", basis="reports_originator", source_type="press")]
        self.assertEqual(label(e, "openai.com"), "originator_claim_only")

    def test_independent_testing_verifies(self):
        e = [ev("openai.com", same_org=True, source_type="vendor"), ev("lmarena.ai")]
        self.assertEqual(label(e, "openai.com"), "independently_verified")

    def test_independent_contradiction_wins(self):
        e = [ev("lmarena.ai"), ev("arxiv.org", stance="contradicts", source_type="paper")]
        self.assertEqual(label(e, "openai.com"), "disputed")

    def test_quote_not_found_does_not_count(self):
        self.assertEqual(label([ev("lmarena.ai", quote_found=False)], "openai.com"), "originator_claim_only")

    def test_subdomain_of_originator_is_not_independent(self):
        self.assertEqual(label([ev("platform.openai.com")], "openai.com"), "originator_claim_only")

    def test_hacker_news_discussion_does_not_count(self):
        self.assertEqual(label([ev("news.ycombinator.com", source_type="discussion")], "openai.com"),
                         "originator_claim_only")

    def test_unapproved_domain_does_not_count(self):
        self.assertEqual(label([ev("random.blog", source_type="unknown", approved=False)], "openai.com"),
                         "originator_claim_only")

    def test_nothing_relevant(self):
        self.assertEqual(label([ev("lmarena.ai", stance="irrelevant")], "openai.com"), "unverified")


if __name__ == "__main__":
    unittest.main()
