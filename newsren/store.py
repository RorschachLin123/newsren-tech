"""Paths, time helpers and the per-topic ledger (what has been seen, verified and briefed)."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "sources.json"
TOPICS = ROOT / "topics"
DATA = ROOT / "data"
OUT = ROOT / "out"


def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def short_hash(s):
    return hashlib.sha1(s.encode()).hexdigest()[:10]


def load_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else default


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def load_topic(topic_id):
    topic = load_json(TOPICS / f"{topic_id}.json")
    if topic is None:
        raise SystemExit(f"No topic '{topic_id}' in {TOPICS}")
    return topic


def list_topics():
    return sorted(p.stem for p in TOPICS.glob("*.json"))


class Ledger:
    """One JSON file per topic.

    seen:   url -> first time the item was retrieved (dedup across runs)
    claims: claim_id -> claim with its latest evidence label and history
    runs:   one record per run (counts, cost, whether an alert fired)
    """

    def __init__(self, topic_id):
        self.path = DATA / topic_id / "ledger.json"
        self.data = load_json(self.path, {"seen": {}, "claims": {}, "runs": [], "briefings": []})

    @property
    def last_run(self):
        runs = self.data["runs"]
        return parse_iso(runs[-1]["finished_at"]) if runs else None

    def is_seen(self, url):
        return url in self.data["seen"]

    def mark_seen(self, url, when):
        self.data["seen"].setdefault(url, when)

    def prior_claims(self):
        return [{"id": cid, "text": c["text"], "label": c["label"]}
                for cid, c in self.data["claims"].items()]

    def save(self):
        save_json(self.path, self.data)
