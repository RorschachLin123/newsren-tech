"""LLM access with a per-run call budget.

Two backends, no third-party packages:
  - "claude-cli" (default): the Claude Code CLI in headless mode (`claude -p`), tools disabled.
  - "anthropic-api": the Messages API over HTTPS, used when ANTHROPIC_API_KEY is set.
"""
import json
import os
import re
import subprocess
import urllib.request

SYSTEM = (
    "You are one step inside an editorially controlled news-verification pipeline. "
    "Use only the material given in the prompt; never add facts from memory. "
    "Reply with a single JSON value and nothing else."
)


class BudgetExceeded(RuntimeError):
    pass


class LLM:
    def __init__(self, max_calls=20, model=None, backend=None):
        self.max_calls = max_calls
        self.calls = 0
        self.cost_usd = 0.0
        self.backend = backend or os.environ.get("NEWSREN_LLM") or (
            "anthropic-api" if os.environ.get("ANTHROPIC_API_KEY") else "claude-cli")
        self.model = model or os.environ.get("NEWSREN_MODEL") or (
            "claude-sonnet-5-5" if self.backend == "anthropic-api" else "sonnet")

    def json(self, prompt):
        """Send a prompt, return the parsed JSON reply."""
        if self.calls >= self.max_calls:
            raise BudgetExceeded(f"LLM call budget of {self.max_calls} reached")
        self.calls += 1
        text = self._api(prompt) if self.backend == "anthropic-api" else self._cli(prompt)
        return _parse_json(text)

    def _cli(self, prompt):
        cmd = ["claude", "-p", "--model", self.model, "--tools", "", "--setting-sources", "",
               "--no-session-persistence", "--output-format", "json", "--system-prompt", SYSTEM]
        out = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=300)
        if out.returncode != 0:
            raise RuntimeError(f"claude CLI failed: {out.stderr[-500:]}")
        data = json.loads(out.stdout)
        if data.get("is_error"):
            raise RuntimeError(f"claude CLI error: {data.get('result')}")
        self.cost_usd += data.get("total_cost_usd") or 0.0
        return data["result"]

    def _api(self, prompt):
        body = json.dumps({
            "model": self.model, "max_tokens": 4096, "system": SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body, method="POST",
            headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                     "anthropic-version": "2023-06-01", "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.load(r)
        return "".join(b.get("text", "") for b in data["content"])


def _parse_json(text):
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise
        return json.JSONDecoder().raw_decode(text[start:])[0]
