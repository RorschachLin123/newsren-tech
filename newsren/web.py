"""`newsren.py serve`: a local web page over the same pipeline (plan section 5). Standard library only.

The top bar has the plan's four verticals; only Technology is built, the others are greyed out. Technology has
one tab per tool, each opening with what it does, how, and what it produced on real data:

  Overview              design, coverage of the plan, success metrics, limitations
  Follow a Topic        the saved question, Run now, briefings and the editorial Approve button
  Launch Reality Check  every tracked claim with its label and evidence
  Ask Newsren           questions answered from checked sources
  Learn                 Explain for student / educator / professional
  Paper Decoder         paste an arXiv link, or decode a recommended paper
  Timeline              developer release -> independent evaluation -> label changes, from the ledger

Buttons run the same code as the CLI. Run now, Ask, Learn and Decode call the LLM; Approve, Timeline and every
page view do not. The server listens on 127.0.0.1 only.
"""
import html
import re
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import brief, explain, follow, llm, papers, timeline
from .brief import LABEL_TEXT
from .store import OUT, Ledger, list_topics, load_topic

BUSY = threading.Lock()  # one LLM action at a time
ALLOWED_TAGS = ("details", "summary", "sub")
VERTICALS = ("Technology", "Education", "Industry", "Finance")

CSS = """
:root { --bg:#fbfaf7; --fg:#1d1d1f; --muted:#6b6b70; --line:#dedbd2; --card:#fff; --accent:#1f3864;
        --ok:#1e7a3c; --warn:#9a6700; --bad:#b42318; }
@media (prefers-color-scheme: dark) { :root { --bg:#16171a; --fg:#ececee; --muted:#a0a0a8; --line:#33343a;
        --card:#1e1f23; --accent:#9db7ff; --ok:#5cc27e; --warn:#e3b341; --bad:#ff7b72; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.55 -apple-system, "Segoe UI", Roboto, sans-serif; }
header { border-bottom:1px solid var(--line); padding:14px 16px 0; }
.top { display:flex; gap:18px; align-items:baseline; flex-wrap:wrap; max-width:1080px; margin:0 auto; }
.top b { font-family:Georgia, serif; font-size:22px; color:var(--accent); }
.top nav a, .top nav span { margin-right:16px; text-decoration:none; }
.top nav a { color:var(--fg); font-weight:600; border-bottom:2px solid var(--accent); }
.top nav span.off { color:var(--muted); opacity:.55; cursor:not-allowed; }
.tabs { display:flex; gap:2px; flex-wrap:wrap; max-width:1080px; margin:10px auto 0; }
.tabs a { padding:7px 12px; border-radius:6px 6px 0 0; text-decoration:none; color:var(--muted); font-size:14px; }
.tabs a.on { color:var(--fg); background:var(--card); border:1px solid var(--line); border-bottom-color:var(--card);
        margin-bottom:-1px; font-weight:600; }
main { max-width:1080px; margin:0 auto; padding:20px 16px 60px; }
h1 { font-family:Georgia, serif; font-size:26px; margin:6px 0 4px; }
h2 { font-size:17px; margin:28px 0 10px; border-bottom:1px solid var(--line); padding-bottom:4px; }
.muted { color:var(--muted); }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:14px 16px; margin:10px 0; }
.intro { display:grid; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr)); gap:10px; margin:12px 0 4px; }
.intro .card { margin:0; font-size:14px; } .intro h3 { margin:0 0 4px; font-size:13px; text-transform:uppercase;
        letter-spacing:.04em; color:var(--muted); }
table { border-collapse:collapse; width:100%; font-size:14px; }
td, th { border:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top; }
th { background:color-mix(in srgb, var(--accent) 12%, transparent); }
.wrap { overflow-x:auto; }
.label { white-space:nowrap; font-weight:600; }
.l-independently_verified { color:var(--ok); } .l-disputed { color:var(--bad); }
.l-originator_claim_only, .l-unverified { color:var(--warn); }
form { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin:8px 0; }
input[type=text], textarea, select { font:inherit; padding:6px 8px; border:1px solid var(--line); border-radius:6px;
        background:var(--bg); color:var(--fg); }
input[type=text].wide { flex:1; min-width:240px; }
textarea { width:100%; min-height:60px; }
button { font:inherit; padding:6px 14px; border-radius:6px; border:1px solid var(--accent); background:var(--accent);
        color:var(--bg); cursor:pointer; }
button.ghost { background:transparent; color:var(--accent); }
ul.files { list-style:none; padding:0; margin:0; } ul.files li { padding:6px 0; border-bottom:1px solid var(--line); }
.doc h1 { font-size:24px; } .doc h2, .doc h3 { margin-top:22px; }
details { margin:4px 0 8px; } summary { cursor:pointer; color:var(--accent); }
pre { white-space:pre-wrap; background:var(--card); border:1px solid var(--line); padding:10px; border-radius:6px; font-size:13px; }
a { color:var(--accent); }
ol.tl { list-style:none; margin:8px 0 0; padding:0 0 0 18px; border-left:2px solid var(--line); }
ol.tl li { position:relative; margin:0 0 12px; padding-left:10px; }
ol.tl li::before { content:""; position:absolute; left:-25px; top:6px; width:10px; height:10px; border-radius:50%;
        background:var(--muted); border:2px solid var(--bg); }
ol.tl li.k-developer::before { background:var(--warn); } ol.tl li.k-independent::before { background:var(--ok); }
ol.tl li.k-label::before { background:var(--accent); } ol.tl li.k-briefing::before { background:var(--fg); }
.when { font-variant-numeric:tabular-nums; color:var(--muted); font-size:13px; margin-right:6px; }
.kind { font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.03em; margin-right:6px; }
.chip { display:inline-block; font-size:12px; padding:1px 7px; border-radius:10px; border:1px solid var(--line);
        color:var(--muted); margin-left:4px; }
"""

# Each tab opens with three short boxes: what the tool does, how, and what it produced on the live data
# (numbers from the report, October 25, 2026).
MODULES = {
    "overview": ("Overview", None),
    "follow": ("Follow a Topic", (
        "A reader saves a question and an alert condition. Newsren checks approved sources and drafts a briefing "
        "only when a claim meets that condition and the minimum evidence level (plan §2.6).",
        "Discover from approved feeds, arXiv, Hacker News and independent evaluation data; skip items already "
        "seen; the model picks relevant items and extracts claims; code labels the evidence, filters the alert "
        "and renders the briefing. A human editor approves before delivery.",
        "41 items read, 25 new, 5 claims checked: 4 independently verified (Epoch AI's own runs), Moonshot's "
        "Kimi K3.1 Pro claim kept as the originator's only. One alert drafted. 8 model calls, about $0.22, 85 s.")),
    "reality": ("Launch Reality Check", (
        "Shows every tracked claim next to the evidence for it, separating what a developer says about its "
        "model from what independent evaluators measured (plan §3, §2.5).",
        "Per source the model reports only stance (supports / contradicts / irrelevant), basis (own testing / "
        "repeats the developer / discussion) and a quote. Fixed rules compute the label; the quote must be found "
        "in the source text. Independence is judged against the model's developer.",
        "DeepSeek V4.1 Flash GPQA Diamond 0.932 ± 0.016 and Muse Spark Open 0.886 ± 0.019, both Epoch AI's own "
        "runs, labelled independently verified; Artificial Analysis agreed on Muse within half a point.")),
    "ask": ("Ask Newsren", (
        "Answers a reader's question from claims Newsren already checked, plus a bounded lookup in the approved "
        "tools when those are not enough (plan §2.1, §1).",
        "One call plans which claims help and whether to look up; at most 6 extra sources, marked not "
        "rule-checked; one call answers with [n] citations. Code strips invented citations. Asking never "
        "changes the ledger.",
        "“Does Kimi K3.1 Pro really match Claude Opus 5.5?”: cites Artificial Analysis 57.6 vs 55.1, keeps "
        "Moonshot's numbers as its own, concludes the match is not established. 2 calls, about $0.04.")),
    "learn": ("Learn", (
        "Turns a verified briefing into material for a student, an educator or a professional: the plan's "
        "“Explain for” selector (§2.3, §5).",
        "Only the briefing's independently verified claims are passed as facts, so unverified claims cannot be "
        "taught as true. Concepts (benchmarks, error bars) are taught from general knowledge. 1 call per level.",
        "Three levels generated, including a classroom “Error-Bar Detective” activity. Review found a misreading "
        "of ± as a 95% range; the prompt now treats ± as one standard error.")),
    "papers": ("Paper Decoder", (
        "Explains one arXiv paper: hypothesis, method, findings, limitations and possible applications, for the "
        "chosen audience, and marks it as a preprint (plan §3, §6).",
        "Reads the arXiv record and, when arXiv has one, the HTML full text; one model call; every sentence cites "
        "the abstract [1] or full text [2]. Limitations and applications are tagged (authors) or (our reading). "
        "Recommendations come from the topic's arXiv queries, without the model.",
        "Decoded papers appear below. Recommendations update from arXiv each hour.")),
    "timeline": ("Timeline", (
        "Shows how each story evolved: the developer's release, independent evaluations, and Newsren's label "
        "changes and briefings, in order (plan §2.2, §3 Technology Evolution Timeline).",
        "Built by code from the ledger: publication and retrieval times of every source, claim first-seen times, "
        "label history and briefings. No model calls, so it costs nothing and every node links to its source.",
        "DeepSeek released V4.1 Flash on Hugging Face on October 17; Epoch AI's own evaluation followed about "
        "two days later. Moonshot's Kimi K3.1 Pro has no independent result yet.")),
}


def page(title, body, topic_id=None, tab=None):
    nav = "".join(f'<a href="/">{v}</a>' if v == "Technology" else
                  f'<span class="off" title="Not in this prototype" aria-disabled="true">{v}</span>' for v in VERTICALS)
    tabs = ""
    if tab:
        t = f"?t={urllib.parse.quote(topic_id)}" if topic_id and len(list_topics()) > 1 else ""
        tabs = '<div class="tabs">' + "".join(
            f'<a class="{"on" if k == tab else ""}" href="/tech/{k}{t}">{name}</a>' for k, (name, _) in MODULES.items()
        ) + "</div>"
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>'
            f'<style>{CSS}</style></head><body><header><div class="top"><b><a href="/" style="text-decoration:none;'
            f'color:inherit">The Newsren</a></b><nav>{nav}</nav></div>{tabs}</header><main>{body}</main></body></html>')


# ---- Markdown subset used by the reports -------------------------------------------

def _inline(s):
    s = html.escape(s, quote=False)
    for tag in ALLOWED_TAGS:
        s = s.replace(f"&lt;{tag}&gt;", f"<{tag}>").replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<em>\1</em>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
               lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener">{m.group(1)}</a>', s)
    return s


def markdown(text):
    out, para, lst, table = [], [], None, []

    def flush():
        nonlocal para, lst, table
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
        if lst:
            out.append(f"<{lst[0]}>" + "".join(f'<li{f" value={n}" if n else ""}>{x}</li>' for n, x in lst[1])
                       + f"</{lst[0]}>")
        if table:
            rows = [r for r in table if not re.fullmatch(r"\|[\s:|-]+\|", r)]
            cells = [[_inline(c.strip()) for c in r.strip("|").split("|")] for r in rows]
            head = "".join(f"<th>{c}</th>" for c in cells[0])
            rest = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in cells[1:])
            out.append(f'<div class="wrap"><table><tr>{head}</tr>{rest}</table></div>')
        para, lst, table = [], None, []

    for line in text.splitlines():
        if line.startswith("|"):
            if not table:
                flush()
            table.append(line)
            continue
        if table:
            flush()
        m_head = re.match(r"(#{1,4}) (.*)", line)
        m_ul = re.match(r"- (.*)", line)
        m_ol = re.match(r"(\d+)\. (.*)", line)
        if m_head:
            flush()
            n = len(m_head.group(1))
            out.append(f"<h{n}>{_inline(m_head.group(2))}</h{n}>")
        elif line.strip() == "---":
            flush()
            out.append("<hr>")
        elif m_ul or m_ol:
            kind = "ul" if m_ul else "ol"
            if para or (lst and lst[0] != kind):
                flush()
            lst = lst or (kind, [])
            # keep the source numbers: [n] citations point at them, and ask answers start above 1
            lst[1].append((m_ol.group(1), _inline(m_ol.group(2))) if m_ol else (None, _inline(m_ul.group(1))))
        elif line.startswith("   ") and lst:
            lst[1][-1] = (lst[1][-1][0], lst[1][-1][1] + " " + _inline(line.strip()))
        elif not line.strip():
            flush()
        else:
            if lst:
                flush()
            para.append(line.strip())
    flush()
    return "\n".join(out)


# ---- Pages ---------------------------------------------------------------------------

e = html.escape
q = urllib.parse.quote


def _files(topic_id):
    d = OUT / topic_id
    return sorted((p for p in d.glob("*.md")), reverse=True) if d.exists() else []


def _kind(name):
    for k in ("ask", "learn", "paper", "timeline"):
        if f"_{k}_" in name or name.endswith(f"_{k}.md"):
            return k
    return "brief"


def _file_list(topic_id, kind, empty="None yet."):
    items = [p for p in _files(topic_id) if _kind(p.name) == kind]
    if not items:
        return f'<p class="muted">{empty}</p>'
    return '<ul class="files">' + "".join(
        f'<li><a href="/view?topic={q(topic_id)}&file={q(p.name)}">{e(p.name)}</a></li>' for p in items) + "</ul>"


def _hidden(**kw):
    return "".join(f'<input type="hidden" name="{k}" value="{e(v)}">' for k, v in kw.items())


def _intro(tab):
    name, boxes = MODULES[tab]
    heads = ("What it does", "How it works", "Real result")
    return f"<h1>{e(name)}</h1>" + '<div class="intro">' + "".join(
        f'<div class="card"><h3>{h}</h3>{e(t)}</div>' for h, t in zip(heads, boxes)) + "</div>"


def _model():
    cfg = llm.settings()
    return f'<code>{e(cfg["provider"])}/{e(cfg["model"])}</code>'


def overview(topic_id):
    topic = load_topic(topic_id)
    rows = [("Follow a Topic", "§2.6, §7", "Built: saved question, alert condition, change detection, dedup", "follow"),
            ("Evidence and Bias Inspection", "§2.5", "Built for claims: provenance, independence, rule-based labels", "reality"),
            ("Launch Reality Check", "§3", "Built: developer claims vs independent evaluations", "reality"),
            ("AI Story Brief + Ask Newsren", "§2.1, §7", "Built: cited briefings; source-grounded Q&A", "ask"),
            ("News-to-Learning (Explain for)", "§2.3, §5", "Built: student, educator, professional", "learn"),
            ("Research Paper Decoder", "§3", "Built: arXiv link or recommended paper, 1 model call", "papers"),
            ("Technology Evolution Timeline", "§2.2, §3", "Built from the ledger, no model calls", "timeline"),
            ("Editorial quality gate", "§6", "Built: drafts wait for human approval", "follow"),
            ("Scheduled runs, email delivery", "§5, §7", "Not built: delivery is a log file standing in for email", None)]
    cov = "".join(f'<tr><td>{f"<a href=/tech/{k}>{e(a)}</a>" if k else e(a)}</td><td>{b}</td><td>{e(c)}</td></tr>'
                  for a, b, c, k in rows)
    metrics = [("Trust", "Labels are rule-based and tested; quotes must be found in the source; invalid citations "
                "are stripped.", "A sampled editor audit over many runs."),
               ("Usefulness", "Ask answered the comparison the reader asked for and said what was not established.",
                "Reader ratings of time saved."),
               ("Education", "Three levels from verified facts; one statistical misreading found and fixed.",
                "Quiz results versus an ordinary summary."),
               ("Alert quality", "No alert on re-posts of developer numbers; unchanged labels do not re-alert.",
                "Useful-versus-unwanted alert rate over weeks."),
               ("Cost", "Run: 8 calls, about $0.22. Ask: 2 calls. Learn and Decode: 1 call each. Timeline: 0.",
                "Costs across many topics on a schedule.")]
    met = "".join(f"<tr><td>{e(a)}</td><td>{e(b)}</td><td>{e(c)}</td></tr>" for a, b, c in metrics)
    body = f"""<h1>Technology: the Launch Reality Check</h1>
<p>The Technology vertical of <i>The Newsren: Agentic News Intelligence Plan</i>, built around one saved question
on live sources: <b>{e(topic["question"])}</b></p>
<div class="intro"><div class="card"><h3>Design rule</h3>The language model reads and writes; code decides.
The model classifies what a source says and drafts prose. Fixed, unit-tested rules decide whether a claim is
independently verified, whether an alert fires, and which citations survive.</div>
<div class="card"><h3>Guardrails (plan §6)</h3>Approved sources only, no open browsing; publication and retrieval
time on every source; preprints marked; disagreement shown, not hidden; a budget of model calls per run; nothing
reaches a reader without human approval.</div>
<div class="card"><h3>Model for the buttons</h3>{_model()}. Run <code>python3 newsren.py doctor</code> to check
the key and every source without spending anything.</div></div>
<h2>Modules</h2><div class="wrap"><table><tr><th>Plan item</th><th>Section</th><th>Status</th></tr>{cov}</table></div>
<p class="muted">Education, Industry and Finance share the same technology but are out of scope by choice.</p>
<h2>Success metrics (plan §9)</h2><div class="wrap"><table><tr><th>Metric</th><th>Evidence so far</th>
<th>Still needed</th></tr>{met}</table></div>
<h2>Limitations</h2><ul>
<li>Model names are matched as strings across sources; settings such as reasoning effort count as one model.</li>
<li>Some sources are fragile: Artificial Analysis is read from its public page; LMArena's API is sometimes down.</li>
<li>The live data has no developer score on the same benchmark as an independent one yet, so no claim has been
confirmed or disputed head to head.</li>
<li>Model wording still needs an editor: the rules stop wrong labels and fake citations, not every bad sentence.</li>
<li>The Paper Decoder reads arXiv's HTML version; papers without one are decoded from the abstract only.</li>
<li>The timeline shows only what Newsren's runs found; a missing step means unseen, not absent.</li></ul>"""
    return page("Newsren · Technology", body, topic_id, "overview")


def follow_page(topic_id):
    topic = load_topic(topic_id)
    ledger = Ledger(topic_id)
    briefs = {b["path"].rsplit("/", 1)[-1]: b for b in ledger.data["briefings"]}
    body = [_intro("follow"),
            f'<div class="card"><p class="muted">Saved question</p><h2 style="border:0;margin:0">{e(topic["question"])}</h2>'
            f'<p><b>Alert me when:</b> {e(topic["alert_when"])}</p>'
            f'<p class="muted">Minimum evidence: {e(LABEL_TEXT.get(topic["min_evidence"], topic["min_evidence"]))}</p></div>']
    runs = ledger.data["runs"]
    last = (f'Last run {e(runs[-1]["finished_at"])}: {runs[-1].get("new_items", 0)} new items, '
            f'{runs[-1].get("llm_calls", 0)} LLM calls, ${runs[-1].get("llm_cost_usd", 0):.3f}, '
            f'{"alert drafted" if runs[-1].get("alert") else "no alert"}.') if runs else "No runs on this machine yet."
    body.append(f'<div class="card"><b>Follow developments</b><p class="muted">{last}</p>'
                f'<form method="post" action="/run">{_hidden(topic=topic_id)}<button>Run now</button>'
                f'<span class="muted">checks all approved sources with {_model()}; up to 20 LLM calls, 1–3 minutes</span>'
                f'</form></div>')
    body.append('<h2>Briefings</h2>')
    rows = []
    for p in _files(topic_id):
        if _kind(p.name) != "brief":
            continue
        b = briefs.get(p.name)
        status = (b["status"].replace("_", " ") if b else "sample in repository" if "_ALERT" in p.name else "no alert")
        action = ""
        if b and b["status"] == "pending_review":
            action = (f'<form method="post" action="/approve">{_hidden(topic=topic_id, briefing=b["id"])}'
                      f'<input type="text" name="editor" placeholder="editor name" required>'
                      f'<button class="ghost">Approve and deliver</button>'
                      f'<span class="muted">editor\'s decision; no LLM call</span></form>')
        rows.append(f'<li><a href="/view?topic={q(topic_id)}&file={q(p.name)}">{e(p.name)}</a> '
                    f'<span class="muted">· {e(status)}{" · " + e(b["id"]) if b else ""}</span>{action}</li>')
    body.append(f'<ul class="files">{"".join(rows)}</ul>' if rows else '<p class="muted">None yet. Press Run now.</p>')
    return page("Follow a Topic", "".join(body), topic_id, "follow")


def reality_page(topic_id):
    claims = Ledger(topic_id).data["claims"]
    body = [_intro("reality")]
    if not claims:
        body.append('<p class="muted">No claims on this machine yet. Run the topic under Follow a Topic.</p>')
        return page("Launch Reality Check", "".join(body), topic_id, "reality")
    counts = {}
    for c in claims.values():
        counts[c["label"]] = counts.get(c["label"], 0) + 1
    body.append("<p>" + " · ".join(f'<span class="label l-{k}">{e(LABEL_TEXT[k])}</span>: {n}'
                                    for k, n in counts.items()) + "</p>")
    trs = []
    for c in claims.values():
        ev = [x for x in c.get("evidence", []) if x.get("stance") != "irrelevant"]
        srcs = "".join(f'<li>{"✓" if x["stance"] == "supports" else "✗"} <a href="{e(x["url"])}" target="_blank" '
                       f'rel="noopener">{e(x["publisher"])}</a> <span class="chip">{e(x["basis"].replace("_", " "))}'
                       f'</span><span class="chip">{e(x["source_type"])}</span>'
                       f'{"<br><i>“" + e(x["quote"]) + "”</i>" if x.get("quote") else ""}</li>' for x in ev)
        trs.append(f'<tr><td class="label l-{e(c["label"])}">{e(LABEL_TEXT[c["label"]])}</td><td>{e(c["text"])}'
                   f'<details><summary>{len(ev)} relevant source{"s" * (len(ev) != 1)} and the verifier\'s note'
                   f'</summary><ul>{srcs}</ul><p class="muted">{e(c.get("note", ""))}</p></details></td>'
                   f'<td>{e(c.get("originator") or "")}</td><td>{e(c.get("developer") or "")}</td></tr>')
    body.append('<div class="wrap"><table><tr><th>Label</th><th>Claim and evidence</th><th>Made by</th>'
                f'<th>Developer</th></tr>{"".join(trs)}</table></div>'
                '<p class="muted">✓ supports, ✗ contradicts. Independence is judged against the developer.</p>')
    return page("Launch Reality Check", "".join(body), topic_id, "reality")


def ask_page(topic_id):
    body = [_intro("ask"),
            f'<form method="post" action="/ask">{_hidden(topic=topic_id)}'
            f'<textarea name="question" required placeholder="Has anyone independently tested Kimi K3.1 Pro, and '
            f'does it really match Claude Opus 5.5?"></textarea><label>Explain for</label><select name="audience">'
            f'<option value="">default</option><option>student</option><option>educator</option>'
            f'<option>professional</option></select><button>Ask</button>'
            f'<span class="muted">2 LLM calls with {_model()}</span></form>',
            "<h2>Answers</h2>", _file_list(topic_id, "ask")]
    return page("Ask Newsren", "".join(body), topic_id, "ask")


def learn_page(topic_id):
    ledger = Ledger(topic_id)
    body = [_intro("learn"), "<h2>Make learning material from a briefing</h2>"]
    rows = [f'<li><a href="/view?topic={q(topic_id)}&file={q(b["path"].rsplit("/", 1)[-1])}">{e(b["id"])}</a> '
            f'<span class="muted">· {e(b["status"].replace("_", " "))} · {e(b["created_at"])}</span>'
            f'<form method="post" action="/learn">{_hidden(topic=topic_id, briefing=b["id"])}<label>Explain for</label>'
            f'<select name="level"><option>student</option><option>educator</option><option>professional</option>'
            f'</select><button class="ghost">Make learning material</button>'
            f'<span class="muted">1 LLM call</span></form></li>' for b in reversed(ledger.data["briefings"])]
    body.append(f'<ul class="files">{"".join(rows)}</ul>' if rows else
                '<p class="muted">No briefings on this machine yet. Samples from the repository are listed below.</p>')
    body += ["<h2>Learning material</h2>", _file_list(topic_id, "learn")]
    return page("Learn", "".join(body), topic_id, "learn")


def papers_page(topic_id):
    topic = load_topic(topic_id)
    levels = "".join(f"<option{' selected' if k == 'professional' else ''}>{k}</option>" for k in explain.LEVELS)
    decoded = {m.group(1).replace("-", "/") for p in _files(topic_id)
               for m in [re.search(r"_paper_(.+)\.md$", p.name)] if m}
    body = [_intro("papers"),
            f'<div class="card"><b>Decode a paper</b><form method="post" action="/decode">{_hidden(topic=topic_id)}'
            f'<input class="wide" type="text" name="paper" required placeholder="https://arxiv.org/abs/2610.32891 or 2610.32891">'
            f'<label>Explain for</label><select name="audience">{levels}</select><button>Decode</button>'
            f'<span class="muted">1 LLM call with {_model()}</span></form></div>',
            "<h2>Decoded papers</h2>", _file_list(topic_id, "paper"), "<h2>Recommended for this topic</h2>"]
    errors = []
    recs = papers.recommend(topic, Ledger(topic_id), log=errors.append)
    if errors:
        body.append(f'<p class="muted">{e("; ".join(errors))}</p>')
    rows = []
    for r in recs:
        summary = f'<details><summary>Abstract</summary>{e(r["summary"])}</details>' if r.get("summary") else ""
        rows.append(f'<li><a href="https://arxiv.org/abs/{e(r["id"])}" target="_blank" rel="noopener">{e(r["title"])}</a>'
                    f'<br><span class="muted">{e(r["authors"])} · {e((r["published"] or "")[:10])} · arXiv {e(r["id"])}'
                    f'</span><span class="chip">{e(r["why"])}</span><span class="chip">preprint</span>'
                    f'{"<span class=chip>decoded</span>" if r["id"] in decoded else ""}{summary}'
                    f'<form method="post" action="/decode">{_hidden(topic=topic_id, paper=r["id"])}'
                    f'<select name="audience">{levels}</select><button class="ghost">Decode</button></form></li>')
    body.append(f'<ul class="files">{"".join(rows)}</ul>' if rows else '<p class="muted">No recommendations: '
                f'the topic has no arxiv_queries, or arXiv did not answer.</p>')
    body.append(f'<p class="muted">From the topic\'s arXiv queries: {e("; ".join(topic.get("arxiv_queries", [])))}. '
                'Listing papers calls arXiv only, never the model.</p>')
    return page("Paper Decoder", "".join(body), topic_id, "papers")


def timeline_page(topic_id):
    stories = timeline.build(Ledger(topic_id))
    body = [_intro("timeline")]
    if not stories:
        body.append('<p class="muted">No claims on this machine yet. Run the topic under Follow a Topic.</p>')
    body.append(f'<form method="post" action="/timeline">{_hidden(topic=topic_id)}<button class="ghost">'
                f'Save as a report</button><span class="muted">writes the timeline to out/; no LLM call</span></form>'
                '<p class="muted"><span class="kind" style="color:var(--warn)">●</span>developer '
                '<span class="kind" style="color:var(--ok)">●</span>independent evaluation '
                '<span class="kind" style="color:var(--accent)">●</span>evidence label '
                '<span class="kind">●</span>editorial</p>')
    for s in stories:
        name = s["developer"] if s["developer"] != "Unknown" else "Developer not identified"
        claims = "".join(f'<li>{e(c["text"])} <span class="label l-{e(c["label"])}">{e(LABEL_TEXT[c["label"]])}</span></li>'
                         for c in s["claims"])
        evs = []
        for ev in s["events"]:
            if ev.get("url"):
                main = (f'<a href="{e(ev["url"])}" target="_blank" rel="noopener">{e(ev["title"])}</a> '
                        f'<span class="muted">· {e(ev["publisher"] or "")}</span>'
                        f'{"<span class=chip>" + e(timeline.role_text(ev)) + "</span>" if timeline.role_text(ev) else ""}')
            elif ev.get("file"):
                main = f'<a href="/view?topic={q(topic_id)}&file={q(ev["file"])}">{e(ev["title"])}</a>'
            else:
                main = e(ev["title"]) + (f'<br><span class="muted">{e(ev["claim"])}</span>' if ev.get("claim") else "")
            evs.append(f'<li class="k-{ev["kind"]}"><span class="when">{e(timeline.when_text(ev))}</span>'
                       f'<span class="kind">{e(timeline.KIND_TEXT[ev["kind"]])}</span>{main}</li>')
        body.append(f'<div class="card"><h2 style="margin-top:0">{e(name)}</h2><p><b>{e(timeline.summary_line(s))}</b></p>'
                    f'<details><summary>Claims ({len(s["claims"])})</summary><ul>{claims}</ul></details>'
                    f'<ol class="tl">{"".join(evs)}</ol></div>')
    body.append('<p class="muted">Dates are publication times where the source gives one, otherwise retrieval '
                'times. Only sources found by Newsren\'s runs appear.</p>')
    body += ["<h2>Saved timelines</h2>", _file_list(topic_id, "timeline")]
    return page("Timeline", "".join(body), topic_id, "timeline")


PAGES = {"overview": overview, "follow": follow_page, "reality": reality_page, "ask": ask_page,
         "learn": learn_page, "papers": papers_page, "timeline": timeline_page}
TAB_OF = {"run": "follow", "approve": "follow", "ask": "ask", "learn": "learn", "decode": "papers",
          "timeline": "timeline"}


def view(topic_id, name):
    tab = {"ask": "ask", "learn": "learn", "paper": "papers", "timeline": "timeline"}.get(_kind(name), "follow")
    back = f'<p><a href="/tech/{tab}?t={q(topic_id)}">← back to {MODULES[tab][0]}</a></p>'
    return page(name, back + f'<article class="doc">{markdown((OUT / topic_id / name).read_text())}</article>',
                topic_id, tab)


def result(title, lines, link=None, topic_id=None, tab=None):
    body = f"<h1>{e(title)}</h1><pre>{e(chr(10).join(lines))}</pre>"
    if link:
        body += f'<p><a href="{link[0]}">{e(link[1])}</a></p>'
    return page(title, body, topic_id, tab)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=()):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _topic(self, t):
        topics = list_topics()
        if not t and topics:
            return topics[0]
        if t not in topics:
            raise LookupError(f"unknown topic {t!r}")
        return t

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        try:
            if url.path in ("/", "/tech", "/tech/"):
                self._send(200, overview(self._topic(qs.get("t", ""))))
            elif url.path.startswith("/tech/") and url.path[6:] in PAGES:
                self._send(200, PAGES[url.path[6:]](self._topic(qs.get("t", ""))))
            elif url.path == "/topic":  # old single-page address
                self._send(302, "", headers=[("Location", f"/tech/follow?t={q(qs.get('t', ''))}")])
            elif url.path == "/view":
                t, name = self._topic(qs.get("topic", "")), qs.get("file", "")
                if name not in {p.name for p in _files(t)}:  # only files listed in out/<topic>/
                    raise LookupError(f"no report {name!r}")
                self._send(200, view(t, name))
            else:
                self._send(404, page("Not found", "<h1>Not found</h1>"))
        except LookupError as err:
            self._send(404, page("Not found", f"<h1>{e(str(err))}</h1>"))

    def do_POST(self):
        size = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(size).decode()).items()}
        action = self.path.strip("/")
        tab = TAB_OF.get(action)
        lines = []
        log = lines.append
        try:
            t = self._topic(form.get("topic", ""))
            back = (f"/tech/{tab}?t={q(t)}", f"← back to {MODULES[tab][0]}") if tab else None
            show = lambda title, ls, link, code=200: self._send(code, result(title, ls, link, t, tab))
            if action == "approve":
                b = brief.approve(Ledger(t), t, form["briefing"], form["editor"])
                show("Approved", [f"{b['id']} approved by {b['approved_by']}",
                                  "delivered: appended to out/alerts.jsonl"], back)
                return
            if action == "timeline":
                path = timeline.write(load_topic(t), Ledger(t), log=log)
                show("Timeline saved", lines, (f"/view?topic={q(t)}&file={q(path.name)}", "Open the timeline"))
                return
            if action not in ("run", "ask", "learn", "decode"):
                self._send(404, page("Not found", "<h1>Not found</h1>"))
                return
            if not BUSY.acquire(blocking=False):
                show("Busy", ["Another LLM action is still running. Try again shortly."], back, 409)
                return
            try:
                if action == "run":
                    follow.run(t, log=log)
                elif action == "ask":
                    path, out = explain.ask(t, form["question"], form.get("audience") or None, log=log)
                    back = (f"/view?topic={q(t)}&file={q(path.name)}", "Open the answer")
                elif action == "learn":
                    paths = explain.learn(t, form["briefing"], [form.get("level", "student")], log=log)
                    back = (f"/view?topic={q(t)}&file={q(paths[0].name)}", "Open the learning material")
                else:
                    path, out = papers.decode(load_topic(t), form["paper"], form.get("audience") or "professional",
                                              log=log)
                    back = (f"/view?topic={q(t)}&file={q(path.name)}", "Open the decoded paper")
            finally:
                BUSY.release()
            show("Done", lines, back)
        except (LookupError, KeyError) as err:
            self._send(400, result("Bad request", lines + [str(err)]))
        except SystemExit as err:
            self._send(400, result("Stopped", lines + [str(err)]))
        except Exception:
            self._send(500, result("Error", lines + traceback.format_exc().splitlines()[-6:]))

    def log_message(self, fmt, *args):
        print("  web:", fmt % args)


def serve(port=8000):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Newsren is running at http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
