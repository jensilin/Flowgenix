"""Tests for the deterministic rule base."""

from __future__ import annotations

from migration_rules import (
    CONFIG_CHANGE,
    MANUAL,
    NEEDS_REVIEW,
    OUTCOME_ORDER,
    REMOVED,
    UNKNOWN,
    component_history_for,
    downgrade_property_rules_for,
    flow_concerns,
    lifecycle_verdict,
    processor_rule_for,
    property_name_on,
    property_rules_for,
    rule_summary,
    service_rule_for,
)


def test_outcome_order_covers_every_outcome_the_engine_emits():
    assert REMOVED in OUTCOME_ORDER
    assert UNKNOWN in OUTCOME_ORDER
    assert NEEDS_REVIEW <= set(OUTCOME_ORDER)


def test_gethttp_is_removed_when_targeting_2x():
    rule = processor_rule_for("org.apache.nifi.processors.standard.GetHTTP", "2.11.0")
    assert rule is not None
    assert rule.outcome == REMOVED
    assert rule.replacement == "InvokeHTTP"
    assert rule.manual_review is True


def test_gethttp_rules_do_not_fire_for_a_1x_target():
    """The rule describes a 2.0 removal, so a 1.x → 1.x move must not trip it."""
    assert processor_rule_for("GetHTTP", "1.25.0") is None


def test_rules_match_on_short_name_and_full_class_name():
    by_short = processor_rule_for("PostHTTP", "2.11.0")
    by_full = processor_rule_for("org.apache.nifi.processors.standard.PostHTTP", "2.11.0")
    assert by_short is not None
    assert by_full is not None
    assert by_short.source_name == by_full.source_name


def test_unknown_processor_has_no_rule():
    assert processor_rule_for("SomeProcessorNobodyWrote", "2.11.0") is None


def test_executescript_is_a_config_change_needing_review_on_2x():
    rule = processor_rule_for("ExecuteScript", "2.11.0")
    assert rule is not None
    assert rule.outcome == CONFIG_CHANGE
    assert rule.manual_review is True
    assert "Jython" in rule.explanation


def test_jolt_property_rename_applies_to_2x():
    rules = property_rules_for("JoltTransformJSON", "2.11.0")
    assert len(rules) == 1
    assert rules[0].old_name == "Jolt Transformation DSL"
    assert rules[0].new_name == "Jolt Transform"


def test_jolt_property_rename_does_not_apply_within_1x():
    assert property_rules_for("JoltTransformJSON", "1.25.0") == []


def test_property_rules_are_scoped_to_their_processor():
    assert property_rules_for("GetFile", "2.11.0") == []


def test_service_rule_flags_deprecated_cache_client():
    rule = service_rule_for("DistributedMapCacheClientService", "2.11.0")
    assert rule is not None
    assert rule.outcome == "deprecated"


def test_targets_respects_the_upper_bound():
    from migration_rules import ProcessorRule

    scoped = ProcessorRule(
        source_name="X", outcome=REMOVED, applies_from="2.0.0", applies_to="2.5.0"
    )
    assert scoped.targets("2.0.0") is True
    assert scoped.targets("2.2.0") is True
    assert scoped.targets("2.5.0") is False
    assert scoped.targets("1.25.0") is False


# --- Flow-level concerns ------------------------------------------------------


def test_template_upload_to_2x_raises_the_templates_removed_concern():
    concerns = flow_concerns("1.25.0", "2.11.0", "xml_template")
    keys = {c.key for c in concerns}
    assert "templates_removed" in keys
    templates = next(c for c in concerns if c.key == "templates_removed")
    assert templates.outcome == MANUAL
    assert "flow definition" in templates.recommendation


def test_json_upload_to_2x_does_not_raise_the_templates_concern():
    concerns = flow_concerns("1.25.0", "2.11.0", "json_snapshot")
    assert "templates_removed" not in {c.key for c in concerns}


def test_crossing_to_2x_always_raises_variables_and_java_concerns():
    keys = {c.key for c in flow_concerns("1.25.0", "2.11.0", "json_snapshot")}
    assert "variable_registry_removed" in keys
    assert "java_21" in keys


def test_same_line_migration_raises_no_breaking_concerns():
    assert flow_concerns("1.19.1", "1.25.0", "json_snapshot") == []


def test_2x_to_2x_migration_raises_no_breaking_concerns():
    assert flow_concerns("2.0.0", "2.11.0", "json_snapshot") == []


def test_rule_summary_is_introspectable():
    summary = rule_summary()
    assert summary["processorRules"] > 0
    assert "GetHTTP" in summary["coveredProcessors"]
    assert "HashContent" in summary["coveredProcessors"]
    assert "MapCacheClientService" in summary["coveredServices"]
    assert summary["historyEntries"] > 0


# --- Component history --------------------------------------------------------

_STD = "org.apache.nifi.processors.standard."


def test_deprecated_but_available_is_an_optional_upgrade():
    verdict = lifecycle_verdict(_STD + "GetHTTP", "1.2.0", "1.16.3")
    assert verdict.status == "deprecated"
    assert verdict.direction == "upgrade"
    assert verdict.required is False
    assert verdict.swap_to == _STD + "InvokeHTTP"


def test_removed_is_a_required_upgrade():
    verdict = lifecycle_verdict("GetHTTP", "1.2.0", "2.0.0")
    assert verdict.status == "removed"
    assert verdict.required is True
    assert verdict.swap.relationship_map == {"success": "Response"}


def test_not_yet_deprecated_is_available():
    assert lifecycle_verdict("GetHTTP", "1.2.0", "1.2.0").status == "available"
    # HashAttribute was deprecated in 1.21.0, so 1.16.3 still treats it as current.
    assert lifecycle_verdict("HashAttribute", "1.2.0", "1.16.3").status == "available"
    assert lifecycle_verdict("HashAttribute", "1.2.0", "1.28.1").status == "deprecated"


def test_target_older_than_the_type_suggests_the_older_equivalent():
    verdict = lifecycle_verdict(_STD + "CryptographicHashContent", "2.11.0", "1.2.0")
    assert verdict.status == "not_available"
    assert verdict.direction == "downgrade"
    assert verdict.required is True
    assert verdict.swap_to == _STD + "HashContent"


def test_target_older_than_the_type_with_no_equivalent_has_no_swap():
    verdict = lifecycle_verdict("org.apache.nifi.kafka.processors.ConsumeKafka", "2.11.0", "1.28.1")
    assert verdict.status == "not_available"
    assert verdict.swap_to is None


def test_full_class_names_do_not_match_a_different_package():
    """The 1.x ConsumeKafka_2_6 family must not be confused with 2.x ConsumeKafka."""
    assert component_history_for("org.apache.nifi.processors.kafka.pubsub.ConsumeKafka") is None
    assert component_history_for("ConsumeKafka_2_6") is None


def test_unknown_types_have_no_history():
    assert lifecycle_verdict("GetFile", "1.2.0", "2.11.0") is None


def test_downgrade_renames_fire_only_when_crossing_the_boundary_downwards():
    names = {r.old_name: r.new_name for r in downgrade_property_rules_for("InvokeHTTP", "2.11.0", "1.28.1")}
    assert names["HTTP URL"] == "Remote URL"
    assert names["Socket Read Timeout"] == "Read Timeout"
    assert downgrade_property_rules_for("InvokeHTTP", "2.11.0", "2.6.0") == []
    assert downgrade_property_rules_for("InvokeHTTP", "1.25.0", "1.2.0") == []
    jolt = downgrade_property_rules_for("JoltTransformJSON", "2.11.0", "1.28.1")
    assert [(r.old_name, r.new_name) for r in jolt] == [("Jolt Transform", "Jolt Transformation DSL")]


def test_swapped_properties_use_the_target_release_spelling():
    assert property_name_on(_STD + "InvokeHTTP", "Remote URL", "2.11.0") == "HTTP URL"
    assert property_name_on(_STD + "InvokeHTTP", "Remote URL", "1.28.1") == "Remote URL"
    assert property_name_on(_STD + "InvokeHTTP", "HTTP Method", "2.11.0") == "HTTP Method"
    assert property_name_on("GetFile", "Remote URL", "2.11.0") == "Remote URL"
