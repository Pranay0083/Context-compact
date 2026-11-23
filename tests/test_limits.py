"""Rate-limit / quota handling, with a fake handler and no real sleeping."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from experiment.limits import DailyQuotaExceeded, RateLimitMiddleware, limit_kind, parse_wait

TPD = ("Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-120b` in organization "
       "`org_x` service tier `on_demand` on tokens per day (TPD): Limit 200000, Used 197794, Requested 2879. "
       "Please try again in 4m50.736s.', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}")


class E(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status_code = status


class Req(SimpleNamespace):
    def override(self, **kw):
        return Req(**{**self.__dict__, **kw})


def req(model="m1"):
    return Req(model=SimpleNamespace(model_name=model), system_message=None, messages=[HumanMessage("hi")], tools=[])


def ok(model):
    return SimpleNamespace(result=[AIMessage("done", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})])


def test_parse_and_classify():
    assert parse_wait(E(TPD)) == pytest.approx(290.736)
    assert parse_wait(E("Please try again in 7.5s")) == 7.5
    assert parse_wait(E("try again in 1h2m3s")) == 3723
    assert parse_wait(E("try again in 250ms")) == 0.25
    assert limit_kind(E(TPD, 429)) == "tpd"
    assert limit_kind(E("Request too large for model on tokens per minute (TPM)", 413)) == "too_large"
    assert limit_kind(E("on tokens per minute (TPM): Limit 8000", 429)) == "tpm"
    assert limit_kind(E("attempted to call tool 'read_file' which does not match request.tool_choice", 400)) == "tool_choice"
    assert limit_kind(E("tool_use_failed: bad json", 400)) == "tool_use_failed"
    assert limit_kind(E("boom")) is None


def test_short_daily_wait_is_waited_out():
    slept, events, calls = [], [], []
    mw = RateLimitMiddleware(tpm_limit=None, sleep=slept.append, on_event=events.append)

    def handler(r):
        calls.append(r.model.model_name)
        if len(calls) == 1:
            raise E(TPD, 429)
        return ok(r.model)

    mw.wrap_model_call(req(), handler)
    assert calls == ["m1", "m1"] and slept == [pytest.approx(291.736)]
    assert events[0]["type"] == "rate_limit" and events[0]["kind"] == "tpd"


def test_long_daily_wait_falls_back_then_gives_up():
    events = []
    long_tpd = TPD.replace("4m50.736s", "3h10m2s")
    mw = RateLimitMiddleware(tpm_limit=None, sleep=lambda s: None, on_event=events.append,
                             fallbacks=[SimpleNamespace(model_name="m2")])
    seen = []

    def handler(r):
        seen.append(r.model.model_name)
        if r.model.model_name == "m1":
            raise E(long_tpd, 429)
        return ok(r.model)

    mw.wrap_model_call(req(), handler)
    assert seen == ["m1", "m2"] and events[0] == {"type": "fallback", "from": "m1", "to": "m2",
                                                   "reason": "tpd quota exhausted (frees in 3h10m)"}
    with pytest.raises(DailyQuotaExceeded, match="resume"):
        RateLimitMiddleware(tpm_limit=None, sleep=lambda s: None).wrap_model_call(req(), lambda r: (_ for _ in ()).throw(E(long_tpd, 429)))


def test_too_large_is_not_retried_and_tool_use_failed_is():
    n = []

    def too_large(r):
        n.append(1)
        raise E("Request too large", 413)

    with pytest.raises(E):
        RateLimitMiddleware(tpm_limit=None, sleep=lambda s: None).wrap_model_call(req(), too_large)
    assert len(n) == 1
    tries = []

    def flaky(r):
        tries.append(1)
        if len(tries) < 3:
            raise E("tool_use_failed", 400)
        return ok(r.model)

    RateLimitMiddleware(tpm_limit=None, sleep=lambda s: None).wrap_model_call(req(), flaky)
    assert len(tries) == 3


def test_pacer_waits_when_minute_budget_is_used():
    mw = RateLimitMiddleware(tpm_limit=1000)
    mw.pacer.record("pace-test", 900)
    assert mw.pacer.wait_time("pace-test", 200) > 50  # must wait for the window to roll
    assert mw.pacer.wait_time("other-model", 200) == 0
