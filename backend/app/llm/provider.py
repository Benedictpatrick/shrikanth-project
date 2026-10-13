"""LLM provider interface with a rate-limit-aware Groq implementation.

Groq (especially the free tier) enforces requests-per-minute, tokens-per-minute
and per-day quotas. The provider therefore:

* throttles itself with a sliding-window limiter (RPM + TPM) *before* sending,
* reads the ``x-ratelimit-*`` / ``retry-after`` headers and waits as told,
* retries 429/5xx with bounded back-off and jitter,
* treats an exhausted daily quota as a circuit breaker: it stops calling,
  reports ``available() == False`` and lets every agent fall back to its
  deterministic path instead of hanging for hours,
* tells agents how large a prompt may be (``prompt_char_budget``) so requests
  are shaped to fit the token budget instead of being rejected with HTTP 413,
* reports waits to the run log and exposes usage statistics to the UI.
"""
from __future__ import annotations

import contextlib
import json
import random
import re
import threading
import time
from collections import deque
from typing import Callable, Iterator, Protocol

import httpx

from ..config import settings


class LLMError(Exception):
    pass


class LLMQuotaError(LLMError):
    """Daily/long-window quota exhausted: retrying now cannot succeed."""


class LLMProvider(Protocol):
    name: str
    model: str

    def available(self) -> bool: ...

    def prompt_char_budget(self, max_tokens: int) -> int: ...

    def complete(self, system: str, user: str, *, json_mode: bool = False,
                 temperature: float = 0.1, max_tokens: int = 4096) -> str: ...


# ------------------------------------------------------------------ run-log hook
_sink = threading.local()


@contextlib.contextmanager
def llm_events(fn: Callable[[str], None]) -> Iterator[None]:
    """Route rate-limit wait messages of calls made in this thread to `fn`."""
    prev = getattr(_sink, "fn", None)
    _sink.fn = fn
    try:
        yield
    finally:
        _sink.fn = prev


def _notify(msg: str) -> None:
    fn = getattr(_sink, "fn", None)
    if fn:
        try:
            fn(msg)
        except Exception:  # logging must never break a call
            pass


# ------------------------------------------------------------------- rate limiter
class RateLimiter:
    """Sliding 60 s window over requests and tokens; blocks until a request fits."""

    def __init__(self, rpm: int, tpm: int, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.rpm, self.tpm = max(1, rpm), max(500, tpm)
        self.clock, self.sleep = clock, sleep
        self._events: deque[list] = deque()   # [timestamp, tokens]
        self._lock = threading.Lock()
        self.blocked_until = 0.0              # server-instructed pause (headers / 429)
        self.total_wait = 0.0
        self.throttled = 0

    def configure(self, rpm: int | None = None, tpm: int | None = None) -> None:
        if rpm:
            self.rpm = max(1, int(rpm))
        if tpm:
            self.tpm = max(500, int(tpm))

    def _purge(self, now: float) -> None:
        while self._events and now - self._events[0][0] >= 60:
            self._events.popleft()

    def window(self) -> tuple[int, int]:
        with self._lock:
            self._purge(self.clock())
            return len(self._events), sum(e[1] for e in self._events)

    def _wait_needed(self, est: int, now: float) -> tuple[float, str]:
        self._purge(now)
        if now < self.blocked_until:
            return self.blocked_until - now, "server asked us to wait"
        if len(self._events) >= self.rpm:
            return self._events[0][0] + 60 - now + 0.05, f"request limit ({self.rpm}/min)"
        used = sum(e[1] for e in self._events)
        if self._events and used + est > self.tpm:
            need, freed = used + est - self.tpm, 0
            for ts, tok in self._events:
                freed += tok
                if freed >= need:
                    return ts + 60 - now + 0.05, f"token limit ({self.tpm}/min)"
        return 0.0, ""

    def acquire(self, est_tokens: int, max_wait: float = 120.0) -> float:
        """Block until the request fits; returns seconds waited."""
        waited = 0.0
        while True:
            with self._lock:
                now = self.clock()
                delay, why = self._wait_needed(est_tokens, now)
                if delay <= 0:
                    self._events.append([now, est_tokens])
                    return waited
            if waited + delay > max_wait:
                raise LLMQuotaError(f"rate limit wait of {delay:.0f}s exceeds the {max_wait:.0f}s cap ({why})")
            self.throttled += 1
            _notify(f"Rate limit: waiting {delay:.0f}s for {why}")
            self.sleep(delay)
            waited += delay
            self.total_wait += delay

    def settle(self, actual_tokens: int) -> None:
        """Replace the estimate of the newest request with the real usage."""
        with self._lock:
            if self._events and actual_tokens > 0:
                self._events[-1][1] = actual_tokens

    def refund(self) -> None:
        """A request that failed did not consume tokens: keep the request, drop its token reservation."""
        with self._lock:
            if self._events:
                self._events[-1][1] = 0

    def block_for(self, seconds: float) -> None:
        with self._lock:
            self.blocked_until = max(self.blocked_until, self.clock() + max(0.0, seconds))


_DUR = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


def parse_duration(text: str | None) -> float | None:
    """'2m59.56s' -> 179.56, '7.66s' -> 7.66, '1h2m' -> 3720; plain numbers are seconds."""
    if not text:
        return None
    t = text.strip()
    try:
        return float(t)
    except ValueError:
        pass
    parts = _DUR.findall(t)
    if not parts:
        return None
    mult = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
    return sum(float(v) * mult[u] for v, u in parts)


def estimate_tokens(*texts: str) -> int:
    return sum(len(t) for t in texts) // 3 + 8   # ~3 chars/token: deliberately pessimistic for code


# ----------------------------------------------------------------------- providers
class NullProvider:
    """No LLM configured: agents fall back to their deterministic paths."""

    name = "none"
    model = "-"

    def available(self) -> bool:
        return False

    def prompt_char_budget(self, max_tokens: int) -> int:
        return 20000

    def complete(self, system: str, user: str, **_: object) -> str:
        raise LLMError("no LLM provider configured")

    def stats(self) -> dict:
        return {}


class GroqProvider:
    name = "groq"
    MAX_WAIT = 90.0          # never sleep longer than this for one rate-limit wait
    MAX_ATTEMPTS = 6

    def __init__(self, api_key: str, model: str, base_url: str, rpm: int | None = None,
                 tpm: int | None = None, client: httpx.Client | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")
        self.limiter = RateLimiter(rpm or settings.groq_rpm, tpm or settings.groq_tpm, clock, sleep)
        self.client = client or httpx.Client(timeout=120)
        self.clock, self.sleep = clock, sleep
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.exhausted_until = 0.0     # wall-clock epoch seconds (circuit breaker)
        self.exhausted_reason = ""
        self.last_error = ""
        self.daily_remaining: int | None = None

    # -- availability / budgets ---------------------------------------------------
    def available(self) -> bool:
        return bool(self.api_key) and time.time() >= self.exhausted_until

    def prompt_char_budget(self, max_tokens: int) -> int:
        """Largest prompt (characters) that fits the TPM budget next to `max_tokens`."""
        room = self.limiter.tpm - max_tokens - 400
        return max(2000, int(room * 3))

    def reset_breaker(self) -> None:
        self.exhausted_until = 0.0
        self.exhausted_reason = ""
        self.limiter.blocked_until = 0.0

    def stats(self) -> dict:
        reqs, toks = self.limiter.window()
        return {
            "calls": self.calls, "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "window_requests": reqs, "window_tokens": toks,
            "rpm": self.limiter.rpm, "tpm": self.limiter.tpm,
            "throttled": self.limiter.throttled, "waited_seconds": round(self.limiter.total_wait, 1),
            "exhausted_seconds": max(0, int(self.exhausted_until - time.time())),
            "exhausted_reason": self.exhausted_reason if time.time() < self.exhausted_until else "",
            "daily_requests_remaining": self.daily_remaining, "last_error": self.last_error,
        }

    # -- the call -------------------------------------------------------------------
    def complete(self, system: str, user: str, *, json_mode: bool = False,
                 temperature: float = 0.1, max_tokens: int = 4096) -> str:
        if time.time() < self.exhausted_until:
            raise LLMQuotaError(self.exhausted_reason or "Groq quota exhausted")
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        est = estimate_tokens(system, user) + max_tokens
        if est > self.limiter.tpm:
            raise LLMError(f"request needs ~{est} tokens but the configured limit is {self.limiter.tpm}/min; "
                           "shorten the prompt or raise the limit in Settings")
        last_err = "unknown error"
        for attempt in range(self.MAX_ATTEMPTS):
            self.limiter.acquire(est, self.MAX_WAIT)
            try:
                resp = self.client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
            except httpx.HTTPError as exc:
                last_err = f"network error: {exc}"
                self.limiter.refund()
                self._pause(min(2 ** attempt, 15) + random.random(), "network error")
                continue
            self._read_headers(resp)
            if resp.status_code == 200:
                data = resp.json()
                usage = data.get("usage", {})
                self.calls += 1
                self.prompt_tokens += int(usage.get("prompt_tokens", 0))
                self.completion_tokens += int(usage.get("completion_tokens", 0))
                self.limiter.settle(int(usage.get("total_tokens", 0)) or est)
                return data["choices"][0]["message"]["content"] or ""
            self.limiter.refund()
            text = resp.text[:400]
            last_err = f"HTTP {resp.status_code}: {text}"
            self.last_error = last_err
            if resp.status_code == 413:
                raise LLMError(f"request too large for the model/tier (HTTP 413): {text[:200]}")
            if resp.status_code in (401, 403):
                raise LLMError("Groq rejected the API key (unauthorized) - check it in Settings")
            if resp.status_code == 429:
                wait = self._retry_after(resp, text)
                day = bool(re.search(r"per day|\bTPD\b|\bRPD\b|daily", text, re.I))
                if day or wait > self.MAX_WAIT:
                    reset = wait if wait > 0 else 3600.0
                    self.exhausted_until = time.time() + min(reset, 6 * 3600)
                    self.exhausted_reason = ("daily quota reached" if day else f"rate limit reset in {reset:.0f}s") \
                        + f"; LLM disabled for about {min(reset, 6 * 3600) / 60:.0f} min"
                    _notify(f"Groq {self.exhausted_reason} - continuing without the LLM")
                    raise LLMQuotaError(f"Groq quota exhausted: {text[:200]}")
                self.limiter.block_for(wait)
                last_err = f"rate limited (429), waiting {wait:.0f}s"
                continue  # acquire() at the top of the loop sleeps until blocked_until
            if resp.status_code >= 500:
                self._pause(min(2 ** (attempt + 1), 30) + random.random(), f"Groq server error {resp.status_code}")
                continue
            if resp.status_code == 400 and json_mode and "json" in resp.text.lower() and "response_format" in body:
                body.pop("response_format")  # model without JSON mode: rely on the prompt
                continue
            break
        self.last_error = last_err
        raise LLMError(f"Groq request failed: {last_err}")

    def _pause(self, seconds: float, why: str) -> None:
        _notify(f"{why}: retrying in {seconds:.0f}s")
        self.limiter.total_wait += seconds
        self.sleep(seconds)

    def _retry_after(self, resp: httpx.Response, text: str) -> float:
        for h in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
            v = parse_duration(resp.headers.get(h))
            if v is not None:
                return v + 0.5
        m = re.search(r"try again in ([0-9hms.]+)", text)
        v = parse_duration(m.group(1)) if m else None
        return (v if v is not None else 10.0) + 0.5

    def _read_headers(self, resp: httpx.Response) -> None:
        """Honour what the server says about the remaining budget."""
        h = resp.headers
        rem_req = h.get("x-ratelimit-remaining-requests")
        rem_tok = h.get("x-ratelimit-remaining-tokens")
        if rem_req is not None and rem_req.isdigit():
            self.daily_remaining = int(rem_req)
        if rem_tok is not None and rem_tok.isdigit() and int(rem_tok) < 400:
            reset = parse_duration(h.get("x-ratelimit-reset-tokens"))
            if reset:
                self.limiter.block_for(min(reset, self.MAX_WAIT))

    def list_models(self) -> list[str]:
        resp = self.client.get(f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"},
                               timeout=30)
        resp.raise_for_status()
        return sorted(m["id"] for m in resp.json().get("data", []))


_provider: LLMProvider | None = None


def get_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        if settings.llm_provider == "groq" and settings.groq_api_key:
            _provider = GroqProvider(settings.groq_api_key, settings.groq_model, settings.groq_base_url)
        else:
            _provider = NullProvider()
    return _provider


def reset_provider() -> None:
    global _provider
    _provider = None


def complete_json(llm: LLMProvider, system: str, user: str, *, max_tokens: int = 2000) -> dict:
    """Ask for a JSON object; one corrective retry if the reply is not valid JSON."""
    system = system + "\nReply with a single valid JSON object and nothing else."
    text = llm.complete(system, user, json_mode=True, max_tokens=max_tokens)
    for attempt in range(2):
        try:
            return json.loads(_strip_fence(text))
        except json.JSONDecodeError as exc:
            if attempt:
                raise LLMError(f"model did not return valid JSON: {exc}") from exc
            text = llm.complete(system, user + f"\n\nYour previous reply was not valid JSON ({exc}). "
                                "Return only the JSON object.", json_mode=True, max_tokens=max_tokens)
    raise LLMError("unreachable")


def _strip_fence(text: str) -> str:
    t = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.DOTALL)
    return m.group(1) if m else t


def extract_python(text: str) -> str:
    """Pull the module source out of a model reply."""
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL)
    if blocks:
        return max(blocks, key=len).strip() + "\n"
    return text.strip() + "\n"
