"""`newsren.py serve`: a local web page over the same pipeline (plan section 5). Standard library only.

One page per topic shows the saved question, the claims being tracked with their evidence labels, and every
report in out/<topic>/. Buttons run the same commands as the CLI: Run now, Ask, Explain for (learn) and
Approve. Only the first three call the LLM. The server listens on 127.0.0.1 only.
"""
import html
import re
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import brief, explain, follow, llm
from .brief import LABEL_TEXT
from .store import OUT, Ledger, list_topics, load_topic

BUSY = threading.Lock()  # one LLM action at a time
ALLOWED_TAGS = ("details", "summary", "sub")

CSS = """
:root { --bg:#fbfaf7; --fg:#1d1d1f; --muted:#6b6b70; --line:#dedbd2; --card:#fff; --accent:#1f3864;
        --ok:#1e7a3c; --warn:#9a6700; --bad:#b42318; }
@media (prefers-color-scheme: dark) { :root { --bg:#16171a; --fg:#ececee; --muted:#a0a0a8; --line:#33343a;
        --card:#1e1f23; --accent:#9db7ff; --ok:#5cc27e; --warn:#e3b341; --bad:#ff7b72; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.55 -apple-system, "Segoe UI", Roboto, sans-serif; }
header { border-bottom:1px solid var(--line); padding:14px 16px; display:flex; gap:18px; align-items:baseline; flex-wrap:wrap; }
header b { font-family:Georgia, serif; font-size:22px; color:var(--accent); }
header nav span { color:var(--muted); margin-right:14px; }
header nav span.on { color:var(--fg); font-weight:600; }
main { max-width:980px; margin:0 auto; padding:20px 16px 60px; }
h1 { font-family:Georgia, serif; font-size:26px; margin:6px 0 4px; }
h2 { font-size:17px; margin:28px 0 10px; border-bottom:1px solid var(--line); padding-bottom:4px; }
.muted { color:var(--muted); }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:14px 16px; margin:10px 0; }
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
textarea { width:100%; min-height:60px; }
button { font:inherit; padding:6px 14px; border-radius:6px; border:1px solid var(--accent); background:var(--accent);
        color:var(--bg); cursor:pointer; }
button.ghost { background:transparent; color:var(--accent); }
ul.files { list-style:none; padding:0; margin:0; } ul.files li { padding:6px 0; border-bottom:1px solid var(--line); }
.doc h1 { font-size:24px; } .doc h2, .doc h3 { margin-top:22px; }
details { margin:4px 0 8px; } summary { cursor:pointer; color:var(--accent); }
pre { white-space:pre-wrap; background:var(--card); border:1px solid var(--line); padding:10px; border-radius:6px; font-size:13px; }
a { color:var(--accent); }
"""


def page(title, body):
    nav = "".join(f'<span class="{"on" if v == "Technology" else ""}">{v}</span>'
                  for v in ("Technology", "Education", "Industry", "Finance"))
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>'
            f'<style>{CSS}</style></head><body><header><b><a href="/" style="text-decoration:none;color:inherit">'
            f'The Newsren</a></b><nav>{nav}</nav></header><main>{body}</main></body></html>')


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

def _files(topic_id):
    d = OUT / topic_id
    return sorted((p for p in d.glob("*.md")), reverse=True) if d.exists() else []


def _kind(name):
    return "ask" if "_ask_" in name else "learn" if "_learn_" in name else "brief"


def topic_page(topic_id):
    topic = load_topic(topic_id)
    ledger = Ledger(topic_id)
    cfg = llm.settings()
    model = f"{cfg['provider']}/{cfg['model']}"
    briefs = {b["path"].rsplit("/", 1)[-1]: b for b in ledger.data["briefings"]}
    files = _files(topic_id)
    q = urllib.parse.quote
    e = html.escape

    body = [f'<p class="muted">Follow a Topic · saved question</p><h1>{e(topic["question"])}</h1>',
            f'<p><b>Alert me when:</b> {e(topic["alert_when"])}</p>',
            f'<p class="muted">Minimum evidence: {e(LABEL_TEXT.get(topic["min_evidence"], topic["min_evidence"]))} · '
            f'LLM for the buttons below: <code>{e(model)}</code> (run <code>python3 newsren.py doctor</code> for costs)</p>']

    runs = ledger.data["runs"]
    last = (f'Last run {e(runs[-1]["finished_at"])}: {runs[-1].get("new_items", 0)} new items, '
            f'{runs[-1].get("llm_calls", 0)} LLM calls, ${runs[-1].get("llm_cost_usd", 0):.3f}, '
            f'{"alert drafted" if runs[-1].get("alert") else "no alert"}.') if runs else "No runs on this machine yet."
    body.append(f'<div class="card"><b>Follow developments</b><p class="muted">{last}</p>'
                f'<form method="post" action="/run"><input type="hidden" name="topic" value="{e(topic_id)}">'
                f'<button>Run now</button><span class="muted">checks all approved sources; takes 1–3 minutes</span>'
                f'</form></div>')

    body.append('<h2>Briefings</h2>')
    rows = []
    for p in files:
        if _kind(p.name) != "brief":
            continue
        b = briefs.get(p.name)
        status = (b["status"].replace("_", " ") if b else "sample in repository" if "_ALERT" in p.name else "no alert")
        actions = ""
        if b and b["status"] == "pending_review":
            actions += (f'<form method="post" action="/approve"><input type="hidden" name="topic" value="{e(topic_id)}">'
                        f'<input type="hidden" name="briefing" value="{e(b["id"])}">'
                        f'<input type="text" name="editor" placeholder="editor name" required>'
                        f'<button class="ghost">Approve and deliver</button></form>')
        if b:
            actions += (f'<form method="post" action="/learn"><input type="hidden" name="topic" value="{e(topic_id)}">'
                        f'<input type="hidden" name="briefing" value="{e(b["id"])}"><label>Explain for</label>'
                        f'<select name="level"><option>student</option><option>educator</option>'
                        f'<option>professional</option></select><button class="ghost">Make learning material</button></form>')
        rows.append(f'<li><a href="/view?topic={q(topic_id)}&file={q(p.name)}">{e(p.name)}</a> '
                    f'<span class="muted">· {e(status)}{" · " + e(b["id"]) if b else ""}</span>{actions}</li>')
    body.append(f'<ul class="files">{"".join(rows)}</ul>' if rows else '<p class="muted">None yet. Press Run now.</p>')

    body.append(f'<h2>Ask Newsren</h2><form method="post" action="/ask"><input type="hidden" name="topic" value="{e(topic_id)}">'
                f'<textarea name="question" required placeholder="Has anyone independently tested Kimi K3.1 Pro, and does it really match Claude Opus 5.5?"></textarea>'
                f'<label>Explain for</label><select name="audience"><option value="">default</option><option>student</option>'
                f'<option>educator</option><option>professional</option></select><button>Ask</button>'
                f'<span class="muted">answers only from checked sources and approved tools; 2 LLM calls</span></form>')

    for kind, title in (("ask", "Answers"), ("learn", "Learning material")):
        items = [p for p in files if _kind(p.name) == kind]
        body.append(f"<h2>{title}</h2>")
        body.append('<ul class="files">' + "".join(
            f'<li><a href="/view?topic={q(topic_id)}&file={q(p.name)}">{e(p.name)}</a></li>' for p in items)
            + "</ul>" if items else '<p class="muted">None yet.</p>')

    claims = ledger.data["claims"]
    body.append("<h2>Inspect evidence: claims being tracked</h2>")
    if claims:
        trs = "".join(
            f'<tr><td class="label l-{e(c["label"])}">{e(LABEL_TEXT[c["label"]])}</td><td>{e(c["text"])}</td>'
            f'<td>{e(c.get("originator") or "")}</td>'
            f'<td>{len([x for x in c.get("evidence", []) if x.get("stance") != "irrelevant"])}</td></tr>'
            for c in claims.values())
        body.append(f'<div class="wrap"><table><tr><th>Label</th><th>Claim</th><th>Made by</th><th>Relevant sources</th></tr>'
                    f'{trs}</table></div>')
    else:
        body.append('<p class="muted">No claims on this machine yet. Press Run now.</p>')
    return page(topic["question"], "".join(body))


def home():
    topics = list_topics()
    if len(topics) == 1:
        return topic_page(topics[0])
    items = "".join(f'<li><a href="/topic?t={urllib.parse.quote(t)}">{html.escape(load_topic(t)["question"])}</a></li>'
                    for t in topics)
    return page("The Newsren", f"<h1>Followed topics</h1><ul class='files'>{items}</ul>")


def view(topic_id, name):
    path = OUT / topic_id / name
    back = f'<p><a href="/topic?t={urllib.parse.quote(topic_id)}">← back to the topic</a></p>'
    return page(name, back + f'<article class="doc">{markdown(path.read_text())}</article>')


def result(title, lines, link=None):
    body = f"<h1>{html.escape(title)}</h1><pre>{html.escape(chr(10).join(lines))}</pre>"
    if link:
        body += f'<p><a href="{link[0]}">{html.escape(link[1])}</a></p>'
    return page(title, body)


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
        if t not in list_topics():
            raise LookupError(f"unknown topic {t!r}")
        return t

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        try:
            if url.path == "/":
                self._send(200, home())
            elif url.path == "/topic":
                self._send(200, topic_page(self._topic(qs.get("t", ""))))
            elif url.path == "/view":
                t, name = self._topic(qs.get("topic", "")), qs.get("file", "")
                if name not in {p.name for p in _files(t)}:  # only files listed in out/<topic>/
                    raise LookupError(f"no report {name!r}")
                self._send(200, view(t, name))
            else:
                self._send(404, page("Not found", "<h1>Not found</h1>"))
        except LookupError as err:
            self._send(404, page("Not found", f"<h1>{html.escape(str(err))}</h1>"))

    def do_POST(self):
        size = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(size).decode()).items()}
        lines = []
        log = lines.append
        try:
            t = self._topic(form.get("topic", ""))
            back = (f"/topic?t={urllib.parse.quote(t)}", "← back to the topic")
            if self.path == "/approve":
                b = brief.approve(Ledger(t), t, form["briefing"], form["editor"])
                self._send(200, result("Approved", [f"{b['id']} approved by {b['approved_by']}",
                                                    "delivered: appended to out/alerts.jsonl"], back))
                return
            if not BUSY.acquire(blocking=False):
                self._send(409, result("Busy", ["Another LLM action is still running. Try again shortly."], back))
                return
            try:
                if self.path == "/run":
                    follow.run(t, log=log)
                elif self.path == "/ask":
                    path, out = explain.ask(t, form["question"], form.get("audience") or None, log=log)
                    back = (f"/view?topic={urllib.parse.quote(t)}&file={urllib.parse.quote(path.name)}", "Open the answer")
                elif self.path == "/learn":
                    paths = explain.learn(t, form["briefing"], [form.get("level", "student")], log=log)
                    back = (f"/view?topic={urllib.parse.quote(t)}&file={urllib.parse.quote(paths[0].name)}",
                            "Open the learning material")
                else:
                    self._send(404, page("Not found", "<h1>Not found</h1>"))
                    return
            finally:
                BUSY.release()
            self._send(200, result("Done", lines, back))
        except (LookupError, KeyError) as err:
            self._send(400, result("Bad request", [str(err)]))
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
