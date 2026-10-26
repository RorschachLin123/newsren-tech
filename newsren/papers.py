"""Research Paper Decoder (plan section 3): one arXiv paper -> hypothesis, method, findings, limitations,
possible applications, for a chosen audience. 1 LLM call.

The paper is read from arXiv only (an approved domain): the API gives title, authors, dates, abstract and any
journal reference; the HTML full text is used when arXiv has one, otherwise the decoder says it read the
abstract only. The output is marked as a preprint (plan section 6), and limitations and applications are
labelled by who says them: the authors, or Newsren's reading.

recommend() lists recent papers for a topic from its arxiv_queries plus arXiv sources already in the ledger.
It calls the arXiv API only, never the LLM.
"""
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

from . import sources
from .brief import clean_citations, source_line
from .explain import LEVELS
from .llm import LLM, label as llm_label
from .store import OUT, iso, now

ARXIV = "{http://arxiv.org/schemas/atom}"
ID_RE = re.compile(r"(\d{4}\.\d{4,5}|[a-z][a-z-]*(?:\.[A-Z]{2})?/\d{7})(v\d+)?")
FULL_TEXT_CHARS = 30000
_cache = {}  # topic id -> (time, papers)


def parse_id(text):
    """arXiv id from an id, an abs/pdf/html link or 'arXiv:...'; the version is dropped. None if there is none."""
    text = (text or "").strip()
    url = urllib.parse.urlparse(text)
    if url.netloc and not sources.domain_of(text).endswith("arxiv.org"):
        return None
    m = ID_RE.search(url.path if url.netloc else text)
    return m.group(1) if m else None


def _entry(e):
    pid = parse_id((e.findtext(sources.ATOM + "id") or "").strip())
    return {"id": pid, "url": f"https://arxiv.org/abs/{pid}",
            "title": sources._clean(e.findtext(sources.ATOM + "title")),
            "authors": [a.findtext(sources.ATOM + "name") for a in e.findall(sources.ATOM + "author")],
            "published": e.findtext(sources.ATOM + "published"), "updated": e.findtext(sources.ATOM + "updated"),
            "abstract": sources._clean(e.findtext(sources.ATOM + "summary")),
            "categories": [c.get("term") for c in e.findall(sources.ATOM + "category")],
            "journal_ref": sources._clean(e.findtext(ARXIV + "journal_ref")) or None,
            "comment": sources._clean(e.findtext(ARXIV + "comment")) or None}


def fetch(paper_id):
    root = ET.fromstring(sources._get(
        "http://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": paper_id}), timeout=30))
    for e in root.iter(sources.ATOM + "entry"):
        p = _entry(e)
        if p["id"] and p["title"] and p["title"].lower() != "error":
            p["retrieved_at"] = iso(now())
            return p
    raise LookupError(f"arXiv has no paper {paper_id}")


def full_text(paper_id):
    """arXiv's HTML rendering of the paper, or None when there is none (older or PDF-only papers)."""
    text = sources.fetch_text(f"https://arxiv.org/html/{paper_id}", max_chars=FULL_TEXT_CHARS)
    if not text or len(text) < 3000 or "No HTML for" in text[:2000]:
        return None
    return text


def review_status(p):
    if p.get("journal_ref"):
        return (f"arXiv preprint; the authors list a published version ({p['journal_ref']}), "
                "not checked by Newsren")
    if re.search(r"(?i)\b(accepted|to appear|published)\b", p.get("comment") or ""):
        return f"preprint; the authors' comment says \"{p['comment']}\", not checked by Newsren"
    return "preprint, not peer reviewed"


def decode(topic, paper_ref, audience="professional", log=print):
    paper_id = parse_id(paper_ref)
    if not paper_id:
        raise LookupError(f"not an arXiv id or link: {paper_ref!r}")
    p = fetch(paper_id)
    body = full_text(paper_id)
    log(f"  {paper_id}: {p['title']} ({'full text' if body else 'abstract only'})")
    refs = [{"ref": 1, "title": p["title"], "url": p["url"], "publisher": f"arXiv ({', '.join(p['authors'][:3])}"
             f"{' et al.' if len(p['authors']) > 3 else ''})", "source_type": "paper", "domain": "arxiv.org",
             "published": p["published"], "retrieved_at": p["retrieved_at"], "via": "paper:abstract"}]
    if body:
        refs.append(refs[0] | {"ref": 2, "title": p["title"] + " (full text, HTML)",
                               "url": f"https://arxiv.org/html/{paper_id}", "via": "paper:html"})
    llm = LLM(max_calls=1)
    who = LEVELS.get(audience, LEVELS["professional"])[0]
    out = llm.json(f"""Decode a research paper for {who}. Plain, precise English. No hype.
Reader's topic, for the relevance note: {topic['question']}

[1] Title: {p['title']}
Authors: {', '.join(p['authors'])}
Submitted {p['published']}, last updated {p['updated']}. Categories: {', '.join(p['categories'])}.
Author comment: {p['comment'] or 'none'}. Journal reference: {p['journal_ref'] or 'none'}.
Abstract: {p['abstract']}
""" + (f"\n[2] Full text (may be cut off):\n{body}\n" if body else "\nOnly the abstract is available.\n") + f"""
Rules: use only the material above. Every factual sentence about the paper ends with [1] or [2].
Report what the authors claim as their claims ("the authors report"), not as established fact; this is a
preprint unless a journal reference says otherwise. For limitations and applications, say who says it:
start each item with "(authors)" if the paper states it, or "(our reading)" if it is your inference.
Applications that the paper does not test are speculative; say so. If only the abstract is available,
say what cannot be judged from it.
Return JSON: {{"one_line": "<one sentence: what the paper does>", "hypothesis": "<the question or claim tested>",
"method": "<how they tested it: data, models, setup, comparisons>", "findings": ["<with numbers where given>"],
"limitations": ["(authors) ...", "(our reading) ..."], "possible_applications": ["..."],
"relevance_to_topic": "<1-2 sentences, or 'Not directly relevant.'>",
"terms": [{{"term": "...", "meaning": "..."}}]}}""")
    problems = []
    out = clean_citations(out, {r["ref"] for r in refs}, problems.append)

    lines = [f"# Paper Decoder: {p['title']}", "",
             f"**{', '.join(p['authors'][:6])}{' et al.' if len(p['authors']) > 6 else ''}** · arXiv {paper_id} · "
             f"submitted {p['published'][:10]}, updated {p['updated'][:10]} · **{review_status(p)}**", "",
             f"Decoded for: {audience} · read {'abstract and full text' if body else 'abstract only (no HTML full text on arXiv)'}"
             f" · {iso(now())}", ""]
    if out.get("one_line"):
        lines += [f"*{out['one_line']}*", ""]
    for key, title in (("hypothesis", "Hypothesis"), ("method", "Method"), ("findings", "Findings"),
                       ("limitations", "Limitations"), ("possible_applications", "Possible applications"),
                       ("relevance_to_topic", "Relevance to your topic")):
        val = out.get(key)
        if not val:
            continue
        lines += [f"### {title}", ""] + ([val] if isinstance(val, str) else [f"- {x}" for x in val]) + [""]
    if out.get("terms"):
        lines += ["### Terms", ""] + [f"- **{t['term']}**: {t['meaning']}" for t in out["terms"]] + [""]
    lines += ["### Sources", ""] + [source_line(r) for r in refs]
    lines += ["", "---", "*Decoded by an AI research agent from the arXiv record above. Findings are the authors' "
              "claims and have not been independently checked; items marked (our reading) are the agent's "
              "inference. A preprint has not been through peer review.*",
              "", f"<sub>{llm.calls} LLM call ({llm_label(llm)}), ${llm.cost_usd:.3f}.</sub>", ""]
    for x in problems:
        log("  ! " + x)
    path = OUT / topic["id"] / f"{now().strftime('%Y-%m-%d_%H%M%S')}_paper_{paper_id.replace('/', '-')}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    log(f"  decoded -> {path}  ({llm.calls} LLM call, ${llm.cost_usd:.3f})")
    return path, out


def recommend(topic, ledger, limit=8, max_age=3600, log=print):
    """Recent arXiv papers for the topic (newest first) plus arXiv sources cited in the ledger. No LLM."""
    hit = _cache.get(topic["id"])
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    found = {}
    for c in ledger.data["claims"].values():
        for e in c.get("evidence", []):
            pid = parse_id(e["url"]) if e.get("domain", "").endswith("arxiv.org") else None
            if pid:
                found.setdefault(pid, {"id": pid, "title": e["title"], "published": e.get("published"),
                                       "authors": e.get("publisher", ""), "summary": "", "why": "cited in a checked claim"})
    for q in topic.get("arxiv_queries", []):
        try:
            for it in sources.search_arxiv(q, limit=limit):
                pid = parse_id(it["url"])
                if pid:
                    found.setdefault(pid, {"id": pid, "title": it["title"], "published": it["published"],
                                           "authors": it["publisher"], "summary": it["summary"],
                                           "why": "recent on arXiv for this topic"})
        except Exception as e:  # arXiv down should not break the page
            log(f"arxiv '{q}' failed: {e}")
    papers = sorted(found.values(), key=lambda x: x["published"] or "", reverse=True)
    _cache[topic["id"]] = (time.time(), papers)
    return papers
