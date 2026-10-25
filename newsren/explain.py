"""Ask Newsren and News-to-Learning: answers and teaching material built only from the ledger's sources.

Ask:   plan (which tracked claims help, is a lookup needed) -> optional bounded lookup in the approved
       tools -> answer with [n] citations and a list of what could not be verified. At most 2 LLM calls.
Learn: one verified briefing -> material for a student, an educator or a professional. 1 LLM call per level.

As in briefings, the LLM writes prose; code numbers the sources and strips citations to nothing.
"""
import copy
import json

from . import evals, sources
from .brief import LABEL_TEXT, NOTICE, _number_sources, clean_citations, source_line
from .llm import LLM
from .store import OUT, Ledger, iso, load_topic, now, short_hash

LEVELS = {
    "student": ("a high-school or first-year university student with no technical background",
                '{"title": "...", "explainer": "<5-minute read, 3-5 short paragraphs>", '
                '"glossary": [{"term": "...", "meaning": "..."}], '
                '"check_your_understanding": [{"question": "...", "answer": "..."}]}'),
    "educator": ("a teacher preparing a 20-minute class discussion",
                 '{"title": "...", "summary_for_teacher": "<2-3 paragraphs>", "learning_goals": ["..."], '
                 '"discussion_questions": ["..."], "activity": "<one classroom activity, step by step>", '
                 '"misconceptions_to_watch": ["..."]}'),
    "professional": ("a software engineer or product manager deciding whether this matters for their work",
                     '{"title": "...", "technical_summary": "<2-3 paragraphs>", '
                     '"implications": ["<labelled as supported or speculative>"], "caveats": ["..."]}'),
}
# Evaluation rows print "(± x)" from the source's stderr field: one standard error, not a range.
ERROR_BARS = ("A '±' after a benchmark score is one standard error. Do not present score ± one standard error as "
              "the range the true value is likely in: that covers only about 68%; a rough 95% range is about two "
              "standard errors either side. Do not call one model ahead because the ± ranges do not overlap; "
              "say a gap is clear only if it is larger than about two times the combined error, "
              "sqrt(a² + b²), and otherwise that the results do not clearly separate the models.")
ORDER = {"student": ["explainer", "glossary", "check_your_understanding"],
         "educator": ["summary_for_teacher", "learning_goals", "discussion_questions", "activity",
                      "misconceptions_to_watch"],
         "professional": ["technical_summary", "implications", "caveats"]}


def _claims_for_prompt(claims):
    return [{"id": cid, "claim": c["text"], "made_by": c.get("originator"), "developer": c.get("developer"),
             "label": LABEL_TEXT[c["label"]], "note": c.get("note", ""),
             "evidence": [{"ref": e["ref"], "publisher": e["publisher"], "stance": e["stance"],
                           "basis": e["basis"], "quote": e["quote"]}
                          for e in c["evidence"] if e["stance"] != "irrelevant"]}
            for cid, c in claims.items()]


def _write(topic_id, name, lines):
    path = OUT / topic_id / f"{now().strftime('%Y-%m-%d_%H%M%S')}_{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    return path


# ---- Ask Newsren --------------------------------------------------------------

def ask(topic_id, question, audience=None, max_calls=4, log=print):
    topic = load_topic(topic_id)
    ledger = Ledger(topic_id)
    llm = LLM(max_calls=max_calls)
    problems = []
    note = problems.append
    claims = copy.deepcopy(ledger.data["claims"])  # numbering must not leak into the ledger
    sources_list = _number_sources(claims.values())
    catalogue = [{"id": cid, "claim": c["text"], "label": c["label"]} for cid, c in claims.items()]

    plan = llm.json(f"""A reader asks: {question}

Claims this service has already checked (with evidence labels):
{json.dumps(catalogue, indent=1, ensure_ascii=False)}

Which claims help answer the question? If they are not enough, say which AI models to look up on
independent leaderboards and give up to 2 short search queries (2-4 words).
Return JSON: {{"claim_ids": ["..."], "needs_lookup": false, "models": ["..."], "search_queries": ["..."]}}""")

    used = {cid: claims[cid] for cid in plan.get("claim_ids", []) if cid in claims}
    extra = []
    if plan.get("needs_lookup"):
        # Every evaluation row is kept (one per model per source, plus a few); search hits are capped.
        found = evals.lookup(plan.get("models") or [], note)
        searched = []
        for q in (plan.get("search_queries") or [])[:2]:
            searched += sources.search_evidence(q, note)[:3]
        found += searched[:4]
        known = {e["url"] for e in sources_list}
        for it in found:
            if it["url"] in known:
                continue
            known.add(it["url"])
            text = it.get("text") or sources.fetch_text(it["url"]) or it["summary"]
            approved, _ = sources.classify(it["url"])
            extra.append(it | {"ref": len(sources_list) + len(extra) + 1, "text": text,
                               "approved_domain": approved, "unchecked": True})
        log(f"  looked up {len(extra)} extra sources")

    level = audience or topic.get("audience", "professional")
    out = llm.json(f"""Answer the reader's question for this audience: {level}.
Question: {question}

Checked claims (labels were computed by fixed rules; 'Independently verified' means a party other than the
developer reported its own testing):
{json.dumps(_claims_for_prompt(used), indent=1, ensure_ascii=False)}

Extra sources retrieved for this question (not rule-checked, treat as leads; say so if you rely on them):
{json.dumps([{"ref": e["ref"], "publisher": e["publisher"], "source_type": e["source_type"],
              "text": e["text"][:2000]} for e in extra], indent=1, ensure_ascii=False)}

Rules: use only this material. Every factual sentence ends with [n] citations from the refs above.
Keep developer claims apart from independent results. If the material cannot answer part of the
question, say so instead of guessing.
Return JSON: {{"answer": "<1-4 short paragraphs>", "could_not_verify": ["..."], "follow_up_questions": ["..."]}}""")

    refs = sorted({e["ref"] for c in used.values() for e in c["evidence"] if e["stance"] != "irrelevant"})
    shown = [e for e in sources_list if e["ref"] in refs] + extra
    out = clean_citations(out, {e["ref"] for e in shown}, note)

    lines = [f"# Ask Newsren: {question}", "",
             f"Topic: {topic['question']} · audience: {level} · {iso(now())}", "",
             out.get("answer", ""), ""]
    if out.get("could_not_verify"):
        lines += ["### What we could not verify", ""] + [f"- {x}" for x in out["could_not_verify"]] + [""]
    if out.get("follow_up_questions"):
        lines += ["### You could also ask", ""] + [f"- {x}" for x in out["follow_up_questions"]] + [""]
    lines += ["### Sources", ""] + ([source_line(e) for e in shown] or ["None."])
    lines += ["", "---", "*Answer generated by an AI research agent from the sources listed above. Claim labels "
              "were computed by fixed rules; sources marked 'not rule-checked' were retrieved for this question "
              "only. A personal answer, not a published story.*",
              "", f"<sub>{llm.calls} LLM calls, ${llm.cost_usd:.3f}.</sub>", ""]
    for p in problems:
        log("  ! " + p)
    path = _write(topic_id, "ask_" + short_hash(question), lines)
    log(f"  answer -> {path}")
    return path, out


# ---- News-to-Learning ---------------------------------------------------------

def learn(topic_id, briefing_id, levels=("student", "educator", "professional"), log=print):
    topic = load_topic(topic_id)
    ledger = Ledger(topic_id)
    b = next((b for b in ledger.data["briefings"] if b["id"] == briefing_id), None)
    if b is None:
        raise SystemExit(f"No briefing {briefing_id} for {topic_id}")
    claims = {cid: copy.deepcopy(ledger.data["claims"][cid]) for cid in b["claims"]
              if ledger.data["claims"].get(cid, {}).get("label") == "independently_verified"}
    if not claims:
        raise SystemExit(f"{briefing_id} has no independently verified claims to teach from")
    sources_list = _number_sources(claims.values())
    valid = {e["ref"] for e in sources_list}
    llm = LLM(max_calls=len(levels))
    problems = []
    paths = []
    for level in levels:
        who, shape = LEVELS[level]
        out = llm.json(f"""Turn verified technology news into learning material for {who}.

Verified claims (use nothing else for facts about this news):
{json.dumps(_claims_for_prompt(claims), indent=1, ensure_ascii=False)}

Teach the underlying ideas too (for example what a benchmark is, why an independent evaluation differs
from a developer's own numbers, what an error bar means), using general knowledge only for definitions.
{ERROR_BARS}
Every factual sentence about the news ends with [n] citations from the refs above.
Return JSON: {shape}""")
        out = clean_citations(out, valid, problems.append)
        lines = [f"# {out.get('title', 'Learning material')}", "",
                 f"**For:** {level} · from briefing {briefing_id} ({b['status']}) · {iso(now())} · "
                 f"DRAFT, pending editorial review", ""]
        for key in ORDER[level]:
            val = out.get(key)
            if not val:
                continue
            lines += [f"### {key.replace('_', ' ').capitalize()}", ""]
            if isinstance(val, str):
                lines += [val, ""]
            elif val and isinstance(val[0], dict) and "term" in val[0]:
                lines += [f"- **{g['term']}**: {g['meaning']}" for g in val] + [""]
            elif val and isinstance(val[0], dict):
                lines += [f"{i}. {q['question']}\n   <details><summary>Answer</summary>{q['answer']}</details>"
                          for i, q in enumerate(val, 1)] + [""]
            else:
                lines += [f"- {x}" for x in val] + [""]
        lines += ["### Sources", ""] + [source_line(e) for e in sources_list]
        lines += ["", "---", f"*{NOTICE}*", ""]
        paths.append(_write(topic_id, f"learn_{briefing_id}_{level}", lines))
        log(f"  {level} -> {paths[-1]}")
    for p in problems:
        log("  ! " + p)
    log(f"  {llm.calls} LLM calls, ${llm.cost_usd:.3f}")
    return paths
