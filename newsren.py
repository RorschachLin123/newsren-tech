#!/usr/bin/env python3
"""Newsren Technology module: Follow a Topic with evidence verification and cited briefings.

  python3 newsren.py topics                     list followed topics
  python3 newsren.py run [TOPIC] [--max-calls N]  one monitoring run (all topics if none given)
  python3 newsren.py pending                    briefings waiting for editorial review
  python3 newsren.py approve TOPIC BRIEFING_ID --editor NAME
"""
import argparse

from newsren import brief, follow
from newsren.store import Ledger, list_topics, load_topic


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("topics")
    r = sub.add_parser("run")
    r.add_argument("topic", nargs="?")
    r.add_argument("--max-calls", type=int, default=20, help="LLM call budget per topic run")
    sub.add_parser("pending")
    a = sub.add_parser("approve")
    a.add_argument("topic")
    a.add_argument("briefing")
    a.add_argument("--editor", required=True)
    args = ap.parse_args()

    if args.cmd == "topics":
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


if __name__ == "__main__":
    main()
