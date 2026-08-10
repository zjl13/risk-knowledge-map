from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .pkulaw_mcp import PkuLawMCPClient, PkuLawMCPError


ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "data" / "risk_map_seed.json"
CACHE_PATH = ROOT / "data" / "pkulaw_cases_cache.json"
OFFLINE_DATA_PATH = ROOT / "web" / "risk-map-data.js"


def load_taxonomy() -> dict[str, Any]:
    payload = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    if CACHE_PATH.exists():
        try:
            cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            payload["cases"] = _merge_cases(payload.get("cases", []), cached.get("cases", []))
            payload["meta"]["mode"] = "pkulaw-cache"
            payload["meta"]["pkulaw_synced_at"] = cached.get("synced_at", "")
        except (OSError, ValueError, TypeError):
            payload["meta"]["cache_warning"] = "北大法宝缓存读取失败，已回退离线种子。"
    return payload


def build_graph(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or load_taxonomy()
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    edge_keys: set[tuple[str, str, str]] = set()

    for risk in data.get("risks", []):
        nodes.append({"id": risk["code"], "type": "risk", "label": risk["name"], **risk})
        for stage_id in risk.get("stage_ids", []):
            _add_edge(edges, edge_keys, risk["code"], stage_id, "高发于")
        for law_id in risk.get("law_ids", []):
            _add_edge(edges, edge_keys, risk["code"], law_id, "涉及法条")

    for stage in data.get("stages", []):
        nodes.append({"type": "stage", "label": stage["name"], **stage})

    for law in data.get("laws", []):
        nodes.append({"type": "law", "label": f"{law['name']}\n{law['article']}", **law})

    for case in data.get("cases", []):
        nodes.append({"type": "case", "label": case["name"], **case})
        for risk_code in case.get("risk_codes", []):
            _add_edge(edges, edge_keys, case["id"], risk_code, "映射风险")
        for stage_id in case.get("stage_ids", []):
            _add_edge(edges, edge_keys, case["id"], stage_id, "发生阶段")
        for law_id in case.get("law_ids", []):
            _add_edge(edges, edge_keys, case["id"], law_id, "裁判依据")

    return {"meta": data.get("meta", {}), "nodes": nodes, "edges": edges, "taxonomy": data.get("risks", [])}


def export_offline_data() -> Path:
    payload = load_taxonomy()
    payload["cases"] = [
        {key: value for key, value in item.items() if key != "pkulaw_raw"}
        for item in payload.get("cases", [])
    ]
    OFFLINE_DATA_PATH.write_text(
        "/* Generated from local seed + sanitized Pkulaw cache. Keep beside index.html for offline use. */\n"
        "window.RISK_MAP_DATA = "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
        newline="\n",
    )
    return OFFLINE_DATA_PATH


def sync_pkulaw_cases(risk_codes: Iterable[str] | None = None, limit_per_risk: int = 8) -> dict[str, Any]:
    taxonomy = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    selected = set(risk_codes or [])
    risks = [risk for risk in taxonomy["risks"] if not selected or risk["code"] in selected]
    client = PkuLawMCPClient()
    if not client.configured:
        raise PkuLawMCPError(
            "尚未配置北大法宝 Token。请登录 https://mcp.pkulaw.com/ 控制台获取后，"
            "在 .env 中设置 PKULAW_ACCESS_TOKEN。"
        )
    synced: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for risk in risks:
        cause_terms = risk.get("cause", [])[:4]
        query_terms = risk.get("query", [])[:4]
        query = "；".join(dict.fromkeys(cause_terms + query_terms))
        try:
            raw = client.search_cases(query, mode="semantic", limit=limit_per_risk)
            records = normalize_case_records(raw)
            if not records and query_terms:
                fallback_query = " ".join(query_terms[:3])
                raw = client.search_cases(fallback_query, mode="semantic", limit=limit_per_risk)
                records = normalize_case_records(raw)
            for record in records[:limit_per_risk]:
                evidence = _classify_case_risk(record, risk)
                if not evidence:
                    continue
                record["risk_codes"] = sorted(set(record.get("risk_codes", []) + [risk["code"]]))
                if not record.get("stage_ids"):
                    record["stage_ids"] = risk.get("stage_ids", [])[:2]
                if not record.get("law_ids"):
                    record["law_ids"] = risk.get("law_ids", [])[:3]
                record.setdefault("tag_evidence", {})[risk["code"]] = evidence
                synced.append(record)
        except Exception as exc:  # keep partial results from other risk types
            errors.append({"risk_code": risk["code"], "message": str(exc)})

    existing: list[dict[str, Any]] = []
    if CACHE_PATH.exists():
        try:
            existing = json.loads(CACHE_PATH.read_text(encoding="utf-8")).get("cases", [])
        except (OSError, ValueError, TypeError):
            existing = []
    merged = _merge_cases(existing, synced)
    curated = _curate_cases(merged, taxonomy["risks"])

    synced_at = datetime.now(timezone.utc).isoformat()
    CACHE_PATH.write_text(
        json.dumps({"synced_at": synced_at, "cases": curated}, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    export_offline_data()
    return {
        "ok": bool(synced),
        "synced": len(synced),
        "cached_total": len(curated),
        "filtered_out": len(merged) - len(curated),
        "synced_at": synced_at,
        "errors": errors,
        "offline_updated": True,
    }


def curate_pkulaw_cache() -> dict[str, int]:
    """Re-apply the user-defined cause/body keyword rules to the local MCP cache."""
    taxonomy = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    cached = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
    before = cached.get("cases", [])
    after = _curate_cases(before, taxonomy.get("risks", []))
    CACHE_PATH.write_text(
        json.dumps(
            {
                "synced_at": cached.get("synced_at", ""),
                "curated_at": datetime.now(timezone.utc).isoformat(),
                "cases": after,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    export_offline_data()
    return {"before": len(before), "after": len(after), "removed": len(before) - len(after)}


def normalize_case_records(raw: Any) -> list[dict[str, Any]]:
    decoded = _decode_embedded_json(raw)
    candidates = list(_record_lists(decoded))
    title_keys = {"title", "case_name", "caseName", "name", "document_title", "documentTitle"}

    def candidate_score(items: list[dict[str, Any]]) -> tuple[int, int]:
        record_like = sum(1 for item in items if title_keys.intersection(item))
        return record_like, len(items)

    records = max(candidates, key=candidate_score) if candidates else []
    normalized: list[dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        name = _pick(item, "title", "case_name", "caseName", "name", "document_title", "documentTitle")
        if not name:
            continue
        case_no = _pick(item, "case_no", "caseNo", "case_number", "caseNumber", "案号")
        source_url = _pick(item, "source_url", "sourceUrl", "url", "link", "doc_url", "docUrl")
        court = _pick(item, "court", "court_name", "courtName", "courthouse_name", "法院")
        year = _pick(
            item,
            "year",
            "judgment_year",
            "judgmentYear",
            "date",
            "judgment_date",
            "decision_date",
            "decisionDate",
        )[:10]
        summary = _pick(
            item,
            "summary",
            "abstract",
            "ascertain",
            "identified",
            "referee_result",
            "content",
            "text",
            "裁判要旨",
        )
        digest = hashlib.sha1(f"{case_no}|{name}".encode("utf-8")).hexdigest()[:14]
        normalized.append(
            {
                "id": f"PKULAW-{digest}",
                "name": name,
                "case_no": case_no,
                "year": year,
                "court": court,
                "kind": "北大法宝裁判文书",
                "cause": _pick(item, "cause", "cause_of_action", "causeOfAction", "案由"),
                "case_type": _pick(item, "case_type", "caseType"),
                "doc_type": _pick(item, "doc_type", "docType"),
                "summary": summary[:600],
                "risk_codes": [],
                "stage_ids": [],
                "law_ids": [],
                "source_url": source_url,
                "source_provider": "北大法宝 MCP",
                "pkulaw_id": _pick(item, "gid", "id", "doc_id", "docId"),
            }
        )
    return normalized


def _add_edge(
    edges: list[dict[str, Any]],
    keys: set[tuple[str, str, str]],
    source: str,
    target: str,
    relation: str,
) -> None:
    key = (source, target, relation)
    if key not in keys:
        keys.add(key)
        edges.append({"id": f"E-{len(edges) + 1}", "source": source, "target": target, "relation": relation})


def _merge_cases(base: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {item["id"]: item for item in base if item.get("id")}
    for item in incoming:
        case_id = item.get("id")
        if not case_id:
            continue
        if case_id in merged:
            old = merged[case_id]
            combined = {**old, **item}
            for key in ("risk_codes", "stage_ids", "law_ids"):
                combined[key] = sorted(set(old.get(key, []) + item.get(key, [])))
            combined["tag_evidence"] = {
                **old.get("tag_evidence", {}),
                **item.get("tag_evidence", {}),
            }
            merged[case_id] = combined
        else:
            merged[case_id] = item
    return list(merged.values())


def _classify_case_risk(record: dict[str, Any], risk: dict[str, Any]) -> dict[str, str] | None:
    """Return evidence only when a cause term or body/title query term actually occurs."""
    title = str(record.get("name", ""))
    cause_text = str(record.get("cause", ""))
    summary = str(record.get("summary", ""))
    cause_terms = [str(term) for term in risk.get("cause", []) if term]
    query_terms = [str(term) for term in risk.get("query", []) if term]
    cause_hit = next((term for term in cause_terms if term in cause_text or term in title), "")
    if cause_hit:
        return {"signal": "案由命中", "matched": cause_hit}
    searchable = " ".join((title, cause_text, summary))
    query_hit = next((term for term in query_terms if term in searchable), "")
    if query_hit:
        return {"signal": "正文命中", "matched": query_hit}
    return None


def _curate_cases(cases: list[dict[str, Any]], risks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    risk_by_code = {risk["code"]: risk for risk in risks}
    curated: list[dict[str, Any]] = []
    for original in cases:
        item = dict(original)
        kept_codes: list[str] = []
        evidence: dict[str, dict[str, str]] = {}
        for code in item.get("risk_codes", []):
            risk = risk_by_code.get(code)
            match = _classify_case_risk(item, risk) if risk else None
            if match:
                kept_codes.append(code)
                evidence[code] = match
        if not kept_codes:
            continue
        item["risk_codes"] = sorted(set(kept_codes))
        item["tag_evidence"] = evidence
        item["stage_ids"] = sorted(
            {stage_id for code in kept_codes for stage_id in risk_by_code[code].get("stage_ids", [])[:2]}
        )
        item["law_ids"] = sorted(
            {law_id for code in kept_codes for law_id in risk_by_code[code].get("law_ids", [])[:3]}
        )
        curated.append(item)
    return curated


def _pick(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and not isinstance(value, (dict, list)):
            text = _clean_text(str(value))
            if text:
                return text
    return ""


def _clean_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def _decode_embedded_json(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("{", "[")):
            try:
                return _decode_embedded_json(json.loads(text))
            except ValueError:
                return value
        return value
    if isinstance(value, list):
        return [_decode_embedded_json(item) for item in value]
    if isinstance(value, dict):
        if value.get("type") == "text" and isinstance(value.get("text"), str):
            return _decode_embedded_json(value["text"])
        return {key: _decode_embedded_json(item) for key, item in value.items()}
    return value


def _record_lists(value: Any):
    if isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            yield value
        for item in value:
            yield from _record_lists(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _record_lists(item)
