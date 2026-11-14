import json
import time

import pytest
import requests
from requests.structures import CaseInsensitiveDict

import api_handlers
import config


def _response(status, headers, body):
    r = requests.Response()
    r.status_code = status
    r.headers = CaseInsensitiveDict(headers)
    r._content = json.dumps(body).encode()
    r.url = "https://api.github.com/graphql"
    return r


@pytest.fixture
def slept(monkeypatch, tmp_path):
    """Record sleeps instead of sleeping; keep log files out of the repo."""
    calls = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "RATE_LIMIT_LOG_FILE", str(tmp_path / "rate_limit_events.csv"))
    monkeypatch.setattr(api_handlers.time, "sleep", lambda s: calls.append(s))
    return calls


def test_secondary_limit_without_ratelimit_headers_uses_retry_after(slept):
    # Real GraphQL secondary-limit response: no X-RateLimit-* headers, Retry-After: 300
    resp = _response(403, {"Retry-After": "300"},
                     {"message": "You have exceeded a secondary rate limit. Please wait a few minutes before you try again."})
    results = [api_handlers.handle_rate_limit(resp, "token", retry_count=i) for i in range(7)]
    assert results == [True] * 6 + [False]
    assert slept == [300, 600, 1200, 2400, 3600, 3600]


def test_primary_limit_sleeps_until_reset(slept):
    reset = int(time.time()) + 1200
    resp = _response(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)},
                     {"message": "API rate limit exceeded"})
    assert api_handlers.handle_rate_limit(resp, "token") is True
    assert 1190 <= slept[0] <= 1206


def test_other_403_with_quota_left_keeps_three_retries(slept):
    resp = _response(403, {"X-RateLimit-Remaining": "4321", "X-RateLimit-Reset": str(int(time.time()) + 600)},
                     {"message": "Resource not accessible by personal access token"})
    results = [api_handlers.handle_rate_limit(resp, "token", retry_count=i) for i in range(4)]
    assert results == [True, True, True, False]
    assert slept == [60, 120, 240]


def test_ok_response_does_not_sleep(slept):
    resp = _response(200, {"X-RateLimit-Remaining": "4000"}, {"data": {}})
    assert api_handlers.handle_rate_limit(resp, "token") is False
    assert slept == []


def test_failed_batch_is_split_and_counted(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    sizes = []

    def fake_fetch(ids, token):
        sizes.append(len(ids))
        if len(ids) <= 5:
            return [{"databaseId": i} for i in ids], 1, 5000, True, False
        return [], 0, 5000, False, False

    monkeypatch.setattr(api_handlers, "fetch_jobs_and_steps_graphql", fake_fetch)
    before = api_handlers.GQL_FAILED_ATTEMPTS
    nodes, cost, _remaining, degraded = api_handlers.fetch_with_dynamic_resizing(list(range(10)), "token")
    assert sizes == [10, 5, 5]
    assert len(nodes) == 10 and cost == 2 and degraded == 0
    assert api_handlers.GQL_FAILED_ATTEMPTS - before == 1
