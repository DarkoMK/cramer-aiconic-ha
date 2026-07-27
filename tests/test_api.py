"""Tests for the Globe API request layer.

The Globe cloud reports application errors inside an HTTP 200 body
(``{"code": ...}``), and throttles with code 5031001, so both the transport
status and the body code have to be handled.
"""

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
    """Returns queued responses and records the requests made."""

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
    def _make(responses):
        session = FakeSession(responses)
        api = api_module.CramerAiConicApi(
            session=session, username="u", password="p"
        )
        api._region_resolved = True
        api._access_token = "token"
        api._expires_at = float("inf")
        api._user_id = "user-1"

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(api_module.asyncio, "sleep", _no_sleep)
        return api, session

    return _make


@pytest.mark.asyncio
async def test_datapoints_are_decoded_and_empties_skipped(make_client):
    api, _ = make_client(
        [
            FakeResponse(
                200,
                {
                    "code": 0,
                    "info": [
                        {"data": "02DFD4", "timestamp": "2026-07-27T20:00:00.000Z"},
                        {"data": "", "timestamp": "2026-07-27T20:00:00.000Z"},
                    ],
                },
            )
        ]
    )

    result = await api.async_get_datapoints("prod", "dev", [746, 95])

    assert result == {746: ("02DFD4", "2026-07-27T20:00:00.000Z")}


@pytest.mark.asyncio
async def test_throttling_is_retried_then_surfaced(make_client, api_module):
    throttled = {"code": 5031001, "msg": "service unavailable"}
    api, session = make_client([FakeResponse(200, throttled) for _ in range(3)])

    with pytest.raises(api_module.CramerRateLimitError):
        await api.async_get_datapoints("prod", "dev", [746])

    assert len(session.calls) == 3


@pytest.mark.asyncio
async def test_throttling_recovers_on_retry(make_client):
    api, session = make_client(
        [
            FakeResponse(200, {"code": 5031001, "msg": "service unavailable"}),
            FakeResponse(200, {"code": 0, "info": [{"data": "AB", "timestamp": "t"}]}),
        ]
    )

    result = await api.async_get_datapoints("prod", "dev", [746])

    assert result == {746: ("AB", "t")}
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_other_error_codes_are_not_retried(make_client, api_module):
    api, session = make_client([FakeResponse(200, {"code": 40401, "msg": "nope"})])

    with pytest.raises(api_module.CramerApiError, match="40401"):
        await api.async_get_datapoints("prod", "dev", [746])

    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_401_triggers_reauth_and_retry(make_client, api_module):
    api, session = make_client(
        [
            FakeResponse(401, {}),
            FakeResponse(200, {"code": 0, "info": {"is_online": True}}),
        ]
    )
    reauths = []

    async def fake_ensure(force=False):
        reauths.append(force)
        return "token"

    api.async_ensure_token = fake_ensure

    assert await api.async_send_command("prod", "dev", "deadbeef") is True
    assert True in reauths
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_send_command_reports_offline_device(make_client):
    api, _ = make_client(
        [FakeResponse(200, {"code": 0, "info": {"is_online": False}})]
    )
    assert await api.async_send_command("prod", "dev", "deadbeef") is False


@pytest.mark.asyncio
async def test_send_command_posts_the_payload(make_client):
    api, session = make_client(
        [FakeResponse(200, {"code": 0, "info": {"is_online": True}})]
    )

    await api.async_send_command("PROD", "DEV", "0709005000000015f5")

    method, url, body = session.calls[0]
    assert method == "POST"
    assert url.endswith("/product/PROD/device/DEV/send_command")
    assert body == {"payload": "0709005000000015f5"}


@pytest.mark.asyncio
async def test_device_list_is_parsed(make_client):
    api, _ = make_client(
        [
            FakeResponse(
                200,
                {
                    "list": [
                        {
                            "deviceId": "1000000000000000001",
                            "product_id": "PRODUCTID0001",
                            "name": "LawnMower",
                            "displayModel": "RLM3",
                            "model": "RLM3",
                            "product_type": "RLM3",
                            "sn": "000000001",
                            "mac": "RLM3_000000001",
                            "is_online": True,
                            "firmware_version": 0,
                        }
                    ]
                },
            )
        ]
    )

    devices = await api.async_get_devices()

    assert len(devices) == 1
    device = devices[0]
    assert device.device_id == "1000000000000000001"
    assert device.product_id == "PRODUCTID0001"
    assert device.name == "LawnMower"
    assert device.is_online is True
    # firmware_version 0 means "unknown", not version zero.
    assert device.firmware_version is None


@pytest.mark.asyncio
async def test_device_list_skips_entries_without_ids(make_client):
    api, _ = make_client(
        [FakeResponse(200, {"list": [{"name": "orphan"}, {"deviceId": "1"}]})]
    )
    assert await api.async_get_devices() == []
