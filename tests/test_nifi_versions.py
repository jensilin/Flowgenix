"""Tests for the central version registry."""

from __future__ import annotations

import pytest

from nifi_versions import (
    SUPPORTED_VERSIONS,
    all_versions,
    get_version,
    is_downgrade,
    is_major_upgrade,
    line_of,
    resolve_or_raise,
    version_choices,
)


def test_registry_is_not_empty_and_spans_both_lines():
    lines = {v.line for v in SUPPORTED_VERSIONS}
    assert lines == {"1.x", "2.x"}


def test_all_versions_is_sorted_oldest_first():
    versions = all_versions()
    assert versions == sorted(versions, key=lambda v: v.sort_key)
    assert versions[0].line == "1.x"
    assert versions[-1].line == "2.x"


def test_version_choices_is_newest_first_for_the_dropdown():
    choices = version_choices()
    assert choices[0]["line"] == "2.x"
    assert {"version", "label", "line", "supportsTemplates"} <= set(choices[0])


def test_26_is_a_first_class_release():
    v = get_version("2.6.0")
    assert v is not None
    assert v.version == "2.6.0"
    assert v.supports_templates is False
    versions = [item.version for item in all_versions()]
    assert versions.index("2.6.0") < versions.index("2.7.0")
    assert versions.index("2.5.0") < versions.index("2.6.0")


def test_2x_drops_templates_variables_and_event_driven():
    v2 = get_version("2.11.0")
    assert v2 is not None
    assert v2.supports_templates is False
    assert v2.supports_variable_registry is False
    assert v2.supports_event_driven is False
    assert v2.minimum_java == 21


def test_1x_keeps_templates_variables_and_event_driven():
    v1 = get_version("1.25.0")
    assert v1 is not None
    assert v1.supports_templates is True
    assert v1.supports_variable_registry is True
    assert v1.supports_event_driven is True


def test_unknown_patch_resolves_to_the_same_minor_line():
    """A user on 1.25.7 must not be refused just because we list 1.25.0."""
    resolved = get_version("1.25.7")
    assert resolved is not None
    assert resolved.version == "1.25.0"


def test_unknown_minor_falls_back_within_the_same_major_line():
    """An unlisted 1.x release resolves to 1.x traits, never to 2.x traits."""
    resolved = get_version("1.24.0")
    assert resolved is not None
    assert resolved.line == "1.x"
    assert resolved.supports_templates is True


def test_120_is_listed_and_resolves_to_itself():
    v = get_version("1.2.0")
    assert v is not None
    assert v.version == "1.2.0"
    assert v.supports_templates is True
    assert v.supports_parameter_contexts is False
    assert all_versions()[0].version == "1.2.0"


def test_parameter_contexts_arrive_in_110():
    assert get_version("1.9.2").supports_parameter_contexts is False
    assert get_version("1.25.0").supports_parameter_contexts is True


def test_unlisted_old_release_resolves_to_the_nearest_older_entry():
    """1.3.0 must not borrow traits from the newest 1.x release."""
    resolved = get_version("1.3.0")
    assert resolved is not None
    assert resolved.version == "1.2.0"


def test_release_older_than_every_entry_resolves_to_the_oldest():
    resolved = get_version("1.0.0")
    assert resolved is not None
    assert resolved.version == "1.2.0"


def test_xml_encoding_follows_the_target_release():
    from flow_schema import xml_encoding_for_version

    assert xml_encoding_for_version("1.2.0") == "1.1"
    assert xml_encoding_for_version("1.16.3") == "1.2"
    assert xml_encoding_for_version("1.25.0") == "1.3"


def test_completely_unknown_major_returns_none():
    assert get_version("9.9.9") is None
    assert get_version("") is None
    assert get_version(None) is None


def test_resolve_or_raise_names_the_role_and_lists_options():
    with pytest.raises(ValueError) as err:
        resolve_or_raise("9.9.9", "target")
    message = str(err.value)
    assert "target" in message
    assert "1.25.0" in message


def test_line_of_handles_unregistered_versions():
    assert line_of("1.99.0") == "1.x"
    assert line_of("2.4.0") == "2.x"
    assert line_of("") == "unknown"


def test_major_upgrade_detection():
    assert is_major_upgrade("1.25.0", "2.11.0") is True
    assert is_major_upgrade("1.19.1", "1.25.0") is False
    assert is_major_upgrade("2.0.0", "2.11.0") is False


def test_downgrade_detection():
    assert is_downgrade("2.11.0", "1.25.0") is True
    assert is_downgrade("1.25.0", "2.11.0") is False
    assert is_downgrade("1.25.0", "1.25.0") is False
