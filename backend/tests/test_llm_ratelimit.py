"""Rate-limit behaviour of the Groq provider and its consumers (no network, fake clock)."""
import json

import httpx
import pytest

from app.agents.rules import extract_rules
from app.agents.translator import migrate_unit
from app.cobol.parser import parse_program
from app.llm.provider import (GroqProvider, LLMError, LLMQuotaError, RateLimiter, llm_events,
                              parse_duration)
from app.verification import harness

from conftest import SAMPLE


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def ok_reply(total_tokens=50, headers=None):
    return httpx.Response(200, headers=headers or {}, json={
        "choices": [{"message": {"content": "hello"}}],
        "usage": {"prompt_tokens": total_tokens - 10, "completion_tokens": 10, "total_tokens": total_tokens}})


def provider(handler, clock=None, rpm=30, tpm=12000):
    clock = clock or Clock()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    p = GroqProvider("gsk_test_key_1234", "m", "https://api.test/v1", rpm=rpm, tpm=tpm, client=client,
                     clock=clock.now, sleep=clock.sleep)
    return p, clock


# --------------------------------------------------------------------- limiter
def test_parse_duration():
    assert parse_duration("7.66s") == pytest.approx(7.66)
    assert parse_duration("2m59.56s") == pytest.approx(179.56)
    assert parse_duration("1h2m") == 3720
    assert parse_duration("350ms") == pytest.approx(0.35)
    assert parse_duration("12") == 12
    assert parse_duration("soon") is None


def test_request_per_minute_budget_blocks_until_window_slides():
    c = Clock()
    lim = RateLimiter(rpm=3, tpm=100000, clock=c.now, sleep=c.sleep)
    for _ in range(3):
        lim.acquire(10)
    assert c.sleeps == []
    waited = lim.acquire(10)  # 4th request inside the same minute
    assert 59 < waited < 61
    assert lim.throttled == 1


def test_token_per_minute_budget_blocks_until_tokens_expire():
    c = Clock()
    lim = RateLimiter(rpm=100, tpm=1000, clock=c.now, sleep=c.sleep)
    lim.acquire(600)
    c.t += 20
    waited = lim.acquire(600)  # 600 + 600 > 1000: wait for the first to leave the window
    assert 39 < waited < 41


def test_wait_longer_than_cap_raises_instead_of_hanging():
    c = Clock()
    lim = RateLimiter(rpm=1, tpm=100000, clock=c.now, sleep=c.sleep)
    lim.acquire(10)
    with pytest.raises(LLMQuotaError):
        lim.acquire(10, max_wait=5)


# -------------------------------------------------------------------- provider
def test_429_with_retry_after_waits_then_succeeds():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "7"}, text="Rate limit reached, try again in 7s")
        return ok_reply()

    p, c = provider(handler)
    assert p.complete("s", "u") == "hello"
    assert len(calls) == 2
    assert any(s >= 7 for s in c.sleeps)
    assert p.available()


def test_daily_quota_trips_circuit_breaker_and_stops_calling():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "7200"},
                              text="Rate limit reached for model on tokens per day (TPD): Limit 100000")

    p, c = provider(handler)
    with pytest.raises(LLMQuotaError):
        p.complete("s", "u")
    assert not p.available()
    assert "daily" in p.stats()["exhausted_reason"]
    before = len(calls)
    with pytest.raises(LLMQuotaError):
        p.complete("s", "u")
    assert len(calls) == before  # breaker: no further HTTP traffic
    p.reset_breaker()
    assert p.available()


def test_response_headers_make_the_next_call_wait():
    calls = []

    def handler(req):
        calls.append(1)
        return ok_reply(headers={"x-ratelimit-remaining-tokens": "20", "x-ratelimit-reset-tokens": "9s",
                                 "x-ratelimit-remaining-requests": "812"})

    p, c = provider(handler)
    p.complete("s", "u")
    assert c.sleeps == []
    p.complete("s", "u")
    assert any(8.5 <= s <= 9.5 for s in c.sleeps)
    assert p.stats()["daily_requests_remaining"] == 812


def test_server_errors_back_off_and_recover():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503, text="overloaded") if len(calls) < 3 else ok_reply()

    p, c = provider(handler)
    assert p.complete("s", "u") == "hello"
    assert len(calls) == 3 and len(c.sleeps) == 2


def test_413_is_reported_without_retrying():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(413, text="Request too large for model, tokens per minute (TPM)")

    p, _ = provider(handler)
    with pytest.raises(LLMError, match="413"):
        p.complete("s", "u")
    assert len(calls) == 1


def test_bad_key_is_a_clear_error():
    p, _ = provider(lambda req: httpx.Response(401, text="invalid api key"))
    with pytest.raises(LLMError, match="unauthorized"):
        p.complete("s", "u")


def test_oversized_prompt_is_refused_locally():
    calls = []
    p, _ = provider(lambda req: calls.append(1) or ok_reply(), tpm=1000)
    with pytest.raises(LLMError, match="needs ~"):
        p.complete("s", "x" * 20000, max_tokens=500)
    assert calls == []


def test_prompt_budget_follows_tpm_and_stats_track_usage():
    small, _ = provider(lambda r: ok_reply(), tpm=6000)
    large, _ = provider(lambda r: ok_reply(), tpm=30000)
    assert large.prompt_char_budget(2000) > small.prompt_char_budget(2000) > 2000
    small.complete("s", "u")
    st = small.stats()
    assert st["calls"] == 1 and st["window_requests"] == 1 and st["window_tokens"] == 50


def test_waits_are_reported_to_the_run_log():
    c = Clock()
    p, _ = provider(lambda r: ok_reply(), clock=c, rpm=1)
    seen = []
    with llm_events(seen.append):
        p.complete("s", "u")
        p.complete("s", "u")
    assert any("Rate limit: waiting" in m for m in seen)


# ------------------------------------------------------------ graceful degradation
class QuotaLLM:
    name, model = "groq", "m"

    def available(self):
        return True

    def prompt_char_budget(self, n):
        return 20000

    def complete(self, *a, **k):
        raise LLMQuotaError("Groq quota exhausted")


def test_rule_extraction_falls_back_to_static_analysis_on_quota():
    m = parse_program(SAMPLE / "src" / "INTCALC.cbl")
    res = extract_rules(m, (SAMPLE / "src" / "INTCALC.cbl").read_text(), QuotaLLM())
    assert res["rules"] and not res["used_llm"]
    assert "quota" in res["summary"].lower()


def test_repair_loop_stops_cleanly_on_quota(tmp_path):
    m = parse_program(SAMPLE / "src" / "NAMEFMT.cbl")
    res = migrate_unit(m, (SAMPLE / "src" / "NAMEFMT.cbl").read_text(), harness.load_fixture_cases(SAMPLE, "NAMEFMT"),
                       "fixture", tmp_path, QuotaLLM(), 3)
    assert res.status == "blocked"
    assert any("quota" in n.lower() for n in res.notes)
    assert len(res.attempts) == 1


# ------------------------------------------------------------------ settings API
def test_settings_api_applies_limits_and_persists_to_env(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.llm import provider as prov
    from app.main import app

    monkeypatch.setattr(settings, "env_file", tmp_path / ".env")
    for attr in ("groq_rpm", "groq_tpm", "groq_model", "groq_api_key", "llm_provider"):
        monkeypatch.setattr(settings, attr, getattr(settings, attr))  # restored after the test
    monkeypatch.setattr("app.main._check_model", lambda llm: (None, ""))  # no network in tests
    (tmp_path / ".env").write_text("# my settings\nOTHER=1\nGROQ_RPM=5\n")
    client = TestClient(app)
    r = client.post("/api/settings/llm", json={"api_key": "gsk_abcdefghijklmnop1234", "model": "m1",
                                               "rpm": 20, "tpm": 9000, "persist": True})
    assert r.status_code == 200
    body = r.json()
    assert body["persisted"] and body["key_hint"].endswith("1234") and body["rpm"] == 20 and body["tpm"] == 9000
    assert "gsk_abcdefghijklmnop1234" not in json.dumps(body)  # the key is never echoed back
    text = (tmp_path / ".env").read_text()
    assert "OTHER=1" in text and "# my settings" in text
    assert "GROQ_RPM=20" in text and "GROQ_TPM=9000" in text and "GROQ_API_KEY=gsk_abcdefghijklmnop1234" in text
    assert text.count("GROQ_RPM") == 1
    # removing the key disables the provider
    r = client.post("/api/settings/llm", json={"api_key": ""})
    assert r.json()["available"] is False
    prov.reset_provider()
