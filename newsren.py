#!/usr/bin/env python3
"""Newsren Technology module: Follow a Topic with evidence verification and cited briefings.

  python3 newsren.py doctor                     check Python, .env, the LLM key and every source (no LLM calls)
  python3 newsren.py serve [--port 8000]        local web page with the same actions
  python3 newsren.py topics                     list followed topics
  python3 newsren.py run [TOPIC] [--max-calls N]  one monitoring run (all topics if none given)
  python3 newsren.py pending                    briefings waiting for editorial review
  python3 newsren.py approve TOPIC BRIEFING_ID --editor NAME
  python3 newsren.py ask TOPIC "QUESTION" [--audience student|educator|professional]
  python3 newsren.py learn TOPIC BRIEFING_ID [--level student|educator|professional]
  python3 newsren.py decode ARXIV_ID_OR_LINK [--topic TOPIC] [--audience ...]   Paper Decoder (1 LLM call)
  python3 newsren.py papers [TOPIC]             recent arXiv papers to decode (no LLM calls)
  python3 newsren.py timeline [TOPIC]           evolution timeline from the ledger (no LLM calls)
"""
import argparse

from newsren import brief, doctor, explain, follow, papers, timeline, web
from newsren.store import Ledger, list_topics, load_env, load_topic


def main():
    load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor")
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8000)
    sub.add_parser("topics")
    r = sub.add_parser("run")
    r.add_argument("topic", nargs="?")
    r.add_argument("--max-calls", type=int, default=20, help="LLM call budget per topic run")
    sub.add_parser("pending")
    a = sub.add_parser("approve")
    a.add_argument("topic")
    a.add_argument("briefing")
    a.add_argument("--editor", required=True)
    q = sub.add_parser("ask")
    q.add_argument("topic")
    q.add_argument("question")
    q.add_argument("--audience", choices=list(explain.LEVELS))
    l = sub.add_parser("learn")
    l.add_argument("topic")
    l.add_argument("briefing")
    l.add_argument("--level", choices=list(explain.LEVELS), help="default: all three")
    d = sub.add_parser("decode")
    d.add_argument("paper", help="arXiv id or link")
    d.add_argument("--topic", help="topic for the relevance note and the output folder (default: the first)")
    d.add_argument("--audience", choices=list(explain.LEVELS), default="professional")
    pp = sub.add_parser("papers")
    pp.add_argument("topic", nargs="?")
    t = sub.add_parser("timeline")
    t.add_argument("topic", nargs="?")
    args = ap.parse_args()

    if args.cmd == "doctor":
        raise SystemExit(1 if doctor.main() else 0)
    elif args.cmd == "serve":
        web.serve(args.port)
    elif args.cmd == "topics":
        for t in list_topics():
            topic = load_topic(t)
            print(f"{t}: {topic['question']}\n  alert when: {topic['alert_when']}")
    elif args.cmd == "run":
        for t in [args.topic] if args.topic else list_topics():
            follow.run(t, max_calls=args.max_calls)
    elif args.cmd == "pending":
        for t in list_topics():
            for b in Ledger(t).data["briefings"]:
                if b["status"] == "pending_review":
                    print(f"{t} {b['id']} {b['created_at']} {b['path']}")
    elif args.cmd == "approve":
        b = brief.approve(Ledger(args.topic), args.topic, args.briefing, args.editor)
        print(f"approved {b['id']} by {b['approved_by']}; delivered to out/alerts.jsonl")
    elif args.cmd == "ask":
        path, out = explain.ask(args.topic, args.question, args.audience)
        print("\n" + out.get("answer", ""))
    elif args.cmd == "learn":
        explain.learn(args.topic, args.briefing, [args.level] if args.level else list(explain.LEVELS))
    elif args.cmd == "decode":
        papers.decode(load_topic(args.topic or list_topics()[0]), args.paper, args.audience)
    elif args.cmd == "papers":
        topic = load_topic(args.topic or list_topics()[0])
        for p in papers.recommend(topic, Ledger(topic["id"])):
            print(f"{p['id']}  {(p['published'] or '')[:10]}  {p['title']}  ({p['why']})")
    elif args.cmd == "timeline":
        for tid in [args.topic] if args.topic else list_topics():
            timeline.write(load_topic(tid), Ledger(tid))


if __name__ == "__main__":
    main()
