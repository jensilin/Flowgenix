"""Shared fixtures for the migration test suite.

The repo root goes on `sys.path` so tests can import the top-level modules
(`flow_document`, `migration_engine`, …) the same way `ui/app.py` does, without
requiring the project to be pip-installed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def xml_template_1x() -> str:
    return (FIXTURES / "template_1x.xml").read_text(encoding="utf-8")


@pytest.fixture
def json_flow_1x() -> str:
    return (FIXTURES / "flow_1x.json").read_text(encoding="utf-8")


@pytest.fixture
def json_flow_2x() -> str:
    return (FIXTURES / "flow_2x.json").read_text(encoding="utf-8")


@pytest.fixture
def json_flow_unversioned() -> str:
    return (FIXTURES / "flow_unversioned.json").read_text(encoding="utf-8")


class FakeCatalog:
    """Stand-in for a live `NiFiCatalog`.

    The engine only ever asks a catalog three things — its version and whether a
    processor/service type is present — so a fake keeps the tests offline while
    still exercising the "catalog is ground truth" precedence path.
    """

    def __init__(self, version: str, processors: set[str], services: set[str] | None = None):
        self.version = version
        self._processors = {p.lower() for p in processors}
        self._services = {s.lower() for s in (services or set())}

    @staticmethod
    def _short(name: str) -> str:
        return (name or "").rsplit(".", 1)[-1].lower()

    def has_processor(self, name_or_type: str) -> bool:
        return self._short(name_or_type) in self._processors

    def has_service(self, name_or_type: str) -> bool:
        return self._short(name_or_type) in self._services


@pytest.fixture
def fake_catalog_factory():
    return FakeCatalog
