"""Follow a Topic: one monitoring run for a reader's saved question.

discover -> drop already-seen -> triage -> verify new claims (and re-check unverified tracked ones)
-> decide whether the reader's alert criteria are met -> draft a briefing for editorial review.
"""
import json
import time
from datetime import timedelta

from . import brief, evals, sources, verify
from .llm import LLM, BudgetExceeded, label as llm_label
from .store import Ledger, iso, load_topic, now, short_hash


def check_trigger(llm, topic, claims):
    """LLM proposes which claims meet the reader's criteria; code keeps only those with enough evidence."""
    if not claims:
        return []
    shown = [{"id": c["id"], "text": c["text"], "originator": c["originator"], "label": c["label"],
              "note": c["note"]} for c in claims]
    prompt = f"""A reader asked to be alerted only when this happens:
{topic['alert_when']}

Claims checked in this run, with their evidence labels:
{json.dumps(shown, indent=1, ensure_ascii=False)}

Which claims, if any, meet the reader's criteria? Return JSON:
{{"matches": [{{"claim_id": "...", "why": "<one line>"}}]}}"""
    need = verify.LABELS[topic.get("min_evidence", "independently_verified")]
    by_id = {c["id"]: c for c in claims}
    matches = []
    for m in llm.json(prompt).get("matches", []):
        c = by_id.get(m.get("claim_id"))
        if c and verify.LABELS[c["label"]] >= need:
            matches.append(c | {"why": m.get("why", "")})
    return matches


def run(topic_id, max_calls=20, log=print):
    topic = load_topic(topic_id)
    ledger = Ledger(topic_id)
    llm = LLM(max_calls=max_calls)
    started = now()
    since = ledger.last_run or started - timedelta(days=topic.get("lookback_days", 7))
    record = {"started_at": iso(started), "since": iso(since), "alert": False, "errors": []}

    def note(msg):
        record["errors"].append(msg)
        log("  ! " + msg)

    log(f"[{topic_id}] discovering items since {iso(since)}")
    found = sources.discover(topic, since, note) + evals.discover(since, note)
    fresh = [it for it in found if not ledger.is_seen(it["url"])]
    fresh.sort(key=lambda it: it["source_type"] != "independent_eval")  # evaluation results first
    fresh = fresh[: topic.get("max_candidates", 25)]
    record.update(discovered=len(found), new_items=len(fresh))
    log(f"  {len(found)} items found, {len(fresh)} not seen before")

    checked, matches = [], []
    try:
        triaged = verify.triage(llm, topic, fresh, ledger.prior_claims()) if fresh else []
        by_id = {it["id"]: it for it in fresh}
        queue = []
        for t in triaged:
            if not t.get("relevant") or t.get("id") not in by_id:
                continue
            for c in t.get("claims", []):
                prior = ledger.data["claims"].get(c.get("same_as") or "")
                if prior and verify.LABELS[prior["label"]] >= 3:
                    continue  # already settled: a repeat, not news for this reader
                c["id"] = prior and c["same_as"] or "C" + short_hash(c["text"])
                c["origin"] = by_id[t["id"]]
                c["update_of"] = prior["label"] if prior else None
                queue.append(c)
        queue.sort(key=lambda c: -int(c.get("importance") or 1))
        queue = queue[: topic.get("max_new_claims", 5)]
        record.update(relevant=sum(1 for t in triaged if t.get("relevant")), claims_queued=len(queue))
        log(f"  {record['relevant']} relevant items, verifying {len(queue)} claims")

        for c in queue:
            pool = [it for it in fresh if it is not c["origin"]]
            lab, evidence, why = verify.verify_claim(llm, c, c["origin"], pool, note)
            claim = {"id": c["id"], "text": c["text"], "originator": c.get("originator"),
                     "developer": c.get("developer"), "developer_domain": c.get("developer_domain"), "label": lab, "note": why,
                     "update_of": c["update_of"], "evidence": [_slim(e) for e in evidence]}
            checked.append(claim)
            log(f"  - {lab:24} {c['text'][:90]}")
        matches = check_trigger(llm, topic, checked)
    except BudgetExceeded as e:
        note(str(e))

    # A claim whose label did not move is not a change for the reader.
    matches = [m for m in matches if m["update_of"] != m["label"]]
    for c in checked:
        entry = ledger.data["claims"].setdefault(c["id"], {"first_seen": iso(started), "history": []})
        entry["history"].append({"at": iso(started), "label": c["label"]})
        entry.update({k: c[k] for k in ("text", "originator", "developer", "developer_domain", "label", "note", "evidence")})
    for it in fresh:
        ledger.mark_seen(it["url"], it["retrieved_at"])

    record.update(finished_at=iso(now()), llm=llm_label(llm), llm_calls=llm.calls, llm_cost_usd=round(llm.cost_usd, 4),
                  alert=bool(matches), alert_claims=[m["id"] for m in matches],
                  seconds=round(time.time() - started.timestamp(), 1))
    path = brief.write(llm, topic, record, checked, matches, ledger, note)
    record.update(report=str(path), llm_calls=llm.calls, llm_cost_usd=round(llm.cost_usd, 4))
    ledger.data["runs"].append(record)
    ledger.save()
    log(f"  {'ALERT drafted (pending editorial review)' if matches else 'no alert'} -> {path}")
    log(f"  {llm.calls} LLM calls, ${llm.cost_usd:.3f}, {record['seconds']}s")
    return record


def _slim(e):
    keep = ("eid", "url", "via", "title", "publisher", "domain", "source_type", "published", "retrieved_at",
            "stance", "basis", "same_org", "quote", "quote_found", "approved_domain", "full_text")
    return {k: e.get(k) for k in keep}
