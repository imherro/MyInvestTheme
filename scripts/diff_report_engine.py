from __future__ import annotations

from typing import Any


def flatten_json(value: Any, prefix: str = "", limit: int = 5000) -> dict[str, Any]:
    result: dict[str, Any] = {}

    def walk(item: Any, path: str) -> None:
        if len(result) >= limit:
            return
        if isinstance(item, dict):
            if not item:
                result[path] = {}
            for key in sorted(item):
                child = f"{path}.{key}" if path else str(key)
                walk(item[key], child)
            return
        if isinstance(item, list):
            if not item:
                result[path] = []
            for index, child_item in enumerate(item):
                walk(child_item, f"{path}.{index}" if path else str(index))
            return
        result[path] = item

    walk(value, prefix)
    return result


def diff_json_paths(left: Any, right: Any, limit: int = 100) -> dict[str, Any]:
    left_flat = flatten_json(left)
    right_flat = flatten_json(right)
    left_keys = set(left_flat)
    right_keys = set(right_flat)
    missing_in_right = sorted(left_keys - right_keys)[:limit]
    missing_in_left = sorted(right_keys - left_keys)[:limit]
    changed = []
    for key in sorted(left_keys & right_keys):
        if left_flat[key] != right_flat[key]:
            changed.append(
                {
                    "path": key,
                    "golden": left_flat[key],
                    "current": right_flat[key],
                }
            )
        if len(changed) >= limit:
            break
    return {
        "missing_in_current": missing_in_right,
        "missing_in_golden": missing_in_left,
        "changed_values": changed,
        "missing_in_current_count": len(left_keys - right_keys),
        "missing_in_golden_count": len(right_keys - left_keys),
        "changed_value_count": sum(1 for key in left_keys & right_keys if left_flat[key] != right_flat[key]),
    }


def section_presence_diff(golden_sections: list[str], current_report: dict[str, Any]) -> dict[str, Any]:
    missing = [section for section in golden_sections if section not in current_report]
    return {
        "status": "critical" if missing else "perfect_match",
        "missing_sections": missing,
        "missing_section_count": len(missing),
    }
