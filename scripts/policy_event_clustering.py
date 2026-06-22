from __future__ import annotations

import math
import re
import hashlib
from datetime import date, datetime
from pathlib import Path
from typing import Any

from policy_scoring import policy_score_components


CLUSTER_DATE_WINDOW_DAYS = 7
TITLE_SIMILARITY_THRESHOLD = 0.65
STRICT_TITLE_SIMILARITY_THRESHOLD = 0.75
MISSING_DATE_TITLE_SIMILARITY_THRESHOLD = 0.80
KEYWORD_OVERLAP_THRESHOLD = 0.45
STRICT_KEYWORD_OVERLAP_THRESHOLD = 0.55
MISSING_DATE_KEYWORD_OVERLAP_THRESHOLD = 0.60

POLICY_TEXT_FIELDS = (
    "title",
    "summary",
    "policy_text",
    "key_points",
    "beneficiary_chain",
    "related_industries",
)

SOURCE_FIELDS = ("source_org", "issuer", "authority", "department", "source")
DATE_FIELDS = ("publish_date", "published_date", "date", "source_date")

SOURCE_ALIASES = (
    ("state_council", ("国务院办公厅", "中共中央国务院", "国务院")),
    ("ndrc", ("国家发展和改革委员会", "国家发展改革委", "国家发改委", "发改委")),
    ("mof", ("中华人民共和国财政部", "财政部")),
    ("miit", ("中华人民共和国工业和信息化部", "工业和信息化部", "工信部")),
    ("csrc", ("中国证券监督管理委员会", "中国证监会", "证监会")),
    ("mofcom", ("中华人民共和国商务部", "商务部")),
    ("pboc", ("中国人民银行", "人民银行", "央行")),
    ("most", ("科学技术部", "科技部")),
    ("moe", ("教育部",)),
    ("nea", ("国家能源局",)),
    ("nda", ("国家数据局",)),
)


def flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(flatten_text(item) for _, item in sorted(value.items()))
    if isinstance(value, (list, tuple, set)):
        return " ".join(flatten_text(item) for item in value)
    return str(value)


def normalize_text(value: Any) -> str:
    text = flatten_text(value).replace("\u3000", " ").lower()
    return " ".join(text.split())


def normalize_compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", normalize_text(value))


def normalize_source_org(value: str) -> str:
    raw = normalize_text(value)
    if not raw:
        return ""
    for normalized, aliases in SOURCE_ALIASES:
        if any(alias.lower() in raw for alias in aliases):
            return normalized
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "_", raw).strip("_")


def source_org_for_policy(policy: dict[str, Any]) -> str:
    for field in SOURCE_FIELDS:
        normalized = normalize_source_org(str(policy.get(field) or ""))
        if normalized:
            return normalized
    return ""


def parse_policy_date(policy: dict[str, Any]) -> date | None:
    for field in DATE_FIELDS:
        value = policy.get(field)
        if not value:
            continue
        text = str(value).strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    return None


def char_bigrams(text: str) -> set[str]:
    normalized = normalize_compact_text(text)
    if len(normalized) < 2:
        return set()
    return {normalized[index : index + 2] for index in range(len(normalized) - 1)}


def jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def compute_title_similarity(policy_a: dict[str, Any], policy_b: dict[str, Any]) -> float:
    return jaccard_similarity(char_bigrams(str(policy_a.get("title") or "")), char_bigrams(str(policy_b.get("title") or "")))


def collect_policy_text(policy: dict[str, Any]) -> str:
    return normalize_text(" ".join(flatten_text(policy.get(field)) for field in POLICY_TEXT_FIELDS))


def extract_policy_tokens(policy: dict[str, Any], theme_keywords: list[str] | None = None) -> set[str]:
    text = collect_policy_text(policy)
    tokens: set[str] = set()
    for keyword in theme_keywords or []:
        normalized = normalize_text(keyword)
        if normalized and normalized in text:
            tokens.add(normalized)
    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]*|\d+[a-zA-Z0-9_+-]*", text):
        if len(token) >= 2:
            tokens.add(token.lower())
    return tokens


def compute_keyword_overlap(
    policy_a: dict[str, Any],
    policy_b: dict[str, Any],
    theme_keywords: list[str] | None = None,
) -> float:
    tokens_a = extract_policy_tokens(policy_a, theme_keywords)
    tokens_b = extract_policy_tokens(policy_b, theme_keywords)
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def _same_nonempty(policy_a: dict[str, Any], policy_b: dict[str, Any], fields: tuple[str, ...]) -> bool:
    for field in fields:
        left = normalize_text(policy_a.get(field))
        right = normalize_text(policy_b.get(field))
        if left and left == right:
            return True
    return False


def _date_diff_days(date_a: date | None, date_b: date | None) -> int | None:
    if date_a is None or date_b is None:
        return None
    return abs((date_a - date_b).days)


def should_cluster_policies(
    policy_a: dict[str, Any],
    policy_b: dict[str, Any],
    theme_keywords: list[str] | None = None,
) -> tuple[bool, list[str], dict[str, Any]]:
    source_a = source_org_for_policy(policy_a)
    source_b = source_org_for_policy(policy_b)
    published_a = parse_policy_date(policy_a)
    published_b = parse_policy_date(policy_b)
    date_diff = _date_diff_days(published_a, published_b)
    title_similarity = compute_title_similarity(policy_a, policy_b)
    keyword_overlap = compute_keyword_overlap(policy_a, policy_b, theme_keywords)

    same_policy_id = _same_nonempty(policy_a, policy_b, ("id", "policy_id"))
    same_source_url = _same_nonempty(policy_a, policy_b, ("source_url", "url"))
    same_official_url = _same_nonempty(policy_a, policy_b, ("official_url",))
    same_source_org = bool(source_a and source_a == source_b)

    metrics = {
        "title_similarity": round(title_similarity, 4),
        "keyword_overlap": round(keyword_overlap, 4),
        "date_diff_days": date_diff,
        "same_source_org": same_source_org,
        "same_policy_id": same_policy_id,
        "same_source_url": same_source_url,
        "same_official_url": same_official_url,
    }

    direct_reasons = []
    if same_policy_id:
        direct_reasons.append("same_policy_id")
    if same_source_url:
        direct_reasons.append("same_source_url")
    if same_official_url:
        direct_reasons.append("same_official_url")
    if direct_reasons:
        return True, direct_reasons, metrics

    if date_diff is None:
        reasons = ["missing_publish_date"]
        if same_source_org:
            reasons.append("same_source_org")
        if title_similarity >= MISSING_DATE_TITLE_SIMILARITY_THRESHOLD:
            reasons.append("title_similarity_above_missing_date_threshold")
        if keyword_overlap >= MISSING_DATE_KEYWORD_OVERLAP_THRESHOLD:
            reasons.append("keyword_overlap_above_missing_date_threshold")
        should_cluster = (
            same_source_org
            and title_similarity >= MISSING_DATE_TITLE_SIMILARITY_THRESHOLD
            and keyword_overlap >= MISSING_DATE_KEYWORD_OVERLAP_THRESHOLD
        )
        return should_cluster, reasons if should_cluster else [], metrics

    within_window = date_diff <= CLUSTER_DATE_WINDOW_DAYS
    if same_source_org:
        reasons = []
        if within_window:
            reasons.append("publish_date_within_7_days")
        reasons.append("same_source_org")
        title_hit = title_similarity >= TITLE_SIMILARITY_THRESHOLD
        keyword_hit = keyword_overlap >= KEYWORD_OVERLAP_THRESHOLD
        if title_hit:
            reasons.append("title_similarity_above_threshold")
        if keyword_hit:
            reasons.append("keyword_overlap_above_threshold")
        return within_window and (title_hit or keyword_hit), reasons if within_window and (title_hit or keyword_hit) else [], metrics

    reasons = []
    if within_window:
        reasons.append("publish_date_within_7_days")
    title_hit = title_similarity >= STRICT_TITLE_SIMILARITY_THRESHOLD
    keyword_hit = keyword_overlap >= STRICT_KEYWORD_OVERLAP_THRESHOLD
    if title_hit:
        reasons.append("title_similarity_above_strict_threshold")
    if keyword_hit:
        reasons.append("keyword_overlap_above_strict_threshold")
    if within_window and title_hit and keyword_hit:
        reasons.insert(1, "weak_source_match")
        return True, reasons, metrics
    return False, [], metrics


class UnionFind:
    def __init__(self, values: list[str]):
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left == root_right:
            return
        if root_left <= root_right:
            self.parent[root_right] = root_left
        else:
            self.parent[root_left] = root_right


def policy_id(policy: dict[str, Any], index: int) -> str:
    return str(policy.get("id") or policy.get("policy_id") or f"policy_{index:04d}")


def _safe_policy_score(policy: dict[str, Any]) -> float:
    try:
        number = float(policy.get("policy_score_v2"))
    except (TypeError, ValueError):
        return 0.5
    if math.isnan(number) or math.isinf(number):
        return 0.5
    return max(0.0, min(1.0, number))


def _authority_score(policy: dict[str, Any]) -> float:
    return float(policy_score_components(policy).get("authority_score", 0.5))


def primary_policy_id_for_members(member_ids: list[str], policies_by_id: dict[str, dict[str, Any]]) -> str:
    def sort_key(item_id: str) -> tuple[float, float, str, str]:
        policy = policies_by_id[item_id]
        published = parse_policy_date(policy)
        ordinal = published.toordinal() if published else -1
        return (-_safe_policy_score(policy), -_authority_score(policy), -ordinal, item_id)

    return sorted(member_ids, key=sort_key)[0]


def _primary_sort_key(policy: dict[str, Any], policy_id_value: str) -> tuple[float, float, str, str]:
    published = parse_policy_date(policy)
    date_key = published.isoformat() if published else ""
    return (_safe_policy_score(policy), _authority_score(policy), date_key, policy_id_value)


def _slug(value: str) -> str:
    raw = normalize_text(value)
    ascii_tokens = re.findall(r"[a-z0-9]+", raw)
    if ascii_tokens:
        return "_".join(ascii_tokens)[:48]
    parts = re.findall(r"[\u4e00-\u9fff]{2,}", raw)
    if parts:
        digest = hashlib.sha1("_".join(parts).encode("utf-8")).hexdigest()[:10]
        return f"policy_{digest}"
    return "policy"


def _event_cluster_id(primary_policy: dict[str, Any], primary_id: str, used_ids: set[str]) -> str:
    published = parse_policy_date(primary_policy)
    date_part = published.strftime("%Y%m%d") if published else "unknown_date"
    source_part = source_org_for_policy(primary_policy) or "unknown_org"
    slug = _slug(primary_id) or _slug(str(primary_policy.get("title") or "policy"))
    base = re.sub(r"[^a-z0-9_]+", "_", f"event_{date_part}_{source_part}_{slug}".lower()).strip("_")
    candidate = base
    suffix = 2
    while candidate in used_ids:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used_ids.add(candidate)
    return candidate


def build_policy_event_clusters(policies: list[dict[str, Any]], theme_keywords: list[str] | None = None) -> list[dict[str, Any]]:
    indexed = sorted(((policy_id(policy, index), policy) for index, policy in enumerate(policies)), key=lambda item: item[0])
    if not indexed:
        return []
    ids = [item[0] for item in indexed]
    by_id = {item_id: policy for item_id, policy in indexed}
    uf = UnionFind(ids)
    pair_reasons: dict[tuple[str, str], tuple[list[str], dict[str, Any]]] = {}

    for left_index, (left_id, left_policy) in enumerate(indexed):
        for right_id, right_policy in indexed[left_index + 1 :]:
            should_cluster, reasons, metrics = should_cluster_policies(left_policy, right_policy, theme_keywords)
            if should_cluster:
                uf.union(left_id, right_id)
                pair_reasons[(left_id, right_id)] = (reasons, metrics)

    groups: dict[str, list[str]] = {}
    for item_id in ids:
        groups.setdefault(uf.find(item_id), []).append(item_id)

    used_cluster_ids: set[str] = set()
    clusters = []
    for members in groups.values():
        member_ids = sorted(members)
        primary_id = primary_policy_id_for_members(member_ids, by_id)
        primary_policy = by_id[primary_id]
        dates = [parse_policy_date(by_id[item_id]) for item_id in member_ids]
        valid_dates = sorted(item for item in dates if item is not None)
        source_norm = source_org_for_policy(primary_policy)
        reasons: list[str] = []
        metrics = {"max_title_similarity": 0.0, "max_keyword_overlap": 0.0, "date_span_days": 0}
        for left_index, left_id in enumerate(member_ids):
            for right_id in member_ids[left_index + 1 :]:
                pair = pair_reasons.get((left_id, right_id)) or pair_reasons.get((right_id, left_id))
                if not pair:
                    continue
                pair_reasons_list, pair_metrics = pair
                for reason in pair_reasons_list:
                    if reason not in reasons:
                        reasons.append(reason)
                metrics["max_title_similarity"] = max(metrics["max_title_similarity"], pair_metrics.get("title_similarity") or 0.0)
                metrics["max_keyword_overlap"] = max(metrics["max_keyword_overlap"], pair_metrics.get("keyword_overlap") or 0.0)
        if valid_dates:
            metrics["date_span_days"] = (valid_dates[-1] - valid_dates[0]).days
        clusters.append(
            {
                "event_cluster_id": _event_cluster_id(primary_policy, primary_id, used_cluster_ids),
                "primary_policy_id": primary_id,
                "primary_policy_title": primary_policy.get("title", ""),
                "member_policy_ids": member_ids,
                "cluster_size": len(member_ids),
                "source_org_norm": source_norm,
                "publish_date_min": valid_dates[0].isoformat() if valid_dates else "",
                "publish_date_max": valid_dates[-1].isoformat() if valid_dates else "",
                "cluster_policy_score_v2": compute_cluster_policy_score_v2({"member_policy_ids": member_ids}, by_id),
                "cluster_reason": reasons,
                "metrics": {
                    "max_title_similarity": round(metrics["max_title_similarity"], 4),
                    "max_keyword_overlap": round(metrics["max_keyword_overlap"], 4),
                    "date_span_days": metrics["date_span_days"],
                },
            }
        )
    return sorted(clusters, key=lambda row: row["primary_policy_id"])


def compute_cluster_policy_score_v2(cluster: dict[str, Any], policies_by_id: dict[str, dict[str, Any]]) -> float:
    member_ids = cluster.get("member_policy_ids") or []
    if not member_ids:
        return 0.5
    return round(max(_safe_policy_score(policies_by_id.get(item_id, {})) for item_id in member_ids), 4)


def build_event_cluster_summary(policies: list[dict[str, Any]], clusters: list[dict[str, Any]]) -> dict[str, Any]:
    raw_count = len(policies)
    cluster_count = len(clusters)
    dedup_count = max(0, raw_count - cluster_count)
    ratio = round(dedup_count / raw_count, 4) if raw_count else 0.0
    return {
        "scoring_version": "policy_event_clustering_v2",
        "cluster_date_window_days": CLUSTER_DATE_WINDOW_DAYS,
        "title_similarity_threshold": TITLE_SIMILARITY_THRESHOLD,
        "keyword_overlap_threshold": KEYWORD_OVERLAP_THRESHOLD,
        "raw_policy_count": raw_count,
        "cluster_count": cluster_count,
        "deduplicated_policy_count": dedup_count,
        "deduplication_ratio": ratio,
        "clusters": clusters,
    }
