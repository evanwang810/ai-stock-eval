"""
llm.py - the swappable LLM provider layer

Everything that talks to an LLM API lives here, isolated from the rest
of the app. To change provider or model, no code changes needed:

  LLM_PROVIDER=gemini     (default)  free tier, see rate limits below
  LLM_PROVIDER=cerebras              free ~1M tokens/day  (being discontinued)
  LLM_PROVIDER=groq                  free ~1K req/day
  LLM_MODEL=<anything>               override the model for any provider

All providers speak the OpenAI-compatible API, so we use the `openai`
client package for all of them (it is just the HTTP client - you do NOT
need an OpenAI account or key, your provider's key goes in as-is).

To add a new provider: add one entry to PROVIDERS below. That's it.

exports:
  PROVIDERS, PROVIDER, MODEL, CAPS
  make_client(api_key) -> client
  KeyPool(keys)        -> rotating multi-key pool
  RateLimiter          -> sliding-window RPM + TPM throttle
  call_llm(client_or_pool, system, prompt, spinner=None) -> (text, usage|None)
"""

import os
import re
import time
import threading

from openai import OpenAI

# auto-load .env if present (optional, plain env vars work too)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ── Provider registry ─────────────────────────────────────────────────────────
#
# Per provider we record not just the endpoint but what the API actually
# tolerates and how hard we're allowed to push it. Sending an unsupported
# parameter is a hard 400 on Gemini, so these are correctness flags, not hints.
#
#   reasoning  - accepts reasoning_effort (gpt-oss does; Gemma 400s on it)
#   seed       - accepts seed (Gemini's OpenAI shim rejects the field outright)
#   rpm / tpm  - free-tier ceilings, measured not guessed. tpm is usually the
#                binding one: a full analysis runs ~4.6K tokens, so 15K TPM caps
#                us near 3 requests/min regardless of the 30 RPM allowance.
#   max_tokens - default completion ceiling. Gemma 4 thinks *inside* the content
#                as a <thought> block, so it needs far more room than a model
#                that returns only the answer.
PROVIDERS = {
    # gemini: default. gemma-4-26b-a4b-it is a free-tier MoE instruct model.
    # NOTE: free-tier quota is per *project*, not per key - extra keys from the
    # same Google Cloud project share one bucket and buy you nothing. To
    # actually multiply throughput the keys must come from separate projects.
    #
    # THROUGHPUT WARNING (measured on one free project, same prompt):
    #   gemma-4-26b-a4b-it      ~196s, ~10.8K tokens per stock
    #   gemini-3.5-flash-lite     ~4s,  ~1.7K tokens per stock
    # Gemma's inline <thought> block dominates both numbers, and it expands to
    # fill whatever max_tokens allows. At ~2 stocks/min best case a 500-name
    # scan cannot finish inside the workflow's timeout on a single project.
    # Set LLM_MODEL=gemini-3.5-flash-lite to trade model size for a scan that
    # actually completes; both parsed identically in testing.
    "gemini":   dict(base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                     model="gemma-4-26b-a4b-it",
                     reasoning=False, seed=False, stream_usage=True,
                     rpm=30, tpm=15000, max_tokens=10000),
    # cerebras: ~1M tokens/day per key. Free tier is being discontinued.
    "cerebras": dict(base_url="https://api.cerebras.ai/v1",
                     model="gpt-oss-120b",
                     reasoning=True, seed=True, stream_usage=False,
                     rpm=30, tpm=0, max_tokens=16000),
    "groq":     dict(base_url="https://api.groq.com/openai/v1",
                     model="openai/gpt-oss-120b",
                     reasoning=True, seed=True, stream_usage=False,
                     rpm=30, tpm=0, max_tokens=16000),
}

PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").strip().lower()
if PROVIDER not in PROVIDERS:
    PROVIDER = "gemini"

CAPS  = PROVIDERS[PROVIDER]
MODEL = os.environ.get("LLM_MODEL", "").strip() or CAPS["model"]


def _env_int(name, default):
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


# How hard the model thinks (ignored by providers without reasoning support).
REASONING_EFFORT = os.environ.get("LLM_REASONING_EFFORT", "medium").strip().lower()
MAX_COMPLETION_TOKENS = _env_int("LLM_MAX_TOKENS", CAPS["max_tokens"])

# Rate limits. Override per deployment; 0 disables that dimension.
RPM_LIMIT = _env_int("LLM_RPM", CAPS.get("rpm", 0))
TPM_LIMIT = _env_int("LLM_TPM", CAPS.get("tpm", 0))


def make_client(api_key):
    """Create an OpenAI-compatible client for the configured provider."""
    return OpenAI(api_key=api_key, base_url=CAPS["base_url"])


# ── Response cleanup ──────────────────────────────────────────────────────────

# Gemma 4 emits its chain of thought inline in the message content wrapped in
# <thought>...</thought>, then the real answer. Left in, the parser happily
# scrapes numbers out of the model's internal monologue and returns nonsense -
# so strip it before anything downstream (or storage) ever sees it.
_THOUGHT_RE = re.compile(r"(?is)<thought>.*?</thought>\s*")


def strip_thoughts(text):
    """Remove inline reasoning blocks so only the final answer remains."""
    if not text:
        return text
    out = _THOUGHT_RE.sub("", text)
    # unterminated block (hit the token ceiling mid-thought): nothing usable
    # follows, so drop from the opening tag rather than parse the monologue.
    if "<thought>" in out.lower():
        out = re.split(r"(?i)<thought>", out)[0]
    return out.strip()


# ── Rate limiting ─────────────────────────────────────────────────────────────

class RateLimiter:
    """
    Sliding-window throttle over both requests/min and tokens/min.

    Gemini's free tier rejects on either axis, and tokens is the one that
    actually bites (a ~4.6K-token analysis exhausts a 15K TPM budget in three
    calls). acquire() blocks until sending is safe; record() feeds back the real
    usage so the estimate self-corrects instead of drifting.

    Thread-safe, and shared across a KeyPool by default because free-tier quota
    is per project - rotating keys within one project does not reset it.
    """

    def __init__(self, rpm=0, tpm=0, est_tokens=5000):
        self.rpm, self.tpm = rpm, tpm
        self._req, self._tok = [], []          # (ts) and (ts, ntokens)
        self._est = est_tokens
        self._lock = threading.Lock()

    def _prune(self, now):
        cut = now - 60.0
        self._req = [t for t in self._req if t > cut]
        self._tok = [(t, n) for t, n in self._tok if t > cut]

    def _wait_for(self, now):
        """Seconds to sleep before a request of ~self._est tokens is allowed."""
        waits = [0.0]
        if self.rpm and len(self._req) >= self.rpm:
            waits.append(self._req[0] + 60.0 - now)
        if self.tpm:
            used = sum(n for _, n in self._tok)
            if used + self._est > self.tpm:
                # wait until enough of the oldest tokens age out of the window
                need, freed = used + self._est - self.tpm, 0
                for t, n in self._tok:
                    freed += n
                    if freed >= need:
                        waits.append(t + 60.0 - now)
                        break
                else:
                    waits.append(60.0)
        return max(waits)

    def acquire(self, spinner=None):
        """Block until a request fits inside both windows, then reserve a slot."""
        while True:
            with self._lock:
                now = time.monotonic()
                self._prune(now)
                delay = self._wait_for(now)
                if delay <= 0:
                    self._req.append(now)
                    self._tok.append((now, self._est))   # provisional
                    return
            if spinner:
                spinner.message = f"Rate limit pacing - waiting {delay:.0f}s ..."
            time.sleep(min(delay + 0.05, 65))

    def record(self, actual_tokens):
        """Replace the provisional reservation with real usage; adapt estimate."""
        if not actual_tokens:
            return
        with self._lock:
            if self._tok:
                t, _ = self._tok[-1]
                self._tok[-1] = (t, actual_tokens)
            # EMA so the next estimate tracks reality
            self._est = int(0.7 * self._est + 0.3 * actual_tokens)


# the process-wide limiter (per project quota - see RateLimiter docstring)
LIMITER = RateLimiter(RPM_LIMIT, TPM_LIMIT)


# ── Key loading ───────────────────────────────────────────────────────────────

def _split_keys(raw):
    """split a blob of keys on commas / whitespace / newlines."""
    return [k.strip() for k in re.split(r"[\s,]+", raw or "") if k.strip()]


def load_keys():
    """
    Pull API keys from the environment, first var that has something wins:
      1. LLM_API_KEYS                              (generic, what you should use)
      2. GEMINI_API_KEYS / CEREBRAS_API_KEYS / GROQ_API_KEYS
      3. GEMINI_API_KEY / GROQ_API_KEY             (single-key fallback)
    Comma- or whitespace-separated. Returns a list, maybe empty.
    Put them in a .env file or export them - see README / .env.example.
    """
    for var in ("LLM_API_KEYS", "GEMINI_API_KEYS", "CEREBRAS_API_KEYS",
                "GROQ_API_KEYS", "GEMINI_API_KEY", "GROQ_API_KEY"):
        if os.environ.get(var):
            return _split_keys(os.environ[var])
    return []


# ── Key pool ──────────────────────────────────────────────────────────────────

class KeyPool:
    """
    Manages multiple API keys with automatic rotation on rate-limit or auth errors.
    Pass a KeyPool instead of a client to call_llm for multi-key support.

    Caveat for Gemini: free-tier quota is per project. If every key belongs to
    the same project, rotating on a 429 just hits the same exhausted bucket -
    the shared RateLimiter, not rotation, is what keeps you legal there.
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

def _retry_delay_from(err_text, default):
    """Honor the server's own retryDelay ("21s") when it supplies one."""
    m = re.search(r'retryDelay"?\s*:\s*"?(\d+)s', err_text)
    return int(m.group(1)) if m else default


def call_llm(client_or_pool, system, prompt, spinner=None, limiter=LIMITER):
    """
    calls the configured provider, streams to buffer, returns (text, usage | None)

    accepts either an OpenAI-compatible client or a KeyPool.  when given a
    KeyPool, automatically rotates to the next key on rate-limit or auth
    errors before falling back to timed waits.

    paces itself through `limiter` so we stay inside the provider's RPM/TPM
    budget instead of discovering it via 429s, and strips inline <thought>
    blocks so callers only ever see the final answer.

    spinner is optional - any object with .tick(n) and .message.
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
    RATE_WAITS    = [20, 45, 90]  # fallback waits when the server names none

    def _make_stream():
        current = pool.client if pool else client_or_pool
        # NB: no `stop=None` - Gemini rejects explicit nulls ("Value is not a
        # string: null"). Omit optional params entirely rather than nulling them.
        kw = dict(
            model=MODEL, messages=msgs, temperature=0.9,
            max_completion_tokens=MAX_COMPLETION_TOKENS, top_p=1,
            stream=True,
        )
        # Gemini only reports token usage if explicitly asked; without this the
        # limiter paces on a guess and either wastes budget or blows through it.
        if CAPS.get("stream_usage"):
            kw["stream_options"] = {"include_usage": True}
        # only send what this provider actually accepts - Gemini 400s otherwise
        if use_extras:
            if CAPS.get("reasoning"):
                kw["reasoning_effort"] = REASONING_EFFORT
            if CAPS.get("seed"):
                kw["seed"] = 6767
        return current.chat.completions.create(**kw)

    def _is_rate_limit(e):
        s = str(e).lower()
        return "rate limit" in s or "too many" in s or "quota" in s or "429" in str(e)

    def _is_auth_err(e):
        s = str(e).lower()
        return "401" in str(e) or "unauthorized" in s or "invalid api key" in s

    def _is_param_err(e):
        s = str(e).lower()
        return any(x in s for x in (
            "reasoning_effort", "unsupported", "not supported", "unknown field",
            "unknown name", "unexpected keyword", "invalid argument",
            "invalid_argument",
        ))

    def _countdown(seconds):
        for remaining in range(seconds, 0, -1):
            if spinner:
                spinner.message = f"Rate limited - retrying in {remaining}s ..."
            time.sleep(1)
        if spinner:
            spinner.message = "Generating analysis ..."

    while True:
        if limiter:
            limiter.acquire(spinner)

        # establish stream
        try:
            completion = _make_stream()
        except Exception as e:
            if _is_param_err(e) and use_extras:
                use_extras = False          # retry without the optional params
                continue
            if (_is_rate_limit(e) or _is_auth_err(e)) and pool and pool.rotate_on_error():
                continue  # retry immediately with next key
            if _is_rate_limit(e) and rate_attempts < len(RATE_WAITS):
                _countdown(_retry_delay_from(str(e), RATE_WAITS[rate_attempts]))
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
                _countdown(_retry_delay_from(str(e), RATE_WAITS[rate_attempts]))
                rate_attempts += 1
                stream_rate_hit = True
            else:
                raise

        if stream_rate_hit:
            continue

        # feed real usage back so the limiter's estimate tracks reality
        if limiter:
            total = getattr(usage, "total_tokens", None) if usage else None
            limiter.record(total or MAX_COMPLETION_TOKENS // 2)

        return strip_thoughts(full), usage
