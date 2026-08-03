"""Tests for standing down when the phone app takes the account session.

The Globe account holds a single token version. *Any* token acquisition —
password login or refresh — bumps it, and every previously issued access token
immediately returns 401 ``{"code": 88889, "msg": "Invalid token version"}``.
Proven against the live cloud: A logs in, B logs in, A dies; A refreshes, B
dies; the two can never hold a session at the same time.

So a 401 on a token that should still have been valid does not mean "renew" —
it means somebody picked up their phone. Renewing there is what logged the
owner out of the app within thirty seconds of opening it, over and over. These
tests pin the opposite behaviour: notice the eviction, stand down for a while,
and let the phone keep the account.
"""

import time

import pytest


class FakeResponse:
    def __init__(self, status: int, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise AssertionError(f"unexpected raise_for_status on {self.status}")

    async def json(self, content_type=None):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict | None]] = []

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append((method, url, json))
        if not self._responses:
            raise AssertionError("no queued response left")
        return self._responses.pop(0)


@pytest.fixture
def make_client(api_module, monkeypatch):
    """An authenticated client whose token acquisitions are counted."""

    def _make(responses):
        session = FakeSession(responses)
        api = api_module.CramerAiConicApi(session=session, username="u", password="p")
        api._region_resolved = True
        api._access_token = "token-1"
        # Comfortably inside its life, so a 401 can only be an eviction.
        api._expires_at = time.monotonic() + 7200
        api._user_id = "user-1"

        acquisitions: list[bool] = []
        counter = {"n": 1}

        async def fake_post_form(url, data):
            acquisitions.append(True)
            counter["n"] += 1
            return {
                "access_token": f"token-{counter['n']}",
                "refresh_token": f"refresh-{counter['n']}",
                "expires_in": 7200,
            }

        api._post_form = fake_post_form

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(api_module.asyncio, "sleep", _no_sleep)
        return api, session, acquisitions

    return _make


@pytest.mark.asyncio
async def test_eviction_yields_instead_of_stealing_the_session_back(
    make_client, api_module
):
    """The regression that made the phone app unusable.

    One 401 on a healthy token, and the old code called
    ``async_ensure_token(force=True)`` — bumping the token version and kicking
    the owner straight back out of the app.
    """
    api, session, acquisitions = make_client([FakeResponse(401, {})])

    with pytest.raises(api_module.CramerEvictedError):
        await api.async_get_datapoints("prod", "dev", [746])

    assert acquisitions == [], "took the session back instead of standing down"
    assert len(session.calls) == 1, "retried the call instead of standing down"
    assert api.is_yielded is True


@pytest.mark.asyncio
async def test_a_yielded_client_makes_no_further_token_requests(
    make_client, api_module
):
    api, _, acquisitions = make_client([FakeResponse(401, {})])
    with pytest.raises(api_module.CramerEvictedError):
        await api.async_get_datapoints("prod", "dev", [746])

    for _ in range(5):
        with pytest.raises(api_module.CramerEvictedError):
            await api.async_ensure_token()

    assert acquisitions == []


@pytest.mark.asyncio
async def test_the_session_is_reclaimed_once_the_yield_expires(
    make_client, api_module
):
    """Standing down must be temporary, or Home Assistant never comes back."""
    api, _, acquisitions = make_client([FakeResponse(401, {})])
    with pytest.raises(api_module.CramerEvictedError):
        await api.async_get_datapoints("prod", "dev", [746])

    api._yield_until = time.monotonic() - 1  # the window has passed

    assert await api.async_ensure_token() == "token-2"
    assert acquisitions == [True]
    assert api.is_yielded is False


@pytest.mark.asyncio
async def test_the_dead_token_is_never_reused_after_a_yield(make_client, api_module):
    """The evicted token stays dead; reusing it would 401 forever."""
    api, _, _ = make_client([FakeResponse(401, {})])
    with pytest.raises(api_module.CramerEvictedError):
        await api.async_get_datapoints("prod", "dev", [746])

    api._yield_until = time.monotonic() - 1
    assert await api.async_ensure_token() != "token-1"


@pytest.mark.asyncio
async def test_proactive_renewal_is_untouched(make_client):
    """Ordinary renewal must still happen; only a 401 means eviction.

    ``async_ensure_token`` renews inside ``TOKEN_EXPIRY_MARGIN``, so by the
    time a request goes out its token is one the server should accept. That is
    what makes "any 401 here is somebody else's login" a safe reading — but it
    also means the renewal path itself has to keep working untouched, or the
    integration would stand down every two hours for no reason.
    """
    api, _, acquisitions = make_client(
        [FakeResponse(200, {"code": 0, "info": [{"data": "AB", "timestamp": "t"}]})]
    )
    api._expires_at = time.monotonic() + 1  # inside the renewal margin
    api._refresh_token = "refresh-1"

    result = await api.async_get_datapoints("prod", "dev", [746])

    assert result == {746: ("AB", "t")}
    assert acquisitions == [True], "should have renewed proactively"
    assert api.is_yielded is False


@pytest.mark.asyncio
async def test_eviction_is_not_an_authentication_failure(api_module):
    """It must not raise the reauth dialog — the credentials are fine.

    ``CramerAuthError`` becomes ``ConfigEntryAuthFailed``, which asks the owner
    to log in again. Being told to re-enter a correct password every time you
    open the app would be worse than the bug.
    """
    assert issubclass(api_module.CramerEvictedError, api_module.CramerApiError)
    assert not issubclass(api_module.CramerEvictedError, api_module.CramerAuthError)


@pytest.mark.asyncio
async def test_yield_remaining_counts_down(make_client, api_module):
    api, _, _ = make_client([FakeResponse(401, {})])
    with pytest.raises(api_module.CramerEvictedError):
        await api.async_get_datapoints("prod", "dev", [746])

    assert 0 < api.yield_remaining <= api_module.SESSION_YIELD_SECONDS
