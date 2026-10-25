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
| 1. Discover | Source Discovery | Approved RSS feeds (OpenAI, DeepMind, Google AI, Hugging Face, Simon Willison) plus arXiv and Hacker News search APIs. Only sources in `config/sources.json`. |
| 2. Dedup | Story Intelligence (basic) | Per-topic ledger: items already seen are skipped, so the same story is not news twice. |
| 3. Triage | Orchestrator | One LLM call picks relevant items and extracts checkable claims, matching them to claims tracked in earlier runs. |
| 4. Verify | Evidence Verification | For each claim: search the approved tools for evidence, fetch pages (approved domains only), LLM classifies each item's stance and basis, **code** computes the label. |
| 5. Trigger | Follow developments | LLM proposes which claims meet `alert_when`; **code** drops any whose evidence label is below `min_evidence`, and any claim whose label did not change since last run. |
| 6. Brief | News Explanation + Editorial gate | LLM writes prose only from verified material with `[n]` citations; code renders the evidence table and source list, and strips citations that point at nothing. Briefing is saved as **DRAFT, pending editorial review**. |
| 7. Deliver | Output | `approve` marks it reviewed and appends it to `out/alerts.jsonl` (stands in for email/push). Nothing reaches the reader without approval. |

### Evidence labels (rule-based, `newsren/verify.py`)

| Label | Rule |
|---|---|
| Independently verified | A source that is not the originator (different org and domain, approved, not just discussion) reports **its own testing** supporting the claim, and the quote it gave is found in its text. |
| Disputed | Such an independent source contradicts the claim. |
| Only the originator's claim | Support exists, but only from the originator or outlets repeating it. |
| Could not verify | Nothing relevant found. |

The LLM never decides the label directly. This is what separates a vendor announcement from a third-party result (the plan's "Launch Reality Check") and it is unit-tested.

## Guardrails from the plan

- Approved sources only; page fetches are limited to allow-listed domains (no unrestricted browsing).
- Every source line records publisher, source type, publication time and retrieval time; arXiv items are marked "preprint, not peer reviewed".
- Unverified and disputed claims are listed explicitly ("What we could not verify").
- LLM call budget per run (`--max-calls`, default 20); each run logs calls, cost (USD) and duration in the ledger, for the plan's cost metric.
- Human approval before delivery.

## Run it

Python 3.10+, no third-party packages. LLM access: the Claude Code CLI (`claude -p`, default) or set `ANTHROPIC_API_KEY` to call the API directly.

```bash
python3 newsren.py topics
python3 newsren.py run independent-model-evals
python3 newsren.py pending
python3 newsren.py approve independent-model-evals <BRIEFING_ID> --editor <name>
python3 -m unittest discover tests      # label rules + offline end-to-end (alert -> approval -> dedup)
```

Add a topic by copying `topics/independent-model-evals.json`. Reports go to `out/<topic>/`; the ledger is `data/<topic>/ledger.json`.

## Sample live run (2026-10-25)

34 items discovered, 25 new, 2–4 judged relevant, 3–5 claims checked, all labelled *only the originator's claim* (e.g. Moonshot's own Kimi K3.1 Pro benchmark claims), so **no alert** was sent: correct behaviour for a reader who asked to hear only about independent results. About 5–7 LLM calls, $0.10–0.50, ~60–70 s per run. See `out/independent-model-evals/`.

## Known limits / next steps

- Few independent-eval sources have open feeds; adding LMArena, Artificial Analysis, Epoch AI or METR (by API or approved scraping) would raise recall the most.
- Hacker News search is keyword AND-matching; queries back off to shorter forms, but recall is still limited.
- Story timelines (plan 2.2) are only claim histories in the ledger; no clustering or timeline UI yet.
- Not built yet: Ask Newsren (Q&A), Research Paper Decoder, News-to-Learning audience levels, web UI, scheduled runs.
