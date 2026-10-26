"""Technology Evolution Timeline (plan sections 2.2 and 3), built from the topic ledger. No LLM calls.

For each developer whose claims are tracked, the ledger already holds everything a timeline needs: when each
source was published and retrieved, who published it and on what basis, when Newsren started tracking a claim,
every label it has had, and the briefings drafted from it. This module only sorts and names those events:

  developer    the developer's own publication (release, model card, blog) or its claim
  repeat       someone else repeating the developer's numbers
  independent  a party other than the developer reporting its own testing
  commentary   discussion or other reporting
  label / briefing   Newsren's own steps (tracking and labelling a claim, drafting and approving)

Each event keeps its source link, so the timeline can be checked like a briefing.
"""
from .brief import LABEL_TEXT
from .store import OUT, iso, now, parse_iso

KIND_TEXT = {"developer": "Developer publication", "repeat": "Repeats the developer",
             "independent": "Independent evaluation", "commentary": "Commentary",
             "label": "Evidence label", "briefing": "Editorial"}


def _evidence_kind(e):
    if e.get("same_org") or e.get("source_type") == "vendor":
        return "developer"
    if e.get("basis") == "own_testing":
        return "independent"
    if e.get("basis") == "reports_originator":
        return "repeat"
    return "commentary"


def build(ledger):
    """Return one story per developer: {developer, claims, events, summary}, events oldest first."""
    stories = {}
    for cid, c in ledger.data["claims"].items():
        dev = c.get("developer") or "Unknown"
        s = stories.setdefault(dev, {"developer": dev, "claims": [], "events": {}})
        s["claims"].append({"id": cid, "text": c["text"], "label": c["label"]})
        for e in c.get("evidence", []):
            kind = _evidence_kind(e)
            # Irrelevant items are kept only when the developer published them: that is the release itself.
            if e.get("stance") == "irrelevant" and kind != "developer":
                continue
            ev = s["events"].setdefault(("src", e["url"]), {
                "at": e.get("published") or e.get("retrieved_at"), "date_known": bool(e.get("published")),
                "kind": kind, "title": e.get("title") or e["url"], "url": e["url"],
                "publisher": e.get("publisher"), "source_type": e.get("source_type"),
                "retrieved_at": e.get("retrieved_at"), "supports": [], "contradicts": [], "context": False})
            if e.get("stance") == "supports":
                ev["supports"].append(cid)
            elif e.get("stance") == "contradicts":
                ev["contradicts"].append(cid)
            else:
                ev["context"] = True
        prev = None
        for i, h in enumerate(c.get("history", [])):
            if h["label"] != prev:
                s["events"][("label", cid, i)] = {
                    "at": (c.get("first_seen") or h["at"]) if prev is None else h["at"], "kind": "label",
                    "label": h["label"], "claim": c["text"], "date_known": True,
                    "title": (f"Newsren started tracking the claim, labelled {LABEL_TEXT[h['label']]}" if prev is None
                              else f"Label changed: {LABEL_TEXT[prev]} → {LABEL_TEXT[h['label']]}")}
            prev = h["label"]
    for b in ledger.data["briefings"]:
        devs = {ledger.data["claims"][cid].get("developer") or "Unknown"
                for cid in b["claims"] if cid in ledger.data["claims"]}
        for dev in devs:
            ev = stories[dev]["events"]
            ev[("brief", b["id"])] = {"at": b["created_at"], "kind": "briefing", "date_known": True,
                                      "title": f"Briefing {b['id']} drafted, pending editorial review",
                                      "file": b["path"].rsplit("/", 1)[-1]}
            if b.get("approved_at"):
                ev[("approved", b["id"])] = {"at": b["approved_at"], "kind": "briefing", "date_known": True,
                                             "title": f"Briefing {b['id']} approved by {b['approved_by']} and delivered",
                                             "file": b["path"].rsplit("/", 1)[-1]}
    out = []
    for s in stories.values():
        s["events"] = sorted(s["events"].values(), key=lambda e: (e["at"] or "9999", e["kind"] != "developer"))
        s["summary"] = _summary(s["events"])
        out.append(s)
    # the story with the most recent activity first
    return sorted(out, key=lambda s: max((e["at"] or "") for e in s["events"]), reverse=True)


def _summary(events):
    """Days from the developer's first publication to the first independent result, from dated events only."""
    first = lambda kind: next((e for e in events if e["kind"] == kind and e["date_known"]), None)
    dev, ind = first("developer"), first("independent")
    out = {"developer_at": dev and dev["at"], "independent_at": ind and ind["at"], "lag_days": None}
    if dev and ind:
        out["lag_days"] = round((parse_iso(ind["at"]) - parse_iso(dev["at"])).total_seconds() / 86400, 1)
    return out


def summary_line(s):
    m = s["summary"]
    if m["lag_days"] is not None:
        if m["independent_at"][:10] == m["developer_at"][:10]:
            return (f"Developer's first source and first independent result both on {m['developer_at'][:10]} "
                    "(order within the day unknown).")
        if m["lag_days"] < 0:
            return (f"An independent result ({m['independent_at'][:10]}) predates the developer's first source "
                    f"in the ledger ({m['developer_at'][:10]}).")
        return (f"Developer's first source {m['developer_at'][:10]}; first independent result "
                f"{m['independent_at'][:10]}, {m['lag_days']:g} days later.")
    if m["independent_at"]:
        return f"First independent result {m['independent_at'][:10]}; no dated developer source in the ledger."
    if m["developer_at"]:
        return f"Developer's first source {m['developer_at'][:10]}; no independent result yet."
    return "No dated sources yet."


def when_text(e):
    if not e["date_known"]:
        return f"date unknown (retrieved {(e.get('retrieved_at') or '')[:10]})"
    # sources that give only a day are stored at midnight
    return e["at"][:10] if e["at"][11:19] == "00:00:00" else e["at"][:16].replace("T", " ")


def role_text(e):
    role = []
    if e.get("supports"):
        role.append(f"supports {len(e['supports'])} claim{'s' * (len(e['supports']) > 1)}")
    if e.get("contradicts"):
        role.append(f"contradicts {len(e['contradicts'])}")
    if e.get("context") and not role:
        role.append("context, not cited for a claim")
    if e.get("source_type") == "paper":
        role.append("preprint, not peer reviewed")
    return "; ".join(role)


def _event_line(e):
    head = f"**{when_text(e)}** · {KIND_TEXT[e['kind']]}: "
    if e.get("url"):
        return f"- {head}[{e['title']}]({e['url']}) · {e['publisher']} ({role_text(e)})"
    if e.get("claim"):
        return f"- {head}{e['title']}. *{e['claim']}*"
    return f"- {head}{e['title']}"


def markdown(topic, stories):
    lines = ["# Technology Evolution Timeline", "", f"Topic: {topic['question']} · generated {iso(now())} "
             "from the topic ledger, no LLM calls", ""]
    if not stories:
        lines += ["No claims tracked yet. Run the topic first.", ""]
    for s in stories:
        name = s["developer"] if s["developer"] != "Unknown" else "Developer not identified"
        lines += [f"## {name}", "", summary_line(s), ""]
        lines += ["Claims: " + "; ".join(f"{c['text']} (**{LABEL_TEXT[c['label']]}**)" for c in s["claims"]), ""]
        lines += [_event_line(e) for e in s["events"]] + [""]
    lines += ["---", "*Built by code from the dates recorded with each source: publication time where the source "
              "gives one, otherwise retrieval time. Only sources found by Newsren's runs appear, so a missing step "
              "means Newsren has not seen it, not that it did not happen.*", ""]
    return "\n".join(lines)


def write(topic, ledger, log=print):
    path = OUT / topic["id"] / f"{now().strftime('%Y-%m-%d_%H%M%S')}_timeline.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown(topic, build(ledger)))
    log(f"  timeline -> {path}")
    return path
