"""Shared fixtures for the migration test suite.

The repo root goes on `sys.path` so tests can import the top-level modules
(`flow_document`, `migration_engine`, …) the same way `ui/app.py` does.
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
