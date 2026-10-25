"""Claim extraction and evidence verification (the "Launch Reality Check").

The LLM reads and classifies evidence; the final label is computed here, in code, by fixed rules:
a claim only counts as independently verified when a source that is not the claim's originator
reports its own testing, and the quote it gave can be found in that source's text.
"""
import json
import re

from . import sources

LABELS = {  # label -> rank used by topic thresholds
    "independently_verified": 3,
    "disputed": 3,
    "originator_claim_only": 1,
    "unverified": 0,
}


def triage(llm, topic, candidates, prior_claims):
    """One LLM call: which candidates matter for the reader's question, and what checkable claims they make."""
    listing = [{k: c[k] for k in ("id", "title", "publisher", "source_type", "published", "summary")}
               for c in candidates]
    prompt = f"""Reader's question: {topic['question']}

Candidate items (retrieved from approved sources):
{json.dumps(listing, indent=1, ensure_ascii=False)}

Claims already tracked for this reader (from earlier runs):
{json.dumps(prior_claims, indent=1, ensure_ascii=False)}

For each candidate, decide if it is relevant to the reader's question. For relevant ones, extract at most
2 specific, checkable factual claims (e.g. "Model X scores 85% on benchmark Y", "Lab Z released model X").
Skip opinions and marketing adjectives. If a claim is the same as an already tracked claim, set same_as to
that claim's id. For each claim give 1-2 short search queries (2-4 words, most specific name first, e.g.
"Kimi K3.1 benchmark") that would find independent evidence for or against it.

Return JSON:
{{"items": [{{"id": "<candidate id>", "relevant": true, "reason": "<one line>",
  "claims": [{{"text": "...", "originator": "<organization making the claim>",
    "originator_domain": "<their website domain, e.g. openai.com, or null>",
    "importance": 1-3, "same_as": "<tracked claim id or null>", "search_queries": ["..."]}}]}}]}}"""
    return llm.json(prompt).get("items", [])


def gather_evidence(claim, origin, pool, log, limit=6):
    """Collect evidence from the approved search tools plus this run's other candidates."""
    searched = []
    for q in claim.get("search_queries", [])[:2]:
        searched += sources.search_evidence(q, log)
    words = set(_words(claim["text"]))
    unique, seen = [], set()
    # Search hits were already targeted at the claim, so one shared term is enough; other items need two.
    for need, items in ((0, [origin]), (1, searched), (2, pool)):
        for it in items:
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            overlap = len(words & set(_words(it["title"] + " " + it["summary"])))
            if overlap >= need:
                unique.append((it is origin, overlap, it))
    unique.sort(key=lambda t: (t[0], t[1]), reverse=True)
    evidence = []
    for n, (_, _, it) in enumerate(unique[:limit], 1):
        text = sources.fetch_text(it["url"]) or ""
        approved, _ = sources.classify(it["url"])
        evidence.append(it | {"eid": f"E{n}", "approved_domain": approved,
                              "text": text or it["summary"], "full_text": bool(text)})
    return evidence


def judge(llm, claim, evidence):
    """One LLM call per claim: how each evidence item relates to the claim."""
    shown = [{"id": e["eid"], "publisher": e["publisher"], "domain": e["domain"],
              "source_type": e["source_type"], "published": e["published"], "title": e["title"],
              "text": e["text"][:3500]} for e in evidence]
    prompt = f"""Claim: {claim['text']}
Claim made by: {claim['originator']} ({claim.get('originator_domain') or 'domain unknown'})

Evidence items:
{json.dumps(shown, indent=1, ensure_ascii=False)}

For every evidence item decide:
- stance: "supports", "contradicts" or "irrelevant" (irrelevant if it is about something else)
- basis: "own_testing" (the source ran its own evaluation or measurement), "reports_originator" (it repeats
  what the claim's originator said), "opinion", or "unclear"
- same_org_as_originator: true if the source is published by the organization that made the claim
- quote: an exact sentence copied from the item's text that shows the stance (empty if irrelevant)

Then write "note": one or two sentences on what could and could not be verified.

Return JSON: {{"evidence": [{{"id": "E1", "stance": "...", "basis": "...", "same_org_as_originator": false, "quote": "..."}}], "note": "..."}}"""
    verdict = llm.json(prompt)
    by_id = {v.get("id"): v for v in verdict.get("evidence", [])}
    for e in evidence:
        v = by_id.get(e["eid"], {})
        e["stance"] = v.get("stance", "irrelevant")
        e["basis"] = v.get("basis", "unclear")
        e["same_org"] = bool(v.get("same_org_as_originator"))
        e["quote"] = v.get("quote", "")
        e["quote_found"] = bool(e["quote"]) and _norm(e["quote"]) in _norm(e["text"])
    return verdict.get("note", "")


def is_independent(e, originator_domain):
    """A source independent of whoever made the claim, from an approved domain, that is not just discussion."""
    same_domain = bool(originator_domain) and (e["domain"] == originator_domain
                                               or e["domain"].endswith("." + originator_domain))
    return (not e["same_org"] and not same_domain and e.get("approved_domain", True)
            and e["source_type"] not in ("discussion", "unknown"))


def label(evidence, originator_domain):
    """Deterministic evidence label for a claim."""
    strong = [e for e in evidence if e["stance"] != "irrelevant" and e["basis"] == "own_testing"
              and e["quote_found"] and is_independent(e, originator_domain)]
    if any(e["stance"] == "contradicts" for e in strong):
        return "disputed"
    if any(e["stance"] == "supports" for e in strong):
        return "independently_verified"
    if any(e["stance"] == "supports" for e in evidence):
        return "originator_claim_only"
    return "unverified"


def verify_claim(llm, claim, origin, pool, log):
    evidence = gather_evidence(claim, origin, pool, log)
    note = judge(llm, claim, evidence)
    return label(evidence, claim.get("originator_domain")), evidence, note


def _words(s):
    stop = {"the", "and", "for", "with", "that", "this", "from", "model", "models", "new", "are", "its", "has"}
    return [w for w in re.findall(r"[a-z0-9][a-z0-9.\-]+", s.lower()) if len(w) > 2 and w not in stop]


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
