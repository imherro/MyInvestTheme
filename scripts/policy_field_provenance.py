from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config" / "field_provenance_rules.json"
VERSION = "policy_field_provenance_v1"


def load_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def field_provenance_for_policy(policy: dict[str, Any], rules: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    active = rules or load_rules()
    defaults = active.get("legacy_defaults", {})
    supplied = policy.get("field_provenance") if isinstance(policy.get("field_provenance"), dict) else {}
    result: dict[str, dict[str, Any]] = {}
    for field in sorted(set(policy) | set(supplied)):
        if field == "field_provenance":
            continue
        metadata = supplied.get(field) if isinstance(supplied.get(field), dict) else {}
        source_type = str(metadata.get("source_type") or defaults.get(field) or "legacy_unknown")
        result[field] = {
            "source_type": source_type,
            "model": str(metadata.get("model") or ""),
            "generated_at": str(metadata.get("generated_at") or ""),
            "evidence_fields": list(metadata.get("evidence_fields") or []),
        }
    return result


def build_field_provenance_summary(policies: list[dict[str, Any]], rules: dict[str, Any] | None = None) -> dict[str, Any]:
    active = rules or load_rules()
    source_type_counts: dict[str, int] = {}
    unknown_fields: set[str] = set()
    inference_fields: set[str] = set()
    per_policy: list[dict[str, Any]] = []
    for policy in policies:
        provenance = field_provenance_for_policy(policy, active)
        for field, metadata in provenance.items():
            source_type = metadata["source_type"]
            source_type_counts[source_type] = source_type_counts.get(source_type, 0) + 1
            if source_type == "legacy_unknown":
                unknown_fields.add(field)
            if source_type == "llm_inference":
                inference_fields.add(field)
        per_policy.append({"policy_id": policy.get("id") or policy.get("policy_id") or "", "field_provenance": provenance})
    return {
        "scoring_version": active.get("version", VERSION),
        "status": "degraded" if unknown_fields else "pass",
        "policy_count": len(policies),
        "source_type_counts": dict(sorted(source_type_counts.items())),
        "legacy_unknown_fields": sorted(unknown_fields),
        "llm_inference_fields": sorted(inference_fields),
        "policies": per_policy,
    }
