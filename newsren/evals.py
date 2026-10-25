"""Independent evaluation data: Epoch AI, LMArena and Artificial Analysis.

Each source publishes structured results. We look up a model by name and render each result
row as a short factual sentence, so the verifier can quote it like any other source text.
Rows are rendered by code, never by the LLM, and keep a link to the original data.

  Epoch AI           benchmark_data.zip (CC BY). Files without "_external" are runs Epoch did itself;
                     "_external" rows cite someone else (often the developer) and are classified by their link.
  LMArena            leaderboard-dataset on Hugging Face (text leaderboard, "overall" category).
  Artificial Analysis  model data embedded in their public leaderboard page.
"""
import csv
import io
import json
import re
import time
import urllib.parse
import zipfile

from . import sources
from .store import DATA, iso, now, parse_iso

CACHE_HOURS = 12
EPOCH_ZIP = "https://epoch.ai/data/benchmark_data.zip"
EPOCH_PAGE = "https://epoch.ai/benchmarks"
LMARENA_API = "https://datasets-server.huggingface.co"
LMARENA_DATASET = "lmarena-ai/leaderboard-dataset"
LMARENA_PAGE = "https://lmarena.ai/leaderboard/text"
AA_PAGE = "https://artificialanalysis.ai/leaderboards/models"


def _cached(name, fetch):
    path = DATA / "cache" / name
    if path.exists() and time.time() - path.stat().st_mtime < CACHE_HOURS * 3600:
        return path.read_bytes()
    body = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return body


def norm(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def matches(model, candidate):
    """'Kimi K3.1 Pro' matches 'kimi-k3.1-pro-2026-10' and 'Kimi K3.1 Pro (High)', but 'GPT-6' does not match 'GPT-6.1'."""
    m = norm(model)
    return len(m) >= 4 and re.search(re.escape(m) + r"(?![0-9])", norm(candidate)) is not None


def _row_item(url, title, publisher, source_type, published, text, via):
    return {"id": sources.short_hash(url + text), "url": url, "title": title, "published": published,
            "retrieved_at": iso(now()), "publisher": publisher, "domain": sources.domain_of(url),
            "source_type": source_type, "summary": text, "text": text, "via": via}


# ---- Epoch AI ---------------------------------------------------------------

def _epoch_tables():
    z = zipfile.ZipFile(io.BytesIO(_cached("epoch_benchmarks.zip", lambda: sources._get(EPOCH_ZIP, timeout=60))))
    names = {}
    with z.open("benchmark_metadata.csv") as f:
        for r in csv.DictReader(io.TextIOWrapper(f, "utf-8")):
            if r.get("source_file"):
                names[r["source_file"]] = r["benchmark"]
    for fn in z.namelist():
        if not fn.endswith(".csv") or fn.endswith("_metadata.csv") or "/" in fn:
            continue
        bench = names.get(fn) or fn[:-4].replace("_external", "").replace("_", " ")
        with z.open(fn) as f:
            yield fn, bench, list(csv.DictReader(io.TextIOWrapper(f, "utf-8")))


def _epoch_item(fn, bench, r):
    own = "_external" not in fn
    model = r.get("Model version") or r.get("Name") or ""
    score = r.get("mean_score") or r.get("Best score (across scorers)") or r.get("Score")
    if not model or not score:
        return None
    if own:
        when = (r.get("Started at") or "")[:10] or None
        err = f" (± {float(r['stderr']):.3f})" if r.get("stderr") else ""
        text = (f"Epoch AI ran {bench} itself on {model}"
                f"{' on ' + when if when else ''}: mean score {float(score):.3f}{err}.")
        url = r.get("Log viewer") or EPOCH_PAGE
        return _row_item(url if url.startswith("http") else EPOCH_PAGE, f"Epoch AI: {model} on {bench}",
                         "Epoch AI (own evaluation)", "independent_eval", when and when + "T00:00:00Z",
                         text, "data:epoch")
    link = r.get("Source link") or ""
    _, stype = sources.classify(link) if link else (False, "unknown")
    text = f"Epoch AI's database lists {model} at {score} on {bench}, as reported by {r.get('Source') or 'an external source'}."
    return _row_item(link or EPOCH_PAGE, f"{bench}: {model} (reported by {r.get('Source') or 'external'})",
                     f"Epoch AI database, citing {r.get('Source') or 'external source'}", stype,
                     None, text, "data:epoch-external")


def epoch_lookup(model, limit=4):
    out = []
    for fn, bench, rows in _epoch_tables():
        for r in rows:
            if matches(model, r.get("Model version") or r.get("Name")):
                it = _epoch_item(fn, bench, r)
                if it:
                    out.append(it)
    out.sort(key=lambda it: (it["source_type"] != "independent_eval", -(parse_iso(it["published"]).timestamp() if it["published"] else 0)))
    return out[:limit]


def epoch_new_runs(since):
    """Discovery: evaluations Epoch itself ran since the last check, one item per model."""
    by_model = {}
    for fn, bench, rows in _epoch_tables():
        if "_external" in fn:
            continue
        for r in rows:
            started = parse_iso((r.get("Started at") or "")[:19] + "Z") if r.get("Started at") else None
            if started and started >= since:
                it = _epoch_item(fn, bench, r)
                if it:
                    by_model.setdefault(r["Model version"], []).append(it)
    out = []
    for model, items in by_model.items():
        text = " ".join(it["text"] for it in items[:6])
        out.append(_row_item(items[0]["url"], f"New Epoch AI evaluations: {model}",
                             "Epoch AI (own evaluation)", "independent_eval",
                             max(it["published"] or "" for it in items) or None, text, "data:epoch"))
    return out


# ---- LMArena ----------------------------------------------------------------

def _lmarena_rows():
    def fetch():
        # The /rows endpoint is more reliable than /filter; "overall" rows come first in the split.
        rows, offset = [], 0
        while offset < 3000:
            q = urllib.parse.urlencode({"dataset": LMARENA_DATASET, "config": "text", "split": "latest",
                                        "offset": offset, "length": 100})
            page = json.loads(sources._get(f"{LMARENA_API}/rows?{q}", timeout=30))
            got = [r["row"] for r in page.get("rows", [])]
            overall = [r for r in got if r["category"] == "overall"]
            rows += overall
            if len(got) < 100 or (rows and len(overall) < len(got)):
                break
            offset += 100
        return json.dumps(rows).encode()
    return json.loads(_cached("lmarena_text_overall.json", fetch))


def _lmarena_item(r):
    text = (f"LMArena text leaderboard (overall, published {r['leaderboard_publish_date']}): {r['model_name']} "
            f"ranks {int(r['rank'])} with rating {r['rating']:.0f} (95% CI {r['rating_lower']:.0f}–{r['rating_upper']:.0f}) "
            f"from {int(r['vote_count']):,} human preference votes.")
    return _row_item(LMARENA_PAGE + "#" + norm(r["model_name"]), f"LMArena: {r['model_name']}", "LMArena",
                     "independent_eval", r["leaderboard_publish_date"] + "T00:00:00Z", text, "data:lmarena")


def lmarena_lookup(model, limit=2):
    return [_lmarena_item(r) for r in _lmarena_rows() if matches(model, r["model_name"])][:limit]


def lmarena_new_models(log):
    """Discovery: models that entered the text leaderboard since the previous snapshot.
    The first snapshot is only a baseline, so it reports nothing."""
    rows = _lmarena_rows()
    path = DATA / "cache" / "lmarena_known_models.json"
    known = set(json.loads(path.read_text())) if path.exists() else None
    path.write_text(json.dumps(sorted(r["model_name"] for r in rows)))
    if known is None:
        log("lmarena: first snapshot saved as baseline")
        return []
    return [_lmarena_item(r) for r in rows if r["model_name"] not in known]


# ---- Artificial Analysis ------------------------------------------------------

AA_FIELDS = [("intelligenceIndex", "Intelligence Index", "{:.1f}"), ("gpqa", "GPQA Diamond", "{:.1%}"),
             ("hle", "Humanity's Last Exam", "{:.1%}"), ("terminalBench21", "Terminal-Bench 2.1", "{:.1%}"),
             ("scicode", "SciCode", "{:.1%}"), ("lcr", "long-context reasoning", "{:.1%}")]


def _aa_models():
    page = _cached("aa_leaderboard.html", lambda: sources._get(AA_PAGE, timeout=60)).decode("utf-8", "ignore")
    page = page.replace('\\"', '"')
    dec, models, seen = json.JSONDecoder(), [], set()
    for m in re.finditer(r'\{"slug":"', page):
        try:
            obj, _ = dec.raw_decode(page, m.start())
        except ValueError:
            continue
        if "intelligenceIndex" in obj and obj["slug"] not in seen:
            seen.add(obj["slug"])
            models.append(obj)
    return models


def aa_lookup(model, limit=2):
    out = []
    for m in _aa_models():
        if not (matches(model, m.get("name")) or matches(model, m.get("slug"))):
            continue
        parts = [f"{label} {fmt.format(m[k])}" for k, label, fmt in AA_FIELDS if m.get(k) is not None]
        est = " (estimated)" if m.get("intelligenceIndexIsEstimated") else ""
        text = (f"Artificial Analysis independently benchmarks {m['name']} (by {m.get('modelCreatorName')}): "
                + ", ".join(parts) + est + ".")
        out.append(_row_item(f"https://artificialanalysis.ai/models/{m['slug']}", f"Artificial Analysis: {m['name']}",
                             "Artificial Analysis", "independent_eval", None, text, "data:artificialanalysis"))
    return out[:limit]


# ---- entry points -----------------------------------------------------------

def lookup(models, log):
    """Evidence for a claim: every independent result we hold for the named models."""
    out = []
    for model in models[:3]:
        for name, fn in (("epoch", epoch_lookup), ("lmarena", lmarena_lookup), ("artificialanalysis", aa_lookup)):
            try:
                out += fn(model)
            except Exception as e:
                log(f"{name} lookup '{model}' failed: {e}")
    return out


def discover(since, log):
    out = []
    for name, fn in (("epoch", lambda: epoch_new_runs(since)), ("lmarena", lambda: lmarena_new_models(log))):
        try:
            out += fn()
        except Exception as e:
            log(f"{name} discovery failed: {e}")
    return out
