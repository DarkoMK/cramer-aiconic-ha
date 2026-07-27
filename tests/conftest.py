"""Test bootstrap.

``protocol.py``, ``const.py`` and ``api.py`` have no Home Assistant imports, so
they can be tested outside HA. The package ``__init__`` does import HA, so a
stand-in package module is registered whose ``__path__`` points at the real
directory — submodules then import normally without executing ``__init__``.
"""

import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

COMPONENT_DIR = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "cramer_aiconic"
)

_pkg = types.ModuleType("cramer_aiconic")
_pkg.__path__ = [str(COMPONENT_DIR)]
sys.modules.setdefault("cramer_aiconic", _pkg)


@pytest.fixture(scope="session")
def protocol():
    return importlib.import_module("cramer_aiconic.protocol")


@pytest.fixture(scope="session")
def api_module():
    return importlib.import_module("cramer_aiconic.api")
