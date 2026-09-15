"""Central registry of the NiFi versions this application can migrate between.

Everything version-related for the migration feature reads from here, so adding
support for a new NiFi release is a one-line edit to `SUPPORTED_VERSIONS` plus
(optionally) rules in `migration_rules.py`.

Deliberately separate from `nifi_catalog.py`: the catalog describes a *live*
instance we can interrogate over REST, whereas this registry describes releases
we may have to reason about *offline* — the user migrating a 1.19 flow to 2.11
usually has neither instance running.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nifi_catalog import parse_version


@dataclass(frozen=True)
class NiFiVersion:
    """One supported NiFi release and the traits a migration needs to know about.

    `traits` are coarse capability flags used by the rule engine to decide
    whether a construct in the source flow survives in the target. They are
    intentionally few: anything finer-grained belongs in `migration_rules.py`
    where it can carry an explanation and a suggested replacement.
    """

    version: str
    line: str  # "1.x" or "2.x"
    label: str = ""
    # Traits that migration rules gate on.
    supports_templates: bool = True  # XML templates; removed in 2.0
    supports_variable_registry: bool = True  # Variables; removed in 2.0
    supports_event_driven: bool = True  # EVENT_DRIVEN scheduling; removed in 2.0
    supports_parameter_contexts: bool = True  # 1.10+
    supports_stateless: bool = False  # Stateless execution engine; 2.0+
    minimum_java: int = 8
    notes: str = ""

    @property
    def sort_key(self) -> tuple[int, int, int]:
        return parse_version(self.version)

    @property
    def display(self) -> str:
        return self.label or self.version

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "line": self.line,
            "label": self.display,
            "supportsTemplates": self.supports_templates,
            "supportsVariableRegistry": self.supports_variable_registry,
            "supportsEventDriven": self.supports_event_driven,
            "supportsParameterContexts": self.supports_parameter_contexts,
            "supportsStateless": self.supports_stateless,
            "minimumJava": self.minimum_java,
            "notes": self.notes,
        }


# --- The registry -------------------------------------------------------------
#
# To add a NiFi release: append one entry here. Keep it ordered oldest → newest.
# The traits below reflect release-level behaviour changes:
#   * NiFi 2.0 removed XML templates, the Variable Registry, and EVENT_DRIVEN
#     scheduling, and requires Java 21.
#   * Parameter Contexts arrived in 1.10.
SUPPORTED_VERSIONS: tuple[NiFiVersion, ...] = (
    NiFiVersion(
        version="1.16.3",
        line="1.x",
        label="1.16.3",
        supports_parameter_contexts=True,
        minimum_java=8,
    ),
    NiFiVersion(
        version="1.19.1",
        line="1.x",
        label="1.19.1",
        minimum_java=8,
    ),
    NiFiVersion(
        version="1.21.0",
        line="1.x",
        label="1.21.0",
        minimum_java=8,
    ),
    NiFiVersion(
        version="1.23.2",
        line="1.x",
        label="1.23.2",
        minimum_java=8,
    ),
    NiFiVersion(
        version="1.25.0",
        line="1.x",
        label="1.25.0",
        minimum_java=8,
    ),
    NiFiVersion(
        version="1.26.0",
        line="1.x",
        label="1.26.0",
        minimum_java=8,
    ),
    NiFiVersion(
        version="1.28.1",
        line="1.x",
        label="1.28.1 (final 1.x line)",
        minimum_java=8,
        notes="Last 1.x feature release; the natural jump-off point for a 2.x migration.",
    ),
    NiFiVersion(
        version="2.0.0",
        line="2.x",
        label="2.0.0",
        supports_templates=False,
        supports_variable_registry=False,
        supports_event_driven=False,
        supports_stateless=True,
        minimum_java=21,
        notes="Templates, the Variable Registry, and EVENT_DRIVEN scheduling were removed. Requires Java 21.",
    ),
    NiFiVersion(
        version="2.1.0",
        line="2.x",
        label="2.1.0",
        supports_templates=False,
        supports_variable_registry=False,
        supports_event_driven=False,
        supports_stateless=True,
        minimum_java=21,
    ),
    NiFiVersion(
        version="2.2.0",
        line="2.x",
        label="2.2.0",
        supports_templates=False,
        supports_variable_registry=False,
        supports_event_driven=False,
        supports_stateless=True,
        minimum_java=21,
    ),
    NiFiVersion(
        version="2.11.0",
        line="2.x",
        label="2.11.0",
        supports_templates=False,
        supports_variable_registry=False,
        supports_event_driven=False,
        supports_stateless=True,
        minimum_java=21,
    ),
)

_BY_VERSION: dict[str, NiFiVersion] = {v.version: v for v in SUPPORTED_VERSIONS}


def all_versions() -> list[NiFiVersion]:
    """Every supported version, oldest first."""
    return sorted(SUPPORTED_VERSIONS, key=lambda v: v.sort_key)


def version_choices() -> list[dict[str, Any]]:
    """Dropdown-ready payload, newest first (the common migration target)."""
    return [v.to_dict() for v in sorted(SUPPORTED_VERSIONS, key=lambda v: v.sort_key, reverse=True)]


def get_version(version: str | None) -> NiFiVersion | None:
    """Look up a release, tolerating an unknown patch level.

    A user may hold `1.25.7` while the registry knows `1.25.0`; falling back to
    the nearest same-major.minor entry is far more useful than refusing to
    migrate, and the traits we gate on never change within a minor line.
    """
    if not version:
        return None
    key = str(version).strip()
    if key in _BY_VERSION:
        return _BY_VERSION[key]
    major, minor, _ = parse_version(key)
    same_line = [v for v in SUPPORTED_VERSIONS if parse_version(v.version)[:2] == (major, minor)]
    if same_line:
        return same_line[0]
    # Last resort: the newest entry on the same major line, so a 1.x flow still
    # resolves to 1.x traits rather than falling off a cliff into 2.x traits.
    same_major = [v for v in SUPPORTED_VERSIONS if parse_version(v.version)[0] == major]
    if same_major:
        return sorted(same_major, key=lambda v: v.sort_key)[-1]
    return None


def resolve_or_raise(version: str | None, role: str) -> NiFiVersion:
    known = get_version(version)
    if known is None:
        supported = ", ".join(v.version for v in all_versions())
        raise ValueError(
            f"Unsupported {role} NiFi version {version!r}. Supported versions: {supported}."
        )
    return known


def line_of(version: str | None) -> str:
    """"1.x" / "2.x" for any version string, even one not in the registry."""
    major = parse_version(version or "0")[0]
    return f"{major}.x" if major else "unknown"


def is_major_upgrade(source: str | None, target: str | None) -> bool:
    """True when the migration crosses a major line (1.x → 2.x), where the
    breaking removals live."""
    return parse_version(source or "0")[0] != parse_version(target or "0")[0]


def is_downgrade(source: str | None, target: str | None) -> bool:
    return parse_version(source or "0") > parse_version(target or "0")
