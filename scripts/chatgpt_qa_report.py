from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
QA_DIR = ROOT / "research" / "chatgpt_qa"
QUESTION_PATH = ROOT / "config" / "chatgpt_qa_prompt.md"
REPORT_ID_RE = re.compile(r"^chatgpt_qa_\d{4}-\d{2}-\d{2}$")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def normalize_report(payload: dict[str, Any]) -> dict[str, Any]:
    basis_date = _text(payload.get("basis_date"))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", basis_date):
        raise ValueError("basis_date must use YYYY-MM-DD")
    report_id = _text(payload.get("report_id")) or f"chatgpt_qa_{basis_date}"
    if not REPORT_ID_RE.fullmatch(report_id):
        raise ValueError("report_id must use chatgpt_qa_YYYY-MM-DD")
    question = _text(payload.get("question"))
    if not question and QUESTION_PATH.exists():
        question = QUESTION_PATH.read_text(encoding="utf-8").strip()
    answer = _text(payload.get("answer_markdown") or payload.get("answer"))
    if not question or not answer:
        raise ValueError("question and answer_markdown are required")

    summary_fields: list[dict[str, str]] = []
    for item in _list(payload.get("summary_fields")):
        if not isinstance(item, dict):
            continue
        field = _text(item.get("field"))
        content = _text(item.get("content"))
        if field and content:
            summary_fields.append({"field": field, "content": content})

    ranking: list[dict[str, Any]] = []
    for index, item in enumerate(_list(payload.get("ranking")), start=1):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["rank"] = row.get("rank") or index
        ranking.append(row)

    daily_changes = payload.get("daily_changes")
    if not isinstance(daily_changes, dict):
        daily_changes = {"summary": "今日时代主线判断无实质变化。"}

    return {
        "report_id": report_id,
        "generated_at": _text(payload.get("generated_at")) or date.today().isoformat(),
        "basis_date": basis_date,
        "source_report_id": _text(payload.get("source_report_id")),
        "question": question,
        "answer_markdown": answer,
        "summary_fields": summary_fields,
        "ranking": ranking,
        "daily_changes": daily_changes,
        "data_basis": payload.get("data_basis") if isinstance(payload.get("data_basis"), dict) else {},
        "read_only": True,
        "report_type": "chatgpt_era_mainline_qa",
    }


def write_report(payload: dict[str, Any]) -> tuple[Path, Path]:
    report = normalize_report(payload)
    QA_DIR.mkdir(parents=True, exist_ok=True)
    json_path = QA_DIR / f"{report['report_id']}.json"
    md_path = QA_DIR / f"{report['report_id']}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown = [
        f"# ChatGPT问答：{report['basis_date']}",
        "",
        f"- 报告 ID：`{report['report_id']}`",
        f"- 来源主线报告：`{report['source_report_id'] or '未指定'}`",
        "",
        "## 提问",
        "",
        report["question"],
        "",
        "## 摘要字段",
        "",
        "| 字段 | 摘要内容 |",
        "| --- | --- |",
    ]
    for item in report["summary_fields"]:
        markdown.append(f"| {item['field'].replace('|', '/')} | {item['content'].replace('|', '/').replace(chr(10), '<br>')} |")
    markdown.extend(["", "## 完整回答", "", report["answer_markdown"], ""])
    if report["ranking"]:
        markdown.extend(["## 主线排名", "", "| 排名 | 时代主线 | 产业阶段 | 确定性 | 估值 | 未来空间 | 综合评分 | 当前策略 |", "| ---: | --- | --- | ---: | ---: | ---: | ---: | --- |"])
        for row in report["ranking"]:
            markdown.append("| {rank} | {theme} | {stage} | {certainty} | {valuation} | {space} | {score} | {strategy} |".format(
                rank=row.get("rank", ""),
                theme=_text(row.get("theme")),
                stage=_text(row.get("stage")),
                certainty=_text(row.get("certainty")),
                valuation=_text(row.get("valuation")),
                space=_text(row.get("future_space")),
                score=_text(row.get("score")),
                strategy=_text(row.get("strategy")),
            ))
    md_path.write_text("\n".join(markdown) + "\n", encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and store one ChatGPT era-mainline answer.")
    parser.add_argument("--input", required=True, help="JSON file containing one normalized answer payload.")
    args = parser.parse_args()
    source = Path(args.input)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("input JSON must be an object")
    json_path, md_path = write_report(payload)
    print(json.dumps({"status": "written", "json": str(json_path), "markdown": str(md_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
