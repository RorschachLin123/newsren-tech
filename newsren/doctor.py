"""`newsren.py doctor`: check the setup before spending anything. Makes no LLM calls.

Checks Python, the .env file, the LLM provider and key (by listing models, which is free), that every
approved source answers, and prints a rough cost estimate for the demo commands.
"""
import os
import sys
import urllib.request

from . import evals, llm, sources
from .store import DATA, OUT, ROOT

# Rough tokens per command (input, output), from the 2026-10-25 runs: a run is ~8 calls, ask 2, learn 3.
USAGE = {"run": (60_000, 10_000), "ask": (10_000, 2_500), "learn (3 levels)": (12_000, 6_000)}


def _probe(url, method="GET"):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": sources.UA})
    try:
        with urllib.request.urlopen(req, timeout=20, context=sources.SSL) as r:
            if method == "GET":
                r.read(1024)
            return True, f"HTTP {r.status}"
    except Exception as e:  # report every failure the same way; doctor never stops early
        return False, str(e)[:120]


def source_urls():
    cfg = sources.config()
    urls = [(f["name"], f["url"], "GET") for f in cfg["feeds"]]
    tools = set(cfg.get("search_tools", []))
    extra = {
        "arxiv": ("arXiv API", "http://export.arxiv.org/api/query?search_query=all:benchmark&max_results=1", "GET"),
        "hackernews": ("Hacker News search", "https://hn.algolia.com/api/v1/search?query=llm&hitsPerPage=1", "GET"),
        "epoch_benchmarks": ("Epoch AI benchmark data", evals.EPOCH_ZIP, "HEAD"),
        "lmarena_leaderboard": ("LMArena dataset", evals.LMARENA_API + "/splits?dataset=" + evals.LMARENA_DATASET, "GET"),
        "artificialanalysis_models": ("Artificial Analysis", evals.AA_PAGE, "GET"),
    }
    return urls + [extra[t] for t in sorted(tools) if t in extra]


def main(log=print):
    problems = 0

    def report(ok, what, detail=""):
        nonlocal problems
        problems += not ok
        log(f"  [{'ok' if ok else '!!'}] {what}" + (f": {detail}" if detail else ""))

    log("Python")
    report(sys.version_info >= (3, 10), f"Python {sys.version.split()[0]}", "" if sys.version_info >= (3, 10) else "needs 3.10+")

    log("\nConfiguration")
    has_env = (ROOT / ".env").exists()
    report(has_env, ".env file", "found" if has_env else "missing: copy .env.example to .env and fill in a key")
    cfg = llm.settings()
    log(f"  provider: {cfg['provider']}   model: {cfg['model']}   endpoint: {cfg['base_url'] or '-'}")
    ok, msg = llm.check_key(cfg)
    report(ok, "LLM access", msg)
    for d in (DATA, OUT):
        d.mkdir(parents=True, exist_ok=True)
        report(os.access(d, os.W_OK), f"writable {d.relative_to(ROOT)}/")

    log("\nApproved sources")
    for name, url, method in source_urls():
        ok, msg = _probe(url, method)
        report(ok, name, "" if ok else msg)

    log("\nRough cost per command (list price, estimated tokens)")
    if cfg["price"] is None:
        log("  claude-cli: billed to the Claude Code login; the CLI reports cost after each call")
    elif cfg["price"] == (0.0, 0.0):
        log("  unknown: set NEWSREN_PRICE_IN and NEWSREN_PRICE_OUT (USD per 1M tokens) to estimate")
    else:
        total = 0.0
        for cmd, (tin, tout) in USAGE.items():
            usd = (tin * cfg["price"][0] + tout * cfg["price"][1]) / 1e6
            total += usd
            log(f"  {cmd:18} ~${usd:.3f}")
        log(f"  {'all of the above':18} ~${total:.3f}  (at ${cfg['price'][0]}/${cfg['price'][1]} per 1M in/out tokens)")

    log(f"\n{'All checks passed.' if not problems else f'{problems} problem(s) found.'}")
    return problems
