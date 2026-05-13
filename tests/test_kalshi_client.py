import requests

from weather_bot.strategy.kalshi_client import KalshiClient


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


def _client() -> KalshiClient:
    client = KalshiClient.__new__(KalshiClient)
    client.base_url = "https://example.test"
    client.max_retries = 3
    client.backoff_seconds = 0.1
    client._headers = lambda method, path: {"accept": "application/json"}
    return client


def test_get_retries_rate_limit_then_returns_json(monkeypatch):
    responses = [
        FakeResponse(429, headers={"Retry-After": "0.2"}),
        FakeResponse(200, {"ok": True}),
    ]
    sleeps = []

    monkeypatch.setattr("weather_bot.strategy.kalshi_client.time.sleep", sleeps.append)
    monkeypatch.setattr("weather_bot.strategy.kalshi_client.requests.get", lambda *a, **kw: responses.pop(0))

    assert _client().get("/markets") == {"ok": True}
    assert sleeps == [0.2]


def test_get_raises_after_retry_budget(monkeypatch):
    monkeypatch.setattr("weather_bot.strategy.kalshi_client.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "weather_bot.strategy.kalshi_client.requests.get",
        lambda *a, **kw: FakeResponse(429),
    )

    try:
        _client().get("/markets")
    except requests.HTTPError as exc:
        assert exc.response.status_code == 429
    else:
        raise AssertionError("expected HTTPError")
