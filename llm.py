"""
llm.py - the swappable LLM provider layer

Everything that talks to an LLM API lives here, isolated from the rest
of the app. To change provider or model, no code changes needed:

  LLM_PROVIDER=groq       (default)  free ~1K req/day,  ~100K tokens/day
  LLM_PROVIDER=cerebras              free ~1M tokens/day  (cloud.cerebras.ai)
  LLM_PROVIDER=gemini                free ~1.5K req/day   (aistudio.google.com)
  LLM_MODEL=<anything>               override the model for any provider

All providers speak the OpenAI-compatible API, so we use the `openai`
client package for all of them (it is just the HTTP client - you do NOT
need an OpenAI account or key, your provider's key goes in as-is).

To add a new provider: add one entry to PROVIDERS below. That's it.

exports:
  PROVIDERS, PROVIDER, MODEL
  make_client(api_key) -> client
  KeyPool(keys)        -> rotating multi-key pool
  call_llm(client_or_pool, system, prompt, spinner=None) -> (text, usage|None)
"""

import os
import re
import time

from openai import OpenAI

# auto-load .env if present (optional, plain env vars work too)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ── Provider registry ─────────────────────────────────────────────────────────

PROVIDERS = {
    # cerebras: ~1M tokens/day per key, fastest throughput. gpt-oss-120b follows
    # the structured format reliably and supports reasoning_effort (low/medium/
    # high). reasoning tokens count against max_completion_tokens, so "high" effort
    # plus a roomy ceiling pushes each analysis to ~10K tokens (was ~1.9K at
    # medium/1500). round-robin across multiple keys keeps you under the per-minute
    # rate limit. the other free-tier model, zai-glm-4.7, needs extra parsing.
    "cerebras": dict(base_url="https://api.cerebras.ai/v1",
                     model="gpt-oss-120b"),
    "groq":     dict(base_url="https://api.groq.com/openai/v1",
                     model="openai/gpt-oss-120b"),
    "gemini":   dict(base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                     model="gemini-2.5-flash"),
}

PROVIDER = os.environ.get("LLM_PROVIDER", "cerebras").strip().lower()
if PROVIDER not in PROVIDERS:
    PROVIDER = "cerebras"

MODEL = os.environ.get("LLM_MODEL", "").strip() or PROVIDERS[PROVIDER]["model"]

# How hard the model thinks, and the token ceiling per analysis. gpt-oss spends
# reasoning tokens against max_completion_tokens; reasoning is the bulk of the
# cost. "high" ran ~8K tokens/ticker (~800K/key, near the 1M/key/day cap on a
# 500-stock scan), so we default to "medium" - it roughly halves the thinking
# tokens without changing the prompt at all. Override via env (low = cheapest,
# high = most thorough); LLM_MAX_TOKENS only caps runaway, it doesn't force usage.
REASONING_EFFORT = os.environ.get("LLM_REASONING_EFFORT", "medium").strip().lower()
try:
    MAX_COMPLETION_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "16000"))
except ValueError:
    MAX_COMPLETION_TOKENS = 16000


def make_client(api_key):
    """Create an OpenAI-compatible client for the configured provider."""
    return OpenAI(api_key=api_key, base_url=PROVIDERS[PROVIDER]["base_url"])


# ── Key loading ───────────────────────────────────────────────────────────────

def _split_keys(raw):
    """split a blob of keys on commas / whitespace / newlines."""
    return [k.strip() for k in re.split(r"[\s,]+", raw or "") if k.strip()]


def load_keys():
    """
    Pull API keys from the environment, first var that has something wins:
      1. LLM_API_KEYS                              (generic, what you should use)
      2. CEREBRAS_API_KEYS / GROQ_API_KEYS / GEMINI_API_KEYS
      3. GROQ_API_KEY                              (single-key fallback, legacy)
    Comma- or whitespace-separated. Returns a list, maybe empty.
    Put them in a .env file or export them - see README / .env.example.
    """
    for var in ("LLM_API_KEYS", "CEREBRAS_API_KEYS", "GROQ_API_KEYS",
                "GEMINI_API_KEYS", "GROQ_API_KEY"):
        if os.environ.get(var):
            return _split_keys(os.environ[var])
    return []


# ── Key pool ──────────────────────────────────────────────────────────────────

class KeyPool:
    """
    Manages multiple API keys with automatic rotation on rate-limit or auth errors.
    Pass a KeyPool instead of a client to call_llm for multi-key support.
    """

    def __init__(self, keys):
        self._keys = [k.strip() for k in keys if k and k.strip()]
        if not self._keys:
            raise ValueError("KeyPool requires at least one API key")
        self._idx = 0
        self._rotations = 0
        self._client = make_client(self._keys[0])

    @property
    def client(self):
        return self._client

    def advance(self):
        """
        Round-robin to the next key. Called once per request so consecutive
        analyses hit different keys - spreads per-minute rate-limit load across
        the whole pool instead of hammering one key until it 429s.
        """
        if len(self._keys) > 1:
            self._idx = (self._idx + 1) % len(self._keys)
            self._client = make_client(self._keys[self._idx])

    def reset_for_new_call(self):
        """Reset the error-rotation budget at the start of each call_llm invocation."""
        self._rotations = 0

    def rotate_on_error(self):
        """
        Switch to the next untried key after an error.
        Returns True if a fresh key was available and we rotated, False if all exhausted.
        """
        if self._rotations >= len(self._keys) - 1:
            return False
        self._idx = (self._idx + 1) % len(self._keys)
        self._client = make_client(self._keys[self._idx])
        self._rotations += 1
        return True

    def __len__(self):
        return len(self._keys)


# ── The call ──────────────────────────────────────────────────────────────────

def call_llm(client_or_pool, system, prompt, spinner=None):
    """
    calls the configured provider, streams to buffer, returns (text, usage | None)

    accepts either an OpenAI-compatible client or a KeyPool.  when given a
    KeyPool, automatically rotates to the next key on rate-limit or auth
    errors before falling back to timed waits.

    spinner is optional - any object with .tick(n) and .message.
    tick() gets called per chunk so you can show progress.
    .message gets updated during rate limit countdowns.

    auto-retries on 429s up to 3 times.
    drops optional params (reasoning_effort, seed) if the provider rejects them.
    """
    pool = client_or_pool if isinstance(client_or_pool, KeyPool) else None
    if pool:
        pool.advance()           # round-robin: spread load across keys
        pool.reset_for_new_call()

    msgs = [
        {"role": "system", "content": system},
        {"role": "user",   "content": prompt},
    ]

    use_extras    = True          # flipped once if provider rejects optional params
    rate_attempts = 0
    RATE_WAITS    = [20, 45, 90]  # seconds to wait on successive rate-limit hits

    def _make_stream():
        current = pool.client if pool else client_or_pool
        kw = dict(
            model=MODEL, messages=msgs, temperature=0.9,
            max_completion_tokens=MAX_COMPLETION_TOKENS, top_p=1,
            stream=True, stop=None,
        )
        if use_extras:
            kw["reasoning_effort"] = REASONING_EFFORT
            kw["seed"] = 6767
        return current.chat.completions.create(**kw)

    def _is_rate_limit(e):
        s = str(e).lower()
        return "rate limit" in s or "too many" in s or "429" in str(e)

    def _is_auth_err(e):
        s = str(e).lower()
        return "401" in str(e) or "unauthorized" in s or "invalid api key" in s

    def _is_reasoning_err(e):
        s = str(e).lower()
        return any(x in s for x in (
            "reasoning_effort", "unsupported", "unknown field",
            "unexpected keyword", "invalid argument",
        ))

    def _countdown(seconds):
        for remaining in range(seconds, 0, -1):
            if spinner:
                spinner.message = f"Rate limited - retrying in {remaining}s ..."
            time.sleep(1)
        if spinner:
            spinner.message = "Generating analysis ..."

    while True:
        # establish stream
        try:
            completion = _make_stream()
        except Exception as e:
            if _is_reasoning_err(e) and use_extras:
                use_extras = False
                continue
            if (_is_rate_limit(e) or _is_auth_err(e)) and pool and pool.rotate_on_error():
                continue  # retry immediately with next key
            if _is_rate_limit(e) and rate_attempts < len(RATE_WAITS):
                _countdown(RATE_WAITS[rate_attempts])
                rate_attempts += 1
                continue
            raise

        # drain stream
        full            = ""
        usage           = None
        stream_rate_hit = False
        try:
            for chunk in completion:
                # token counts: standard OpenAI puts them on chunk.usage,
                # Groq puts them in x_groq on the final chunk
                try:
                    if getattr(chunk, "usage", None) is not None:
                        usage = chunk.usage
                    xg = getattr(chunk, "x_groq", None)
                    if xg is not None and getattr(xg, "usage", None) is not None:
                        usage = xg.usage
                except Exception:
                    pass
                # Guard against empty-choices usage chunks
                try:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta is None:
                        continue
                    piece = delta.content or ""
                except (AttributeError, IndexError):
                    continue
                if piece:
                    if spinner:
                        spinner.tick(len(piece.split()))
                    full += piece
        except Exception as e:
            if (_is_rate_limit(e) or _is_auth_err(e)) and pool and pool.rotate_on_error():
                stream_rate_hit = True  # retry with next key
            elif _is_rate_limit(e) and rate_attempts < len(RATE_WAITS):
                _countdown(RATE_WAITS[rate_attempts])
                rate_attempts += 1
                stream_rate_hit = True
            else:
                raise

        if stream_rate_hit:
            continue

        return full, usage
