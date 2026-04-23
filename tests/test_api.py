"""Tests for the Indexa API client."""

from __future__ import annotations

from custom_components.indexa_capital.api import API_BASE, IndexaApiClient


class MockResponse:
    """Minimal async response for API client tests."""

    def __init__(self, payload):
        self.status = 200
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def raise_for_status(self):
        return None

    async def json(self):
        return self._payload


class MockSession:
    """Capture request details from the API client."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def request(self, method, url, headers=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
            }
        )
        return MockResponse(self.payload)


async def test_request_uses_documented_base_url_and_auth_header():
    """The client should follow the live Indexa docs for URL and auth header."""
    session = MockSession({"username": "user@example.com"})
    client = IndexaApiClient(session=session, token="real-token")

    await client.async_validate_token()

    assert session.calls[0]["method"] == "GET"
    assert session.calls[0]["url"] == f"{API_BASE}/users/me"
    assert session.calls[0]["headers"]["X-AUTH-TOKEN"] == "real-token"
    assert "Authorization" not in session.calls[0]["headers"]
