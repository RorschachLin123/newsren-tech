"""LLM access with a per-run call budget and a cost meter. No third-party packages.

Providers (pick with NEWSREN_LLM in .env; see .env.example):
  - "deepseek":          DeepSeek's OpenAI-compatible API, key in DEEPSEEK_API_KEY.
  - "anthropic":         the Anthropic Messages API, key in ANTHROPIC_API_KEY.
  - "openai-compatible": any other OpenAI-compatible endpoint; set NEWSREN_BASE_URL, NEWSREN_MODEL and
                         NEWSREN_API_KEY (and NEWSREN_PRICE_IN / NEWSREN_PRICE_OUT for the cost meter).
  - "claude-cli":        the Claude Code CLI in headless mode (`claude -p`), tools disabled; billed to the
                         CLI's own login.
Without NEWSREN_LLM: deepseek if DEEPSEEK_API_KEY is set, else anthropic if ANTHROPIC_API_KEY is set,
else claude-cli.
"""
import json
import os
import re
import subprocess
import urllib.error
import urllib.request

from .sources import SSL

SYSTEM = (
    "You are one step inside an editorially controlled news-verification pipeline. "
    "Use only the material given in the prompt; never add facts from memory. "
    "Reply with a single JSON value and nothing else."
)

# price = USD per 1M (input, output) tokens, list price checked 2026-10-25. DeepSeek's is the peak rate
# (off-peak is half), so the meter is an upper bound.
PROVIDERS = {
    "deepseek": {"kind": "openai", "base_url": "https://api.deepseek.com", "key_env": "DEEPSEEK_API_KEY",
                 "model": "deepseek-flash", "price": (0.30, 1.20)},
    "anthropic": {"kind": "anthropic", "base_url": "https://api.anthropic.com", "key_env": "ANTHROPIC_API_KEY",
                  "model": "claude-sonnet-5-5", "price": (2.0, 10.0)},
    "openai-compatible": {"kind": "openai", "base_url": None, "key_env": "NEWSREN_API_KEY",
                          "model": None, "price": (0.0, 0.0)},
    "claude-cli": {"kind": "cli", "base_url": None, "key_env": None, "model": "sonnet", "price": None},
}
ALIASES = {"anthropic-api": "anthropic"}


class BudgetExceeded(RuntimeError):
    pass


def settings(env=None):
    """Resolve the provider, model, endpoint, key and price from the environment."""
    env = os.environ if env is None else env
    name = env.get("NEWSREN_LLM") or (
        "deepseek" if env.get("DEEPSEEK_API_KEY") else "anthropic" if env.get("ANTHROPIC_API_KEY") else "claude-cli")
    name = ALIASES.get(name, name)
    if name not in PROVIDERS:
        raise SystemExit(f"Unknown NEWSREN_LLM '{name}'. Use one of: {', '.join(PROVIDERS)}")
    p = dict(PROVIDERS[name], provider=name)
    p["model"] = env.get("NEWSREN_MODEL") or p["model"]
    p["base_url"] = (env.get("NEWSREN_BASE_URL") or p["base_url"] or "").rstrip("/") or None
    p["api_key"] = env.get(p["key_env"]) if p["key_env"] else None
    if env.get("NEWSREN_PRICE_IN") or env.get("NEWSREN_PRICE_OUT"):
        p["price"] = (float(env.get("NEWSREN_PRICE_IN") or 0), float(env.get("NEWSREN_PRICE_OUT") or 0))
    p["thinking"] = env.get("NEWSREN_THINKING", "disabled")  # DeepSeek only
    return p


class LLM:
    def __init__(self, max_calls=20, model=None, backend=None):
        self.max_calls = max_calls
        self.calls = 0
        self.cost_usd = 0.0
        self.tokens_in = self.tokens_out = 0
        env = dict(os.environ)
        if backend:
            env["NEWSREN_LLM"] = backend
        if model:
            env["NEWSREN_MODEL"] = model
        self.cfg = settings(env)
        self.backend, self.model = self.cfg["provider"], self.cfg["model"]

    def json(self, prompt):
        """Send a prompt, return the parsed JSON reply."""
        if self.calls >= self.max_calls:
            raise BudgetExceeded(f"LLM call budget of {self.max_calls} reached")
        self.calls += 1
        kind = self.cfg["kind"]
        text = self._cli(prompt) if kind == "cli" else self._anthropic(prompt) if kind == "anthropic" else self._openai(prompt)
        return _parse_json(text)

    def _meter(self, tokens_in, tokens_out):
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        price_in, price_out = self.cfg["price"]
        self.cost_usd += (tokens_in * price_in + tokens_out * price_out) / 1e6

    def _require(self, *keys):
        missing = [k for k in keys if not self.cfg.get(k)]
        if missing:
            names = {"api_key": self.cfg["key_env"], "base_url": "NEWSREN_BASE_URL", "model": "NEWSREN_MODEL"}
            raise SystemExit(f"Provider '{self.backend}' needs {', '.join(names[k] for k in missing)} in .env")

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

    def _anthropic(self, prompt):
        self._require("api_key")
        data = _post(f"{self.cfg['base_url']}/v1/messages", {
            "model": self.model, "max_tokens": 8192, "system": SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
        }, {"x-api-key": self.cfg["api_key"], "anthropic-version": "2023-06-01"})
        usage = data.get("usage", {})
        self._meter(usage.get("input_tokens", 0), usage.get("output_tokens", 0))
        return "".join(b.get("text", "") for b in data["content"])

    def _openai(self, prompt):
        self._require("api_key", "base_url", "model")
        body = {"model": self.model, "max_tokens": 8192,
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]}
        if self.backend == "deepseek":
            body["thinking"] = {"type": self.cfg["thinking"]}
        data = _post(f"{self.cfg['base_url']}/chat/completions", body,
                     {"Authorization": f"Bearer {self.cfg['api_key']}"})
        usage = data.get("usage", {})
        self._meter(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        return data["choices"][0]["message"]["content"]


def label(llm):
    """'deepseek/deepseek-flash' etc., for run records and report footers."""
    return f"{getattr(llm, 'backend', '?')}/{getattr(llm, 'model', '?')}"


def _post(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=300, context=SSL) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"LLM API error {e.code} from {url}: {e.read()[:300].decode(errors='replace')}") from None


def check_key(cfg):
    """Confirm the key works without generating anything (model listing is free). Returns (ok, message)."""
    if cfg["kind"] == "cli":
        try:
            out = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=30)
            return out.returncode == 0, out.stdout.strip() or out.stderr.strip()
        except FileNotFoundError:
            return False, "claude CLI not found on PATH"
    if not cfg["api_key"]:
        return False, f"{cfg['key_env']} is not set"
    if not cfg["base_url"]:
        return False, "NEWSREN_BASE_URL is not set"
    if cfg["kind"] == "anthropic":
        url, headers = f"{cfg['base_url']}/v1/models", {"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01"}
    else:
        url, headers = f"{cfg['base_url']}/models", {"Authorization": f"Bearer {cfg['api_key']}"}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20, context=SSL) as r:
            ids = [m.get("id") for m in json.load(r).get("data", [])]
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code} from {url} (check the key)"
    except OSError as e:
        return False, f"cannot reach {url}: {e}"
    if cfg["model"] and ids and cfg["model"] not in ids:
        return False, f"key works, but model '{cfg['model']}' is not offered; available: {', '.join(ids[:8])}"
    return True, f"key accepted; model '{cfg['model']}' available"


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
