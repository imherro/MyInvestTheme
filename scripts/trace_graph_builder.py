from __future__ import annotations

import math
from typing import Any


TYPE_ORDER = {"policy": 0, "event": 1, "theme": 2, "mainline": 3}


def round6(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return round(number, 6)


def _node_id(node_type: str, raw_id: Any) -> str:
    return f"{node_type}:{raw_id or 'unknown'}"


def _add_node(nodes: dict[str, dict[str, Any]], node: dict[str, Any]) -> None:
    node_id = str(node.get("id") or "")
    if not node_id:
        return
    existing = nodes.get(node_id, {})
    merged = {**existing, **node}
    nodes[node_id] = merged


def _edge(
    source: str,
    target: str,
    relation: str,
    weight: Any,
    contribution: Any = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = {
        "from": source,
        "to": target,
        "relation": relation,
        "weight": round6(weight),
    }
    if contribution is not None:
        item["contribution"] = round6(contribution)
    if extra:
        item.update(extra)
    return item


def build_trace_graph(
    theme: dict[str, Any],
    event_breakdowns: list[dict[str, Any]],
    policy_paths: list[dict[str, Any]],
) -> dict[str, Any]:
    theme_id = str(theme.get("theme_id") or "")
    theme_node_id = _node_id("theme", theme_id)
    mainline_node_id = "mainline:mainline_score_v6"
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    _add_node(
        nodes,
        {
            "id": mainline_node_id,
            "type": "mainline",
            "label": "mainline_score_v6",
            "score": round6(theme.get("mainline_score_v6")),
        },
    )
    _add_node(
        nodes,
        {
            "id": theme_node_id,
            "type": "theme",
            "theme_id": theme_id,
            "label": theme.get("theme_name") or theme_id,
            "theme_name": theme.get("theme_name", ""),
            "theme_score_v5": round6(theme.get("theme_score_v5")),
            "mainline_score_v6": round6(theme.get("mainline_score_v6")),
            "lifecycle_state": theme.get("lifecycle_state", ""),
        },
    )

    for event in event_breakdowns:
        event_id = str(event.get("event_cluster_id") or "")
        event_node_id = _node_id("event", event_id)
        _add_node(
            nodes,
            {
                "id": event_node_id,
                "type": "event",
                "event_cluster_id": event_id,
                "label": event.get("primary_policy_title") or event_id,
                "primary_policy_id": event.get("primary_policy_id", ""),
                "primary_policy_title": event.get("primary_policy_title", ""),
                "event_activity_date": event.get("event_activity_date", ""),
                "contribution": round6(event.get("contribution")),
            },
        )
        edges.append(
            _edge(
                event_node_id,
                theme_node_id,
                "event_theme_allocation_v2",
                event.get("breakdown", {}).get("allocation_share"),
                event.get("contribution"),
                {
                    "allocation_role": event.get("allocation_role", ""),
                    "cluster_relevance_score_v2": round6(event.get("breakdown", {}).get("relevance_score_v2")),
                },
            )
        )

    for path in policy_paths:
        policy_id = str(path.get("policy_id") or "")
        event_id = str(path.get("event_cluster_id") or "")
        policy_node_id = _node_id("policy", policy_id)
        event_node_id = _node_id("event", event_id)
        _add_node(
            nodes,
            {
                "id": policy_node_id,
                "type": "policy",
                "policy_id": policy_id,
                "label": path.get("policy_title") or policy_id,
                "policy_title": path.get("policy_title", ""),
                "source_org": path.get("source_org", ""),
                "publish_date": path.get("publish_date", ""),
                "source_url": path.get("source_url", ""),
            },
        )
        edges.append(
            _edge(
                policy_node_id,
                event_node_id,
                "policy_event_trace",
                path.get("policy_score_v2"),
                path.get("path_contribution"),
                {"path": list(path.get("path") or [])},
            )
        )

    edges.append(
        _edge(
            theme_node_id,
            mainline_node_id,
            "mainline_lifecycle_v2",
            theme.get("mainline_score_v6"),
            theme.get("mainline_score_v6"),
            {"lifecycle_quality_multiplier": round6(theme.get("lifecycle_quality_multiplier"))},
        )
    )

    sorted_nodes = sorted(nodes.values(), key=lambda node: (TYPE_ORDER.get(str(node.get("type")), 99), str(node.get("id"))))
    sorted_edges = sorted(edges, key=lambda edge: (str(edge.get("from")), str(edge.get("to")), str(edge.get("relation"))))
    return {
        "scoring_version": "explainability_trace_graph_v2",
        "node_count": len(sorted_nodes),
        "edge_count": len(sorted_edges),
        "nodes": sorted_nodes,
        "edges": sorted_edges,
    }


def orphan_theme_nodes(trace_graph: dict[str, Any]) -> list[str]:
    nodes = trace_graph.get("nodes") or []
    edges = trace_graph.get("edges") or []
    connected = {str(edge.get("from")) for edge in edges} | {str(edge.get("to")) for edge in edges}
    return sorted(
        str(node.get("id"))
        for node in nodes
        if node.get("type") == "theme" and str(node.get("id")) not in connected
    )


def validate_trace_graph(trace_graph: dict[str, Any]) -> dict[str, Any]:
    orphans = orphan_theme_nodes(trace_graph)
    edges = trace_graph.get("edges") or []
    missing_endpoints = [
        edge
        for edge in edges
        if not edge.get("from") or not edge.get("to")
    ]
    return {
        "status": "pass" if not orphans and not missing_endpoints else "fail",
        "orphan_theme_node_count": len(orphans),
        "orphan_theme_nodes": orphans,
        "missing_endpoint_edge_count": len(missing_endpoints),
    }
