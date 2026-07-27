"""SigV4 presigning for AWS IoT Core WebSocket connections.

Only the one request shape AWS IoT needs is implemented, so no AWS SDK
dependency is required.
"""

from __future__ import annotations

import hashlib
import hmac
import urllib.parse
from datetime import datetime, timezone

_SERVICE = "iotdevicegateway"
_ALGORITHM = "AWS4-HMAC-SHA256"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def presign_iot_wss(
    endpoint: str,
    region: str,
    access_key: str,
    secret_key: str,
    session_token: str | None = None,
    now: datetime | None = None,
) -> str:
    """Return a presigned ``wss://`` URL for the AWS IoT MQTT gateway."""
    now = now or datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    scope = f"{datestamp}/{region}/{_SERVICE}/aws4_request"

    query = {
        "X-Amz-Algorithm": _ALGORITHM,
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-SignedHeaders": "host",
    }
    canonical_query = "&".join(
        f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
        for k, v in sorted(query.items())
    )
    canonical_request = "\n".join(
        [
            "GET",
            "/mqtt",
            canonical_query,
            f"host:{endpoint}\n",
            "host",
            _EMPTY_SHA256,
        ]
    )
    string_to_sign = "\n".join(
        [
            _ALGORITHM,
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )

    key = _sign(f"AWS4{secret_key}".encode(), datestamp)
    key = _sign(key, region)
    key = _sign(key, _SERVICE)
    key = _sign(key, "aws4_request")
    signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()

    url = f"wss://{endpoint}/mqtt?{canonical_query}&X-Amz-Signature={signature}"
    if session_token:
        url += "&X-Amz-Security-Token=" + urllib.parse.quote(session_token, safe="-_.~")
    return url
