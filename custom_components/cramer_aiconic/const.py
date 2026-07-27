"""Constants for the Cramer AiConic integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "cramer_aiconic"

CONF_REGION_CODE: Final = "region_code"
CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_SCAN_INTERVAL: Final = "scan_interval"

# --- Cloud endpoints -------------------------------------------------------
# The region list is public (no auth) and resolves the per-region GUC/Globe
# hosts. The values below are only fallbacks if that call fails.
NO_AUTH_BASE: Final = "https://api.globegrouponline.com"
REGION_LIST_PATH: Final = "/globe/world/country/region/list"

DEFAULT_REGION_CODE: Final = "eu-central-1-CONSUMER"
FALLBACK_GUC_BASE: Final = "https://user.cramertools.com"
FALLBACK_GLOBE_BASE: Final = "https://api.eu.globegrouponline.com/globe"

# --- OAuth2 ----------------------------------------------------------------
# Extracted from the Cramer AiConic Android app (res/values/strings.xml:
# token_client_id / token_client_secret).
CLIENT_ID: Final = "CramerConnectAiConic"
CLIENT_SECRET: Final = "352fc703-85f8-4fda-815a-6c2b1699b05b"
TENANT: Final = "GLB"
TARGET: Final = "gConnect"

# GConnectConstants.tokenScopes
TOKEN_SCOPES: Final = " ".join(
    [
        "gConnectAppApi.APPPush.Read",
        "gConnectAppApi.APPPush.Write",
        "gConnectAppApi.AssociatedDevice.Write",
        "gConnectAppApi.DataPoint.Read",
        "gConnectAppApi.DeviceRegistration.Read",
        "gConnectAppApi.FirmwareUpgrade.Read",
        "gConnectAppApi.FirmwareUpgrade.Write",
        "gConnectAppApi.MultiZone.Read",
        "gConnectAppApi.MultiZone.Write",
        "gConnectAppApi.Pairing.Read",
        "gConnectAppApi.Pairing.Write",
        "gConnectIotDevice.Device.Read",
        "gConnectIotDevice.Device.Write",
        "gConnectIotDevice.DeviceExtension.Read",
        "gConnectIotDevice.DeviceExtension.Write",
        "gConnectIotDevice.DeviceShare.Read",
        "gConnectIotDevice.DeviceShare.Write",
        "gConnectIotDevice.RLM.Read",
        "gConnectIotDevice.RLM.Write",
        "gConnectIotDevice.UserExtension.Read",
        "gConnectIotDevice.UserExtension.Write",
        "gConnectOpenApi",
        "LicenseServiceApi",
        "offline_access",
        "openid",
        "profile",
        "user.basic",
    ]
)

# Access tokens live 7200 s. Refresh well before that; the refresh token is
# single-use and rotates, so a missed rotation is unrecoverable without a
# full password login.
TOKEN_EXPIRY_MARGIN: Final = 600

USER_AGENT: Final = "okhttp/4.12.0"

# --- Polling ---------------------------------------------------------------
DEFAULT_SCAN_INTERVAL: Final = 30
MIN_SCAN_INTERVAL: Final = 15
MAX_SCAN_INTERVAL: Final = 600

# How often (in poll cycles) to ask the mower for fresh readings of the
# slow-moving datapoints, and to re-read the account device list.
ENRICH_EVERY_CYCLES: Final = 10

# Seconds to wait after issuing a command before refreshing state.
POST_COMMAND_REFRESH_DELAY: Final = 6

# The mower silently drops read commands that arrive back to back; roughly
# three seconds apart is reliable.
COMMAND_PACING: Final = 3.5

# How often to run the MQTT settings pass, and how long to wait for the mower
# to answer once the requests are out.
SETTINGS_REFRESH_SECONDS: Final = 900
MQTT_RESPONSE_TIMEOUT: Final = 25.0

# Consecutive failed polls tolerated before entities report unavailable.
FAILURES_BEFORE_UNAVAILABLE: Final = 3

# Week-timer slots exposed as entities. The mower accepts 0-99, but a handful
# is all a lawn needs and each slot costs four entities.
SCHEDULE_SLOTS: Final = 4

CONF_ENABLE_SETTINGS: Final = "enable_settings"
DEFAULT_ENABLE_SETTINGS: Final = True

# --- API response codes ----------------------------------------------------
API_CODE_OK: Final = 0
API_CODE_SERVICE_UNAVAILABLE: Final = 5031001

# --- Product identification ------------------------------------------------
MOWER_PRODUCT_TYPES: Final = {"RLM1", "RLM2", "RLM3", "RLM4"}
MANUFACTURER: Final = "Cramer"
