"""Short-lived AWS IoT MQTT sessions for the settings the cloud will not cache.

The Globe cloud only caches four datapoints (746, 81, 471, 509) for
``get_datapoints``. Everything else the mower can report — light modes, sound,
obstacle handling, speed, selected site, radio status, GPS — is answered only
on the MQTT ``device_response`` topic and then discarded. Reading those
therefore means subscribing while the request is in flight.

The AWS IoT policy pins the MQTT client id to the account's Cognito identity,
so Home Assistant and the phone app cannot both hold a connection: whoever
connects last kicks the other off. To keep the phone app usable, this module
never holds a persistent connection. It opens a session, reads everything it
needs in one pass and disconnects again — a few seconds every few minutes.
"""

from __future__ import annotations

import json
import logging
import ssl
import threading
import time
import urllib.parse
from typing import Any

import certifi
import paho.mqtt.client as mqtt

from . import protocol
from .sigv4 import presign_iot_wss

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 20.0


class CramerMqttError(Exception):
    """The MQTT session could not be established or produced nothing."""


class MqttReader:
    """Collects mower responses over one short-lived MQTT session.

    Every method here blocks; call them from an executor thread.
    """

    def __init__(self, endpoint: str, client_id: str, path: str, device_id: str) -> None:
        self._endpoint = endpoint
        self._device_id = device_id
        self._connected = threading.Event()
        self._lock = threading.Lock()
        self._frames: dict[int, bytes] = {}

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            transport="websockets",
        )
        self._client.ws_set_options(path=path)
        self._client.tls_set(ca_certs=certifi.where(), cert_reqs=ssl.CERT_REQUIRED)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

    # -- callbacks (paho thread) -------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if getattr(reason_code, "is_failure", False):
            _LOGGER.debug("MQTT connect refused: %s", reason_code)
            return
        client.subscribe(
            [
                (f"$aws/things/{self._device_id}/device_response", 1),
                (f"$aws/things/{self._device_id}/device_upload", 1),
            ]
        )
        self._connected.set()

    def _on_message(self, client, userdata, message) -> None:
        try:
            payload = json.loads(message.payload)
            frame = bytes.fromhex(payload["payload"])
            _, parameter_id, body = protocol.parse_frame(frame)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as err:
            _LOGGER.debug("Ignoring unparsable MQTT payload: %s", err)
            return
        with self._lock:
            self._frames[parameter_id] = body

    # -- session management -------------------------------------------------
    def connect(self) -> None:
        self._client.connect(self._endpoint, 443, keepalive=60)
        self._client.loop_start()
        if not self._connected.wait(CONNECT_TIMEOUT):
            self.disconnect()
            raise CramerMqttError(
                "AWS IoT did not accept the connection — the phone app may be "
                "holding the session"
            )

    def disconnect(self) -> None:
        try:
            self._client.disconnect()
        finally:
            self._client.loop_stop()

    def wait_for(self, parameter_ids: set[int], timeout: float) -> None:
        """Block until every id has answered or the timeout expires."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if parameter_ids <= self._frames.keys():
                    return
            time.sleep(0.2)

    def collected(self) -> dict[int, bytes]:
        with self._lock:
            return dict(self._frames)


def build_reader(
    mqtt_info: dict[str, Any], credentials: dict[str, Any], device_id: str
) -> MqttReader:
    """Create a reader from the cloud's MQTT parameters and AWS credentials."""
    url = presign_iot_wss(
        mqtt_info["endpoint"],
        mqtt_info["region"],
        credentials["AccessKeyId"],
        credentials["SecretKey"],
        credentials.get("SessionToken"),
    )
    parsed = urllib.parse.urlparse(url)
    return MqttReader(
        endpoint=mqtt_info["endpoint"],
        # The IoT policy allows exactly this client id and no other.
        client_id=mqtt_info["identityId"],
        path=f"{parsed.path}?{parsed.query}",
        device_id=device_id,
    )
