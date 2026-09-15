"""Detect NiFi version and pick processors/services that exist on this instance."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nifi_client import NiFiClient

CATALOG_PATH = Path(__file__).resolve().parent / "flows" / ".nifi-catalog.json"

# A shortlist of everyday building blocks, surfaced first in the prompt so the
# agent has familiar defaults. It is NOT a restriction — every processor and
# service installed on the instance is offered to the agent as well.
CORE_PROCESSORS = [
    "GenerateFlowFile",
    "LogAttribute",
    "UpdateAttribute",
    "RouteOnAttribute",
    "EvaluateJsonPath",
    "SplitJson",
    "SplitRecord",
    "SplitText",
    "ReplaceText",
    "MergeContent",
    "ConvertRecord",
    "QueryRecord",
    "ConvertJSONToCSV",
    "ConvertJSONToAvro",
    "ConvertAvroToCSV",
    "GetFile",
    "PutFile",
    "ListenHTTP",
    "InvokeHTTP",
    "HandleHttpRequest",
    "HandleHttpResponse",
    "ConsumeKafka",
    "PublishKafka",
    "ConsumeKafka_2_6",
    "PublishKafka_2_6",
    "ExecuteSQL",
    "PutDatabaseRecord",
    "AttributesToJSON",
    "FlattenJson",
    "JoltTransformJSON",
    # Record-aware Jolt (nifi-jolt-record-nar). Applies the spec per record through a
    # RecordReader, so it handles NDJSON as well as JSON arrays and writes CSV directly.
    "JoltTransformRecord",
]

CORE_SERVICES = [
    "JsonTreeReader",
    "JsonPathReader",
    "CSVReader",
    "CSVRecordSetWriter",
    "JsonRecordSetWriter",
    "AvroReader",
    "AvroRecordSetWriter",
]


def parse_version(version: str) -> tuple[int, int, int]:
    parts = []
    for token in (version or "0").split("."):
        digits = "".join(ch for ch in token if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def version_at_least(version: str, major: int, minor: int = 0, patch: int = 0) -> bool:
    return parse_version(version) >= (major, minor, patch)


def _short_name(type_name: str) -> str:
    return (type_name or "").rsplit(".", 1)[-1]


@dataclass
class TypeInfo:
    type_name: str
    description: str = ""
    bundle: dict[str, Any] = field(default_factory=dict)

    @property
    def short_name(self) -> str:
        return _short_name(self.type_name)

    @property
    def bundle_version(self) -> str:
        return str(self.bundle.get("version") or "")


def pick_matching_bundle(infos: list[TypeInfo], nifi_version: str) -> TypeInfo:
    """Choose the NAR bundle that matches this NiFi version (processor versions differ by release)."""
    if not infos:
        raise KeyError("No processor/service candidates")
    exact = [info for info in infos if info.bundle_version == nifi_version]
    if exact:
        return exact[0]
    return sorted(infos, key=lambda info: parse_version(info.bundle_version), reverse=True)[0]


@dataclass
class NiFiCatalog:
    version: str
    processors: dict[str, TypeInfo]
    services: dict[str, TypeInfo]

    def has_processor(self, name_or_type: str) -> bool:
        return self.processor_info(name_or_type) is not None

    def has_service(self, name_or_type: str) -> bool:
        return self.service_info(name_or_type) is not None

    def processor_info(self, name_or_type: str) -> TypeInfo | None:
        return self._find(self.processors, name_or_type)

    def service_info(self, name_or_type: str) -> TypeInfo | None:
        return self._find(self.services, name_or_type)

    def processor_type(self, name_or_type: str) -> str:
        return self.resolve_processor_info(name_or_type).type_name

    def service_type(self, name_or_type: str) -> str:
        return self.resolve_service_info(name_or_type).type_name

    def resolve_processor(self, name_or_type: str) -> str:
        return self.processor_type(name_or_type)

    def resolve_service(self, name_or_type: str) -> str:
        return self.service_type(name_or_type)

    def resolve_processor_info(self, name_or_type: str) -> TypeInfo:
        found = self.processor_info(name_or_type)
        if not found:
            raise KeyError(f"Processor not available on NiFi {self.version}: {name_or_type}")
        return found

    def resolve_service_info(self, name_or_type: str) -> TypeInfo:
        found = self.service_info(name_or_type)
        if not found:
            raise KeyError(f"Controller service not available on NiFi {self.version}: {name_or_type}")
        return found

    @staticmethod
    def _find(index: dict[str, TypeInfo], name_or_type: str) -> TypeInfo | None:
        key = (name_or_type or "").strip()
        if not key:
            return None
        if key in index:
            return index[key]
        short = _short_name(key).lower()
        for info in index.values():
            if info.short_name.lower() == short or info.type_name.lower() == key.lower():
                return info
        return None

    def _listed(self, names: list[str], kind: str) -> list[dict[str, Any]]:
        out = []
        for name in names:
            info = self.processor_info(name) if kind == "processor" else self.service_info(name)
            out.append(
                {
                    "name": name,
                    "available": info is not None,
                    "type": info.type_name if info else None,
                    "bundle": info.bundle if info else None,
                    "bundleVersion": info.bundle_version if info else None,
                }
            )
        seen: set[str] = set()
        unique = []
        for item in out:
            if item["name"] in seen:
                continue
            seen.add(item["name"])
            unique.append(item)
        return unique

    def strategies(self) -> dict[str, str]:
        record = (
            self.has_processor("ConvertRecord")
            and self.has_service("CSVReader")
            and self.has_service("JsonRecordSetWriter")
        )
        json_csv_record = (
            self.has_processor("ConvertRecord")
            and self.has_service("JsonTreeReader")
            and self.has_service("CSVRecordSetWriter")
        )
        return {
            "anyFlow": "use_available_types_only",
            "csvToJson": "convert_record" if record else "unsupported",
            "jsonToCsv": self.json_to_csv_strategy(),
            "jsonExtract": (
                "EvaluateJsonPath"
                if self.has_processor("EvaluateJsonPath")
                else ("QueryRecord" if self.has_processor("QueryRecord") else "unsupported")
            ),
            "splitJson": (
                "SplitJson"
                if self.has_processor("SplitJson")
                else ("SplitRecord" if self.has_processor("SplitRecord") else "unsupported")
            ),
            "splitText": "SplitText" if self.has_processor("SplitText") else "unsupported",
            "mergeContent": "MergeContent" if self.has_processor("MergeContent") else "unsupported",
            "route": "RouteOnAttribute" if self.has_processor("RouteOnAttribute") else "unsupported",
        }

    def capabilities(self) -> dict[str, bool]:
        return {
            "recordOriented": version_at_least(self.version, 1, 2) and self.has_processor("ConvertRecord"),
            "schemaInference": version_at_least(self.version, 1, 9),
            "startingFieldStrategy": version_at_least(self.version, 1, 15),
            "queryRecord": self.has_processor("QueryRecord"),
            "evaluateJsonPath": self.has_processor("EvaluateJsonPath"),
            "jolt": self.has_processor("JoltTransformJSON"),
            "joltRecord": self.has_processor("JoltTransformRecord"),
            "fileIO": self.has_processor("GetFile") and self.has_processor("PutFile"),
            # Structural NiFi features available on every 1.x release — not processor-dependent.
            "subProcessGroups": True,
            "inputOutputPorts": True,
            "funnels": True,
            "labels": True,
        }

    def summary(self) -> dict[str, Any]:
        major, minor, patch = parse_version(self.version)
        return {
            "version": self.version,
            "majorMinor": f"{major}.{minor}",
            "patch": patch,
            "supportsRecordPath": version_at_least(self.version, 1, 2),
            "supportsSchemaInference": version_at_least(self.version, 1, 9),
            "supportsStartingFieldStrategy": version_at_least(self.version, 1, 15),
            "capabilities": self.capabilities(),
            "strategies": self.strategies(),
            "jsonToCsvStrategy": self.json_to_csv_strategy(),
            "processorBundleVersion": self.version,
            # Curated shortlist (with availability flags) for the UI's inspect panel.
            "processors": self._listed(CORE_PROCESSORS, "processor"),
            "controllerServices": self._listed(CORE_SERVICES, "service"),
            # Everything this instance actually has, for the agent to choose from.
            "processorCount": len(self.all_processor_names()),
            "serviceCount": len(self.all_service_names()),
            "allowedProcessorTypes": self.allowed_types_for_prompt(),
            "allowedServiceTypes": self.allowed_service_types_for_prompt(),
            "allProcessors": self._entries(self.processors),
            "allControllerServices": self._entries(self.services),
        }

    def conversion_summary(self) -> dict[str, Any]:
        return self.summary()

    def json_to_csv_strategy(self) -> str:
        record_capable = (
            self.has_processor("ConvertRecord")
            and self.has_service("JsonTreeReader")
            and self.has_service("CSVRecordSetWriter")
        )
        if (
            self.has_processor("JoltTransformRecord")
            and self.has_service("JsonTreeReader")
            and self.has_service("CSVRecordSetWriter")
        ):
            # Best: the record-aware Jolt reads through JsonTreeReader, so NDJSON and
            # JSON arrays both work, and it flattens + writes CSV in one processor.
            return "jolt_record"
        if record_capable and self.has_processor("JoltTransformJSON"):
            # JoltTransformJSON needs one whole JSON document per FlowFile, so this
            # path cannot read NDJSON input files.
            return "jolt_convert_record"
        if record_capable:
            return "convert_record"
        if self.has_processor("ConvertJSONToCSV"):
            return "legacy_convert_json_to_csv"
        if self.has_processor("ConvertJSONToAvro") and self.has_processor("ConvertAvroToCSV"):
            return "legacy_json_avro_csv"
        return "unsupported"

    def all_processor_names(self) -> list[str]:
        return sorted({info.short_name for info in self.processors.values()})

    def all_service_names(self) -> list[str]:
        return sorted({info.short_name for info in self.services.values()})

    @staticmethod
    def _entries(index: dict[str, TypeInfo]) -> list[dict[str, Any]]:
        """One row per distinct type — the index also keys short names, so dedupe."""
        by_type: dict[str, dict[str, Any]] = {}
        for info in index.values():
            by_type[info.type_name] = {
                "name": info.short_name,
                "type": info.type_name,
                "bundle": info.bundle,
                "bundleVersion": info.bundle_version,
            }
        return sorted(by_type.values(), key=lambda entry: entry["name"])

    def allowed_types_for_prompt(self) -> list[str]:
        """Every installed processor, common ones first.

        This used to truncate the tail, which silently hid most of the instance
        (e.g. ListFile and TailFile) from the agent.
        """
        names = self.all_processor_names()
        priority = set(CORE_PROCESSORS)
        head = [n for n in CORE_PROCESSORS if n in names]
        return head + [n for n in names if n not in priority]

    def allowed_service_types_for_prompt(self) -> list[str]:
        names = self.all_service_names()
        priority = set(CORE_SERVICES)
        head = [n for n in CORE_SERVICES if n in names]
        return head + [n for n in names if n not in priority]

    def save(self, path: Path | None = None) -> Path:
        dest = path or CATALOG_PATH
        dest.parent.mkdir(exist_ok=True)
        dest.write_text(json.dumps(self.summary(), indent=2), encoding="utf-8")
        return dest


def _index_types(items: list[dict[str, Any]], nifi_version: str) -> dict[str, TypeInfo]:
    grouped: dict[str, list[TypeInfo]] = {}
    for item in items:
        type_name = str(item.get("type") or "")
        if not type_name:
            continue
        info = TypeInfo(
            type_name=type_name,
            description=str(item.get("description") or ""),
            bundle=item.get("bundle") or {},
        )
        grouped.setdefault(type_name, []).append(info)

    index: dict[str, TypeInfo] = {}
    for type_name, infos in grouped.items():
        chosen = pick_matching_bundle(infos, nifi_version)
        index[type_name] = chosen
        index[chosen.short_name] = chosen
    return index


def load_catalog(client: NiFiClient | None = None) -> NiFiCatalog:
    client = client or NiFiClient()
    # An unsecured instance logs in to an empty token, so test for "never logged in"
    # rather than truthiness.
    if getattr(client, "_token", None) is None:
        client.login()
    version = client.nifi_version() or "unknown"
    processors = _index_types(client.list_processor_types(), version)
    services = _index_types(client.list_controller_service_types(), version)
    catalog = NiFiCatalog(version=version, processors=processors, services=services)
    catalog.save()
    return catalog
