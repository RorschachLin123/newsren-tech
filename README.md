# Newsren — Technology module (prototype)

Follow a Topic + evidence verification + cited briefing, working end to end on live sources.
Scope: the Technology vertical of *The Newsren: Agentic News Intelligence Plan* (sections 2.1, 2.5, 2.6, 3 "Launch Reality Check", 6, 7).

## What it does

A reader saves a question and says when they want to hear about it:

```json
"question":   "When a new frontier AI model is released, do independent evaluations confirm the developer's benchmark claims?",
"alert_when": "A party other than the model's developer publishes benchmark or evaluation results ... re-posts of the developer's own numbers do not count.",
"min_evidence": "independently_verified"
```

Each run of `python3 newsren.py run` does:

| Step | Component (plan §6) | How |
|---|---|---|
| 1. Discover | Source Discovery | Approved RSS feeds (OpenAI, DeepMind, Google AI, Hugging Face, Simon Willison, Epoch AI, METR), arXiv and Hacker News search, plus **new independent evaluation results**: runs Epoch AI did since the last check, and models that newly entered the LMArena leaderboard. Only sources in `config/sources.json`. |
| 2. Dedup | Story Intelligence (basic) | Per-topic ledger: items already seen are skipped, so the same story is not news twice. |
| 3. Triage | Orchestrator | One LLM call picks relevant items and extracts checkable claims, matching them to claims tracked in earlier runs. |
| 4. Verify | Evidence Verification | For each claim: look up the named models in the independent evaluation data, search the approved tools, fetch pages (approved domains only); LLM classifies each item's stance and basis, **code** computes the label. |
| 5. Trigger | Follow developments | LLM proposes which claims meet `alert_when`; **code** drops any whose evidence label is below `min_evidence`, and any claim whose label did not change since last run. |
| 6. Brief | News Explanation + Editorial gate | LLM writes prose only from verified material with `[n]` citations; code renders the evidence table and source list, and strips citations that point at nothing. Briefing is saved as **DRAFT, pending editorial review**. |
| 7. Deliver | Output | `approve` marks it reviewed and appends it to `out/alerts.jsonl` (stands in for email/push). Nothing reaches the reader without approval. |

### Independent evaluation data (`newsren/evals.py`)

| Source | What we read | Notes |
|---|---|---|
| Epoch AI | `epoch.ai/data/benchmark_data.zip` (CC BY) | Files without `_external` are runs Epoch did itself (with run date, stderr, log link). `_external` rows cite someone else, often the developer, and are classified by their source link, so they do not count as independent by default. |
| LMArena | `lmarena-ai/leaderboard-dataset` on Hugging Face, text leaderboard, overall | Rank, rating with 95% CI, vote count, publish date. The first run saves a baseline; later runs report models that newly appear. |
| Artificial Analysis | Model data embedded in the public leaderboard page | Intelligence Index, GPQA, HLE, SciCode, etc. No dates, shown as "published unknown". |
| METR, Epoch AI | Their RSS/Atom feeds | Research posts and evaluation reports. |

Each data row is rendered by code into one factual sentence that keeps the link to the original data, so the verifier can quote it like any page; the source list marks these as "structured data row". Downloads are cached for 12 hours in `data/cache/`.

### Evidence labels (rule-based, `newsren/verify.py`)

Independence is judged against the **developer** of the model or product the claim is about (the party with a stake), not against whoever reported it. So Epoch AI's own run of a DeepSeek model is independent; DeepSeek's blog, or a news site repeating it, is not.

| Label | Rule |
|---|---|
| Independently verified | A source that is not the developer (different org and domain, approved, not just discussion) reports **its own testing** supporting the claim, and the quote it gave is found in its text. |
| Disputed | Such an independent source contradicts the claim. |
| Only the originator's claim | Support exists, but only from the developer or outlets repeating it. |
| Could not verify | Nothing relevant found. |

The LLM never decides the label directly. This is what separates a vendor announcement from a third-party result (the plan's "Launch Reality Check") and it is unit-tested.

## Guardrails from the plan

- Approved sources only; page fetches are limited to allow-listed domains (no unrestricted browsing).
- Every source line records publisher, source type, publication time and retrieval time; arXiv items are marked "preprint, not peer reviewed".
- Unverified and disputed claims are listed explicitly ("What we could not verify").
- LLM call budget per run (`--max-calls`, default 20); each run logs calls, cost (USD) and duration in the ledger, for the plan's cost metric.
- Human approval before delivery.

## Ask Newsren and News-to-Learning (`newsren/explain.py`)

**Ask Newsren** (plan 2.1, "Ask about this story"): `ask TOPIC "question"`.
1. One LLM call plans: which already-checked claims answer the question, and whether a lookup is needed.
2. If so, a bounded lookup in the approved tools (independent evaluation data, arXiv, Hacker News), at most 6 extra sources, marked *"retrieved for this question, not rule-checked"*.
3. One LLM call answers, citing `[n]`, keeping developer claims apart from independent results and listing what could not be verified. Invented citations are stripped. Asking never writes to the ledger.

**News-to-Learning** (plan 2.3): `learn TOPIC BRIEFING_ID`. Only the briefing's *independently verified* claims are used as facts; the rest are left out. One LLM call per level:

| Level | Output |
|---|---|
| student | 5-minute explainer, glossary, check-your-understanding questions with hidden answers |
| educator | teacher summary, learning goals, discussion questions, one classroom activity, misconceptions to watch |
| professional | technical summary, implications labelled supported or speculative, caveats |

Concepts behind the news (what a benchmark is, why independent evaluation matters, what an error bar means) are taught too; facts about the news itself must carry citations.

## Install and reproduce

Needs Python 3.10 or newer and nothing else: no packages to install. On Windows use `python` instead of `python3`.

**1. Get the code**

```bash
git clone <this repository's URL>
cd newsren-tech
```

**2. Add an LLM key.** Copy `.env.example` to `.env` and fill in one provider. DeepSeek is the default and the
cheapest; the Anthropic API, any OpenAI-compatible endpoint, or the Claude Code CLI also work (see the comments
in `.env.example`). `.env` is git-ignored.

```bash
cp .env.example .env
```

**3. Check the setup.** `doctor` makes no LLM calls. It checks Python, the key (by listing models, which is
free), that every approved source answers, and prints a rough cost per command.

```bash
python3 newsren.py doctor
```

**4. Run it, in the browser or on the command line.**

```bash
python3 newsren.py serve          # then open http://127.0.0.1:8000
```

The page shows the saved question, every claim with its evidence label, and every report. Its buttons are the
same steps as the commands below: **Run now**, **Ask**, **Explain for** (learning material) and **Approve**.

```bash
python3 newsren.py run independent-model-evals            # one Follow a Topic run (~8 LLM calls)
python3 newsren.py pending                                # drafts waiting for editorial review
python3 newsren.py approve independent-model-evals <BRIEFING_ID> --editor <name>
python3 newsren.py ask independent-model-evals "Has anyone independently tested Kimi K3.1 Pro?"   # 2 calls
python3 newsren.py learn independent-model-evals <BRIEFING_ID> --level student                   # 1 call per level
python3 -m unittest discover tests      # 30 offline tests, no network or LLM calls
```

**Cost.** With DeepSeek (`deepseek-flash`, $0.30 / $1.20 per 1M input / output tokens at peak, half off-peak) a
run, an ask and three learning levels together come to a few cents. Every report ends with its LLM calls, the
model used and the cost; each run's totals are also kept in `data/<topic>/ledger.json`.

**What "reproduce" means here.** The sources are live, so a run today reads today's news and evaluation data and
will not match the 2026-10-25 sample below word for word. What should reproduce is the behaviour: developer
claims labelled *only the originator's claim*, a model's own results from Epoch AI or another evaluator labelled
*independently verified*, an alert only when the saved condition is met, and nothing delivered until an editor
approves it. A fresh clone has no ledger, so the first run looks back `lookback_days` (10) days, and LMArena
only stores a baseline on that first run. The committed reports in `out/independent-model-evals/` are the
original sample run, shown on the web page next to your own.

Add a topic by copying `topics/independent-model-evals.json`. Reports go to `out/<topic>/`; the ledger is
`data/<topic>/ledger.json`.

## Sample live runs (2026-10-25)

The first two used Claude Sonnet 5.5 via `claude -p`; the third used DeepSeek.

- **Before the evaluation sources were added:** 25 new items, 3–5 claims checked, all *only the originator's claim* (e.g. Moonshot's own Kimi K3.1 Pro benchmarks), so no alert.
- **With them:** 41 items, 25 new, 5 claims checked. Epoch AI's new runs of DeepSeek V4.1 Flash and Muse Spark Open (GPQA Diamond, FrontierMath) were *independently verified*, with Artificial Analysis's GPQA figure for Muse Spark Open matching Epoch's within a point; Moonshot's Kimi K3.1 Pro claims stayed *originator only*. **Alert drafted**, waiting for editorial review: `out/independent-model-evals/*_ALERT.md`. 8 LLM calls, $0.22, 85 s.
- **Fresh clone with DeepSeek** (`deepseek-flash`, set up only through `.env`, empty ledger): `doctor` passed; the run read 35 items, 25 new, and checked 5 claims. Epoch AI's own GPQA and FrontierMath results for DeepSeek V4.1 Flash, and its Cyber ECI finding for Z.ai's GLM-5.3, were *independently verified*; Moonshot's Kimi K3.1 Pro claims stayed *originator only*. Alert drafted. 8 LLM calls, $0.011 at peak price, 67 s. The Kimi question then cost 2 calls ($0.002) and cited both Artificial Analysis figures (Opus 5.5 Max 57.6, Kimi 55.1); three learning levels cost 3 calls ($0.005). Outputs: `out/independent-model-evals/2026-10-25_230[78]*`.

## Known limits / next steps

- Model-name matching across sources is string-based (`Kimi K3.1 Pro` ↔ `kimi-k3.1-pro_max`); variants like reasoning effort are treated as the same model.
- LMArena's dataset API is sometimes flaky (HTTP 500 on `/filter`); we use `/rows`. Artificial Analysis data is scraped from their page and may break if the page changes; their key-based API would be sturdier.
- Hacker News search is keyword AND-matching; queries back off to shorter forms, but recall is still limited.
- Story timelines (plan 2.2) are only claim histories in the ledger; no clustering or timeline UI yet.
- Not built yet: Research Paper Decoder, story timeline view, scheduled runs, email delivery. The web page is a local single-user view, not a public site.
