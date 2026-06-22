from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config" / "policy_source_rules.json"
SCORING_VERSION = "policy_source_provenance_v2"


def load_policy_source_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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
    return " ".join(flatten_text(value).replace("\u3000", " ").split())


def parse_policy_date(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = normalize_text(value)
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    if "T" in text:
        try:
            return datetime.fromisoformat(text).date().isoformat()
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalize_url(value: str) -> str:
    raw = normalize_text(value)
    if not raw or any(char.isspace() for char in raw):
        return ""
    candidate = raw if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw) else f"https://{raw}"
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return ""
    if not parts.netloc:
        return ""
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = parts.path or ""
    if path == "/":
        path = ""
    elif path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def extract_domain(url: str) -> str:
    normalized = normalize_url(url)
    if not normalized:
        return ""
    try:
        return (urlsplit(normalized).hostname or "").lower()
    except ValueError:
        return ""


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", normalize_text(value).lower())


def normalize_source_org(value: str) -> str:
    raw = _compact(value)
    if not raw:
        return ""
    rules = load_policy_source_rules()
    aliases = rules.get("source_org_aliases") or {}
    for alias, canonical in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
        alias_norm = _compact(alias)
        if alias_norm and alias_norm in raw:
            return str(canonical)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "_", raw).strip("_")


def _field_aliases(rules: dict[str, Any], field: str) -> list[str]:
    aliases = (rules.get("field_aliases") or {}).get(field)
    if isinstance(aliases, list) and aliases:
        return [str(alias) for alias in aliases]
    return [field]


def _first_value(policy: dict[str, Any], rules: dict[str, Any], field: str) -> Any:
    for alias in _field_aliases(rules, field):
        value = policy.get(alias)
        if isinstance(value, (list, tuple, set, dict)):
            if value:
                return value
            continue
        if normalize_text(value):
            return value
    return ""


def canonicalize_source_org(policy: dict[str, Any]) -> str:
    rules = load_policy_source_rules()
    return normalize_source_org(str(_first_value(policy, rules, "source_org") or ""))


def _domain_matches(domain: str, allowed_domains: list[str]) -> bool:
    domain = domain.lower().strip(".")
    for allowed in allowed_domains:
        allowed_domain = str(allowed).lower().strip(".")
        if domain == allowed_domain or domain.endswith(f".{allowed_domain}"):
            return True
    return False


def compute_policy_content_hash(policy: dict[str, Any]) -> str:
    rules = load_policy_source_rules()
    stable = {
        "policy_id": normalize_text(_first_value(policy, rules, "policy_id")),
        "title": normalize_text(_first_value(policy, rules, "title")),
        "source_org_norm": canonicalize_source_org(policy),
        "source_url": normalize_url(str(_first_value(policy, rules, "source_url") or "")),
        "publish_date": parse_policy_date(_first_value(policy, rules, "publish_date")) or "",
        "summary": normalize_text(_first_value(policy, rules, "summary")),
        "key_points": normalize_text(_first_value(policy, rules, "key_points")),
    }
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def classify_source_domain(domain: str, rules: dict[str, Any]) -> dict[str, Any]:
    normalized = (domain or "").lower().strip(".")
    if not normalized:
        return {
            "source_domain_type": "missing_source_domain",
            "official_domain_match": False,
            "matched_source_orgs": [],
        }
    matched_orgs = []
    allowlist = rules.get("official_domain_allowlist") or {}
    for org, domains in allowlist.items():
        if _domain_matches(normalized, [str(item) for item in domains or []]):
            matched_orgs.append(str(org))
    if matched_orgs:
        return {
            "source_domain_type": "official_allowlist",
            "official_domain_match": True,
            "matched_source_orgs": sorted(matched_orgs),
        }
    suffixes = [str(item).lower() for item in rules.get("official_domain_suffixes") or []]
    suffix_match = any(normalized == suffix.lstrip(".") or normalized.endswith(suffix) for suffix in suffixes)
    return {
        "source_domain_type": "official_gov_cn_suffix" if suffix_match else "non_official_domain",
        "official_domain_match": bool(suffix_match),
        "matched_source_orgs": [],
    }


def validate_policy_required_fields(policy: dict[str, Any], rules: dict[str, Any]) -> tuple[bool, list[str]]:
    missing: list[str] = []
    for field in rules.get("required_fields") or []:
        value = _first_value(policy, rules, str(field))
        if isinstance(value, (list, tuple, set, dict)):
            present = bool(value)
        else:
            present = bool(normalize_text(value))
        if not present:
            missing.append(str(field))
    return not missing, missing


def validate_policy_source_org_match(policy: dict[str, Any], rules: dict[str, Any]) -> tuple[bool, list[str]]:
    url = str(_first_value(policy, rules, "source_url") or "")
    domain = extract_domain(url)
    source_org_norm = normalize_source_org(str(_first_value(policy, rules, "source_org") or ""))
    domain_class = classify_source_domain(domain, rules)
    if not url:
        return False, ["missing_source_url"]
    if not domain:
        return False, ["invalid_source_url"]
    if not domain_class["official_domain_match"]:
        return False, ["non_official_source_domain"]
    if not source_org_norm:
        return False, ["missing_source_org"]

    allowlist = rules.get("official_domain_allowlist") or {}
    allowed = [str(item) for item in allowlist.get(source_org_norm, []) or []]
    if allowed and _domain_matches(domain, allowed):
        return True, []
    if source_org_norm == "state_council" and domain_class["official_domain_match"]:
        return False, ["weak_state_council_gov_cn_match"]
    if source_org_norm in allowlist:
        return False, ["source_org_domain_conflict"]
    return False, ["unknown_source_org"]


def _missing_recommended(policy: dict[str, Any], rules: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in rules.get("recommended_fields") or []:
        value = _first_value(policy, rules, str(field))
        if isinstance(value, (list, tuple, set, dict)):
            present = bool(value)
        else:
            present = bool(normalize_text(value))
        if not present:
            missing.append(str(field))
    return missing


def _canonical_policy_id(policy: dict[str, Any], rules: dict[str, Any]) -> str:
    return normalize_text(_first_value(policy, rules, "policy_id"))


def _canonical_policy_for_scoring(policy: dict[str, Any], provenance: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    item = dict(policy)
    policy_id = provenance.get("policy_id") or _canonical_policy_id(policy, rules)
    source_org = provenance.get("source_org") or normalize_text(_first_value(policy, rules, "source_org"))
    source_url = provenance.get("source_url") or normalize_url(str(_first_value(policy, rules, "source_url") or ""))
    publish_date = provenance.get("publish_date") or parse_policy_date(_first_value(policy, rules, "publish_date")) or ""
    if policy_id:
        item.setdefault("id", policy_id)
        item["policy_id"] = policy_id
    if source_org:
        item.setdefault("source", source_org)
        item["source_org"] = source_org
    if source_url:
        item.setdefault("url", source_url)
        item["source_url"] = source_url
    if publish_date:
        item.setdefault("published_date", publish_date)
        item["publish_date"] = publish_date
    item["policy_provenance_status"] = provenance.get("provenance_status", "")
    item["policy_content_hash"] = provenance.get("content_hash", "")
    item["source_domain"] = provenance.get("source_domain", "")
    return item


def compute_policy_provenance_v2(policy: dict[str, Any], rules: dict[str, Any] | None = None) -> dict[str, Any]:
    active_rules = rules or load_policy_source_rules()
    source_url_raw = str(_first_value(policy, active_rules, "source_url") or "")
    normalized_url = normalize_url(source_url_raw)
    domain = extract_domain(source_url_raw)
    source_org_raw = normalize_text(_first_value(policy, active_rules, "source_org"))
    source_org_norm = normalize_source_org(source_org_raw)
    publish_date_raw = _first_value(policy, active_rules, "publish_date")
    parsed_date = parse_policy_date(publish_date_raw)
    required_complete, missing_required = validate_policy_required_fields(policy, active_rules)
    missing_recommended = _missing_recommended(policy, active_rules)
    domain_class = classify_source_domain(domain, active_rules)
    source_org_domain_match, source_reasons = validate_policy_source_org_match(policy, active_rules)

    rejection_reasons: list[str] = []
    if not required_complete:
        rejection_reasons.append("missing_required_fields")
    if "missing_source_url" in source_reasons:
        rejection_reasons.append("missing_source_url")
    if "invalid_source_url" in source_reasons:
        rejection_reasons.append("invalid_source_url")
    if "non_official_source_domain" in source_reasons:
        rejection_reasons.append("non_official_source_domain")
    if not parsed_date:
        rejection_reasons.append("unparseable_publish_date")
    if "missing_source_org" in source_reasons:
        rejection_reasons.append("missing_source_org")
    if "source_org_domain_conflict" in source_reasons:
        rejection_reasons.append("source_org_domain_conflict")

    weak_reasons = [reason for reason in source_reasons if reason in {"weak_state_council_gov_cn_match", "unknown_source_org"}]
    if rejection_reasons:
        provenance_status = "rejected"
        inclusion_status = "excluded_from_mainline"
        exclusion_reason = rejection_reasons[0]
    elif missing_recommended or weak_reasons:
        provenance_status = "degraded"
        inclusion_status = "included_in_mainline"
        exclusion_reason = ""
    else:
        provenance_status = "verified"
        inclusion_status = "included_in_mainline"
        exclusion_reason = ""

    reasons = []
    reasons.extend(rejection_reasons)
    reasons.extend(weak_reasons)
    if missing_recommended:
        reasons.append("missing_recommended_fields")
    if not reasons:
        reasons.append("official_source_verified")

    return {
        "policy_id": _canonical_policy_id(policy, active_rules),
        "title": normalize_text(_first_value(policy, active_rules, "title")),
        "source_org": source_org_raw,
        "source_org_norm": source_org_norm,
        "source_url": normalized_url or source_url_raw,
        "source_domain": domain,
        "source_domain_type": domain_class["source_domain_type"],
        "official_domain_match": bool(domain_class["official_domain_match"]),
        "source_org_domain_match": bool(source_org_domain_match),
        "publish_date": parsed_date or normalize_text(publish_date_raw),
        "publish_date_parseable": bool(parsed_date),
        "required_fields_complete": bool(required_complete),
        "missing_required_fields": missing_required,
        "missing_recommended_fields": missing_recommended,
        "content_hash": compute_policy_content_hash(policy),
        "provenance_status": provenance_status,
        "inclusion_status": inclusion_status,
        "exclusion_reason": exclusion_reason,
        "provenance_reasons": reasons,
    }


def filter_policies_by_provenance(
    policies: list[dict[str, Any]], rules: dict[str, Any] | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    active_rules = rules or load_policy_source_rules()
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for policy in policies:
        provenance = compute_policy_provenance_v2(policy, active_rules)
        if provenance["inclusion_status"] == "included_in_mainline":
            included.append(_canonical_policy_for_scoring(policy, provenance, active_rules))
        else:
            excluded.append(provenance)
    return included, excluded


def build_policy_provenance_summary(policies: list[dict[str, Any]], rules: dict[str, Any] | None = None) -> dict[str, Any]:
    active_rules = rules or load_policy_source_rules()
    rows = [compute_policy_provenance_v2(policy, active_rules) for policy in policies]
    excluded = [row for row in rows if row["inclusion_status"] == "excluded_from_mainline"]
    verified_count = sum(1 for row in rows if row["provenance_status"] == "verified")
    degraded_count = sum(1 for row in rows if row["provenance_status"] == "degraded")
    rejected_count = sum(1 for row in rows if row["provenance_status"] == "rejected")
    included_count = len(rows) - len(excluded)
    if rejected_count or degraded_count:
        status = "degraded"
    else:
        status = "pass"
    return {
        "scoring_version": SCORING_VERSION,
        "status": status,
        "raw_policy_count": len(rows),
        "included_policy_count": included_count,
        "excluded_policy_count": len(excluded),
        "verified_count": verified_count,
        "degraded_count": degraded_count,
        "rejected_count": rejected_count,
        "official_domain_match_count": sum(1 for row in rows if row["official_domain_match"]),
        "source_org_domain_match_count": sum(1 for row in rows if row["source_org_domain_match"]),
        "missing_required_field_count": sum(1 for row in rows if row["missing_required_fields"]),
        "unparseable_date_count": sum(1 for row in rows if not row["publish_date_parseable"]),
        "included_policy_ids": [row["policy_id"] for row in rows if row["inclusion_status"] == "included_in_mainline"],
        "excluded_policy_ids": [row["policy_id"] for row in excluded],
        "policies": rows,
        "excluded_policies": excluded,
    }
