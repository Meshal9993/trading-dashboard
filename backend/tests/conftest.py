import pytest


@pytest.fixture(autouse=True)
def no_external_feed(monkeypatch):
    """No test may start the production Yahoo worker implicitly."""
    monkeypatch.setenv("YAHOO_FEED_ENABLED", "false")
    monkeypatch.setenv("YAHOO_STREAM_ENABLED", "false")
