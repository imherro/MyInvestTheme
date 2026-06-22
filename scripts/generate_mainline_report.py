from __future__ import annotations

import argparse
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import tushare as ts

from canonical_mainline import (
    assert_canonical_mainline_contract,
    build_canonical_mainline_summary,
    build_legacy_theme_ranking,
    build_mainline_ranking,
)
from data_quality_guard import (
    assert_required_data_quality,
    build_data_quality_summary,
    build_stage_status,
    clean_records_safe,
    empty_dataframe_with_columns,
    load_data_quality_rules,
    run_optional_stage,
)
from mainline_contract_validator import validate_mainline_report_contract
from policy_provenance import SCORING_VERSION as POLICY_PROVENANCE_VERSION
from policy_provenance import build_policy_provenance_summary
from policy_snapshot_integrity import SCORING_VERSION as POLICY_SNAPSHOT_VERSION
from policy_snapshot_integrity import (
    assert_policy_snapshot_integrity,
    build_policy_snapshot_summary,
    build_updated_snapshot_registry,
    load_snapshot_registry,
)
from snapshot_registry_finalizer import SCORING_VERSION as SNAPSHOT_REGISTRY_FINALIZATION_VERSION
from snapshot_registry_finalizer import finalize_report_artifacts_with_registry
from policy_signals import load_policy_store, policy_event_summary, policy_theme_summary, score_policy_by_theme


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "research" / "mainline"
TZ = ZoneInfo("Asia/Shanghai")
POLICY_WEIGHT = 0.15
DATA_QUALITY_RULES = load_data_quality_rules()
DATA_QUALITY_SCHEMAS = DATA_QUALITY_RULES.get("schemas", {})
DATA_QUALITY_DEFAULTS = DATA_QUALITY_RULES.get("defaults", {})

SW_TOP_FIELDS = ["ts_code", "name", "r1", "r5", "r20", "amount_ratio", "pe", "pb", "r1_rank", "r5_rank", "r20_rank", "amount_ratio_rank", "score"]
THS_TOP_FIELDS = ["ts_code", "name", "type", "r1", "r5", "r20", "turnover_rate", "r1_rank", "r5_rank", "r20_rank", "turnover_rate_rank", "score"]
ETF_TOP_FIELDS = ["ts_code", "name", "r1", "r5", "r20", "amount", "r1_rank", "r5_rank", "r20_rank", "amount_rank", "score"]


BROAD_INDEXES = [
    ("000001.SH", "上证综指"),
    ("000300.SH", "沪深300"),
    ("000905.SH", "中证500"),
    ("000852.SH", "中证1000"),
    ("000688.SH", "科创50"),
    ("399006.SZ", "创业板指"),
    ("399001.SZ", "深证成指"),
]


def default_breadth() -> dict[str, Any]:
    return {
        "rows": 0,
        "up_ratio": 0.0,
        "median_pct_chg": 0.0,
        "gt_5_count": 0,
        "lt_minus_5_count": 0,
        "r5_positive_ratio": 0.0,
        "r20_positive_ratio": 0.0,
        "median_r5": 0.0,
        "median_r20": 0.0,
    }


def empty_stage_frame(stage: str) -> pd.DataFrame:
    return empty_dataframe_with_columns(DATA_QUALITY_SCHEMAS.get(stage, []))


def empty_limit_up_result() -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    return empty_stage_frame("limit_up"), []


def empty_moneyflow_result() -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    return empty_stage_frame("moneyflow"), []


@dataclass(frozen=True)
class ThemeSpec:
    name: str
    sw_names: tuple[str, ...]
    ths_keywords: tuple[str, ...]
    etf_keywords: tuple[str, ...]
    limit_keywords: tuple[str, ...]
    flow_keywords: tuple[str, ...]


THEMES = [
    ThemeSpec(
        name="硬科技电子/半导体",
        sw_names=("电子",),
        ths_keywords=("半导体", "芯片", "印制电路板", "PCB", "电子化学品", "元件", "被动元件", "封测", "集成电路", "面板", "光学元件", "消费电子"),
        etf_keywords=("半导体", "芯片", "电子", "科创板半导体材料设备", "集成电路"),
        limit_keywords=("元件", "半导体", "其他电子", "光学光电", "电子化学品", "消费电子"),
        flow_keywords=("半导体", "元器件", "IT设备", "其他电子", "光学光电"),
    ),
    ThemeSpec(
        name="高端制造/机器人/军工",
        sw_names=("机械设备", "国防军工"),
        ths_keywords=("机器人", "自动化", "机床", "工业机械", "军工", "军工电子", "通用设备", "专用设备", "工程机械"),
        etf_keywords=("机器人", "机床", "工业机械", "军工", "高端装备", "机械"),
        limit_keywords=("通用设备", "专用设备", "工程机械", "军工", "自动化", "机器人", "航天", "船舶"),
        flow_keywords=("通用机械", "专用机械", "工程机械", "船舶", "航空", "军工", "机械"),
    ),
    ThemeSpec(
        name="建材/稳增长修复",
        sw_names=("建筑材料", "建筑装饰"),
        ths_keywords=("玻璃玻纤", "建筑材料", "建筑产品", "装修建材", "水泥", "玻璃", "基建"),
        etf_keywords=("建筑材料", "建材", "基建", "建筑"),
        limit_keywords=("玻璃", "装修建材", "建筑材料", "水泥", "专业工程", "工程建设", "非金属材"),
        flow_keywords=("玻璃", "其他建材", "水泥", "装修建材", "建筑", "工程"),
    ),
    ThemeSpec(
        name="AI算力/通信",
        sw_names=("通信", "计算机"),
        ths_keywords=("通信", "算力", "数据中心", "CPO", "光模块", "光通信", "5G", "人工智能", "AI", "服务器"),
        etf_keywords=("通信", "5G", "人工智能", "AI", "云计算", "数据", "计算机", "软件"),
        limit_keywords=("通信", "计算机", "软件", "互联网", "IT设备", "数据中心", "算力"),
        flow_keywords=("通信", "软件服务", "互联网", "IT设备", "计算机"),
    ),
    ThemeSpec(
        name="新能源/电力设备",
        sw_names=("电力设备",),
        ths_keywords=("电池", "锂电", "光伏", "风电", "储能", "电网", "新能源", "固态电池"),
        etf_keywords=("新能源", "电池", "光伏", "电网", "储能", "锂电", "电力设备"),
        limit_keywords=("电池", "光伏", "电网", "风电", "电力设备", "储能"),
        flow_keywords=("电气设备", "电池", "光伏", "电网设备", "电力设备"),
    ),
    ThemeSpec(
        name="资源周期",
        sw_names=("有色金属", "钢铁", "煤炭", "石油石化"),
        ths_keywords=("铜", "黄金", "稀土", "煤炭", "石油", "有色", "钢铁", "小金属", "工业金属"),
        etf_keywords=("有色", "煤炭", "钢铁", "稀土", "黄金", "能源", "资源", "工业金属"),
        limit_keywords=("小金属", "有色", "黄金", "铜", "煤炭", "钢铁", "石油"),
        flow_keywords=("小金属", "铜", "铝", "黄金", "有色", "煤炭", "钢铁", "石油"),
    ),
    ThemeSpec(
        name="消费/传媒",
        sw_names=("食品饮料", "商贸零售", "传媒", "社会服务", "美容护理", "家用电器"),
        ths_keywords=("消费", "传媒", "游戏", "影视", "旅游", "家电", "食品饮料", "零售", "免税", "教育"),
        etf_keywords=("消费", "传媒", "游戏", "旅游", "家电", "食品饮料", "零售"),
        limit_keywords=("消费", "传媒", "游戏", "影视", "旅游", "食品", "零售", "家居用品", "家电"),
        flow_keywords=("食品饮料", "传媒", "影视", "游戏", "旅游", "家电", "零售", "家居用品"),
    ),
    ThemeSpec(
        name="创新药/医药",
        sw_names=("医药生物",),
        ths_keywords=("创新药", "医疗器械", "生物制品", "疫苗", "CRO", "医药", "制药", "生命科学"),
        etf_keywords=("医药", "医疗", "创新药", "生物", "疫苗"),
        limit_keywords=("医药", "医疗器械", "生物制品", "化学制药", "中药", "疫苗"),
        flow_keywords=("医药", "医疗器械", "生物制品", "化学制药", "中药"),
    ),
]


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def make_client():
    load_env()
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN is missing. Put it in .env.")
    return ts.pro_api(token)


def q(pro: Any, api_name: str, **kwargs: Any) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            fn = getattr(pro, api_name)
            return fn(**kwargs)
        except Exception as exc:  # Tushare occasionally drops HTTP connections.
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Tushare API failed: {api_name} {kwargs}") from last_error


def pct(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def rounded(value: Any, digits: int = 4) -> float | None:
    number = pct(value)
    return None if number is None else round(number, digits)


def fmt_pct(value: Any) -> str:
    number = pct(value)
    return "" if number is None else f"{number:.2f}%"


def fmt_number(value: Any, digits: int = 2) -> str:
    number = pct(value)
    return "" if number is None else f"{number:.{digits}f}"


def percentile_rank(series: pd.Series) -> pd.Series:
    return series.astype(float).rank(pct=True)


def join_names(items: list[dict[str, Any]], limit: int = 5) -> str:
    return "、".join(str(item["name"]) for item in items[:limit] if item.get("name"))


def contains_any(text: Any, keywords: tuple[str, ...]) -> bool:
    value = "" if pd.isna(text) else str(text)
    return any(keyword in value for keyword in keywords)


def contains_any_mask(series: pd.Series, keywords: tuple[str, ...]) -> pd.Series:
    if series.empty:
        return pd.Series([], index=series.index, dtype=bool)
    return series.apply(lambda value: contains_any(value, keywords)).astype(bool)


def get_trade_dates(pro: Any, today: str) -> list[str]:
    end = today.replace("-", "")
    start_dt = datetime.strptime(end, "%Y%m%d") - timedelta(days=120)
    cal = q(
        pro,
        "trade_cal",
        exchange="",
        start_date=start_dt.strftime("%Y%m%d"),
        end_date=end,
        fields="cal_date,is_open,pretrade_date",
    )
    open_days = cal.loc[cal["is_open"].astype(int) == 1, "cal_date"].astype(str).tolist()
    return sorted(open_days)


def choose_basis_date(pro: Any, open_days: list[str]) -> tuple[str, dict[str, Any]]:
    notes: dict[str, Any] = {"checked": []}
    for trade_date in reversed(open_days):
        daily = q(pro, "daily", trade_date=trade_date, fields="ts_code,trade_date,pct_chg,close,amount")
        basic = q(pro, "daily_basic", trade_date=trade_date, fields="ts_code,trade_date,total_mv,turnover_rate,pe,pb")
        notes["checked"].append({"trade_date": trade_date, "daily_rows": len(daily), "daily_basic_rows": len(basic)})
        if len(daily) >= 5000 and len(basic) >= 5000:
            notes["daily_rows"] = len(daily)
            notes["daily_basic_rows"] = len(basic)
            notes["basis"] = trade_date
            return trade_date, notes
    raise RuntimeError("No complete trading day found in calendar window.")


def concat_daily(pro: Any, api_name: str, dates: list[str]) -> pd.DataFrame:
    frames = []
    for date in dates:
        frame = q(pro, api_name, trade_date=date)
        if not frame.empty:
            frames.append(frame)
        time.sleep(0.1)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def return_from_history(history: pd.DataFrame, code_col: str = "ts_code") -> pd.DataFrame:
    result = []
    for code, group in history.groupby(code_col):
        g = group.sort_values("trade_date")
        if len(g) < 21:
            continue
        cur = g.iloc[-1]
        prev5 = g.iloc[-6]
        prev20 = g.iloc[-21]
        close = pct(cur.get("close"))
        close5 = pct(prev5.get("close"))
        close20 = pct(prev20.get("close"))
        if close is None or close5 in (None, 0) or close20 in (None, 0):
            continue
        item = cur.to_dict()
        item["r1"] = pct(cur.get("pct_change", cur.get("pct_chg")))
        item["r5"] = (close / close5 - 1) * 100
        item["r20"] = (close / close20 - 1) * 100
        item["history_count"] = len(g)
        result.append(item)
    return pd.DataFrame(result)


def score_sw(pro: Any, dates: list[str]) -> pd.DataFrame:
    classify = q(pro, "index_classify", level="L1", src="SW2021")
    classify = classify[["index_code", "industry_name"]].rename(columns={"index_code": "ts_code", "industry_name": "name_l1"})
    history = concat_daily(pro, "sw_daily", dates)
    history = history.merge(classify, on="ts_code", how="inner")
    scored = return_from_history(history)
    if scored.empty:
        return scored
    scored["name"] = scored["name_l1"]
    current_amount = history.sort_values("trade_date").groupby("ts_code").tail(1)[["ts_code", "amount"]]
    avg_amount = history.groupby("ts_code")["amount"].mean().reset_index(name="amount_avg20")
    scored = scored.merge(current_amount.rename(columns={"amount": "amount_current"}), on="ts_code", how="left")
    scored = scored.merge(avg_amount, on="ts_code", how="left")
    scored["amount_ratio"] = scored["amount_current"] / scored["amount_avg20"]
    for col in ("r1", "r5", "r20", "amount_ratio"):
        scored[f"{col}_rank"] = percentile_rank(scored[col])
    scored["score"] = 100 * (
        0.25 * scored["r1_rank"]
        + 0.35 * scored["r5_rank"]
        + 0.25 * scored["r20_rank"]
        + 0.15 * scored["amount_ratio_rank"]
    )
    return scored.sort_values("score", ascending=False)


def score_ths(pro: Any, dates: list[str]) -> pd.DataFrame:
    index = q(pro, "ths_index", exchange="A", type="I")
    index = index[["ts_code", "name", "type"]]
    history = concat_daily(pro, "ths_daily", dates)
    history = history.merge(index, on="ts_code", how="inner")
    scored = return_from_history(history)
    if scored.empty:
        return scored
    for col in ("r1", "r5", "r20", "turnover_rate"):
        scored[f"{col}_rank"] = percentile_rank(scored[col])
    scored["score"] = 100 * (
        0.25 * scored["r1_rank"]
        + 0.35 * scored["r5_rank"]
        + 0.25 * scored["r20_rank"]
        + 0.15 * scored["turnover_rate_rank"]
    )
    return scored.sort_values("score", ascending=False)


def score_etf(pro: Any, dates: list[str]) -> pd.DataFrame:
    basic = q(pro, "fund_basic", market="E")
    basic = basic[(basic["status"] == "L") & basic["name"].astype(str).str.contains("ETF", na=False)]
    basic = basic[~basic["name"].astype(str).str.contains("货币|债|短融|政金债|国债|信用债|可转债", regex=True, na=False)]
    history = concat_daily(pro, "fund_daily", dates)
    history = history.merge(basic[["ts_code", "name", "fund_type", "market"]], on="ts_code", how="inner")
    scored = return_from_history(history)
    if scored.empty:
        return scored
    for col in ("r1", "r5", "r20", "amount"):
        scored[f"{col}_rank"] = percentile_rank(scored[col])
    scored["score"] = 100 * (
        0.20 * scored["r1_rank"]
        + 0.35 * scored["r5_rank"]
        + 0.30 * scored["r20_rank"]
        + 0.15 * scored["amount_rank"]
    )
    return scored.sort_values("score", ascending=False)


def stock_breadth(pro: Any, basis: str, d5: str, d20: str) -> dict[str, Any]:
    cur = q(pro, "daily", trade_date=basis, fields="ts_code,trade_date,pct_chg,close,amount")
    prev5 = q(pro, "daily", trade_date=d5, fields="ts_code,close").rename(columns={"close": "close_5"})
    prev20 = q(pro, "daily", trade_date=d20, fields="ts_code,close").rename(columns={"close": "close_20"})
    merged = cur.merge(prev5, on="ts_code", how="left").merge(prev20, on="ts_code", how="left")
    merged["r5"] = (merged["close"] / merged["close_5"] - 1) * 100
    merged["r20"] = (merged["close"] / merged["close_20"] - 1) * 100
    return {
        "rows": int(len(cur)),
        "up_ratio": float((cur["pct_chg"] > 0).mean() * 100),
        "median_pct_chg": float(cur["pct_chg"].median()),
        "gt_5_count": int((cur["pct_chg"] >= 5).sum()),
        "lt_minus_5_count": int((cur["pct_chg"] <= -5).sum()),
        "r5_positive_ratio": float((merged["r5"] > 0).mean() * 100),
        "r20_positive_ratio": float((merged["r20"] > 0).mean() * 100),
        "median_r5": float(merged["r5"].median()),
        "median_r20": float(merged["r20"].median()),
    }


def broad_index_data(pro: Any, basis: str, d5: str, d20: str) -> list[dict[str, Any]]:
    rows = []
    for code, name in BROAD_INDEXES:
        df = q(pro, "index_daily", ts_code=code, start_date=d20, end_date=basis)
        if df.empty:
            continue
        df = df.sort_values("trade_date")
        cur = df[df["trade_date"].astype(str) == basis]
        old5 = df[df["trade_date"].astype(str) == d5]
        old20 = df[df["trade_date"].astype(str) == d20]
        if cur.empty or old5.empty or old20.empty:
            continue
        current = cur.iloc[-1]
        close = float(current["close"])
        rows.append(
            {
                "code": code,
                "name": name,
                "close": close,
                "r1": rounded(current.get("pct_chg"), 4),
                "r5": (close / float(old5.iloc[-1]["close"]) - 1) * 100,
                "r20": (close / float(old20.iloc[-1]["close"]) - 1) * 100,
            }
        )
    return sorted(rows, key=lambda item: item["r5"], reverse=True)


def limit_up_data(pro: Any, basis: str) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    df = q(pro, "limit_list_d", trade_date=basis)
    if df.empty:
        return df, []
    up = df[df["limit"] == "U"].copy()
    grouped = (
        up.groupby("industry", dropna=False)
        .agg(limit_count=("ts_code", "count"), avg_turnover=("turnover_ratio", "mean"))
        .reset_index()
        .sort_values("limit_count", ascending=False)
    )
    top = [
        {
            "industry": str(row["industry"]),
            "limit_count": int(row["limit_count"]),
            "avg_turnover": rounded(row["avg_turnover"], 4),
        }
        for _, row in grouped.head(20).iterrows()
    ]
    return up, top


def moneyflow_data(pro: Any, basis: str) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    mf = q(pro, "moneyflow", trade_date=basis)
    stocks = q(pro, "stock_basic", exchange="", list_status="L")
    if mf.empty:
        return mf, []
    mf["large_net"] = (mf["buy_lg_amount"] + mf["buy_elg_amount"]) - (mf["sell_lg_amount"] + mf["sell_elg_amount"])
    mf = mf.merge(stocks[["ts_code", "industry"]], on="ts_code", how="left")
    grouped = (
        mf.groupby("industry", dropna=False)
        .agg(net=("net_mf_amount", "sum"), large_net=("large_net", "sum"), count=("ts_code", "count"))
        .reset_index()
        .sort_values("large_net", ascending=False)
    )
    top = [
        {
            "industry": str(row["industry"]),
            "net": rounded(row["net"], 4),
            "large_net": rounded(row["large_net"], 4),
            "count": int(row["count"]),
        }
        for _, row in grouped.head(20).iterrows()
    ]
    return mf, top


def baostock_check(basis: str) -> list[dict[str, Any]]:
    try:
        import baostock as bs
    except Exception as exc:
        return [{"error": f"baostock import failed: {exc}"}]
    checks = [
        ("上证综指", "sh.000001"),
        ("创业板指", "sz.399006"),
        ("科创50", "sh.000688"),
    ]
    out = []
    login = bs.login()
    try:
        for name, code in checks:
            rs = bs.query_history_k_data_plus(
                code,
                "date,code,close,pctChg",
                start_date=f"{basis[:4]}-{basis[4:6]}-{basis[6:]}",
                end_date=f"{basis[:4]}-{basis[4:6]}-{basis[6:]}",
                frequency="d",
                adjustflag="3",
            )
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            out.append({"name": name, "code": code, "rows": rows, "error_code": rs.error_code, "error_msg": rs.error_msg})
    finally:
        bs.logout()
    if login.error_code != "0":
        out.append({"error": f"baostock login failed: {login.error_msg}"})
    return out


def theme_rows(
    sw: pd.DataFrame,
    ths: pd.DataFrame,
    etf: pd.DataFrame,
    limit_up: pd.DataFrame,
    moneyflow: pd.DataFrame,
    policy_by_theme: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    policy_by_theme = policy_by_theme or {}
    sw = empty_stage_frame("sw_score") if sw is None else sw
    ths = empty_stage_frame("ths_score") if ths is None else ths
    etf = empty_stage_frame("etf_score") if etf is None else etf
    limit_up = empty_stage_frame("limit_up") if limit_up is None else limit_up
    moneyflow = empty_stage_frame("moneyflow") if moneyflow is None else moneyflow
    sw = sw.copy()
    ths = ths.copy()
    etf = etf.copy()
    limit_up = limit_up.copy()
    moneyflow = moneyflow.copy()
    for frame, stage in (
        (sw, "sw_score"),
        (ths, "ths_score"),
        (etf, "etf_score"),
        (limit_up, "limit_up"),
        (moneyflow, "moneyflow"),
    ):
        for column in DATA_QUALITY_SCHEMAS.get(stage, []):
            if column not in frame.columns:
                frame[column] = DATA_QUALITY_DEFAULTS.get(column)
    for spec in THEMES:
        sw_match = sw[sw["name"].isin(spec.sw_names)].copy()
        ths_match = ths[contains_any_mask(ths["name"], spec.ths_keywords)].copy()
        etf_match = etf[contains_any_mask(etf["name"], spec.etf_keywords)].copy()

        limit_count = 0
        if not limit_up.empty:
            mask = contains_any_mask(limit_up["industry"], spec.limit_keywords) | contains_any_mask(limit_up["name"], spec.limit_keywords)
            limit_count = int(mask.sum())

        large_net = 0.0
        if not moneyflow.empty and "industry" in moneyflow.columns:
            flow_mask = contains_any_mask(moneyflow["industry"], spec.flow_keywords)
            large_net = float(moneyflow.loc[flow_mask, "large_net"].sum())

        top_ths = ths_match.sort_values("score", ascending=False).head(8)
        top_etf = etf_match.sort_values("score", ascending=False).head(5)

        sw_score = float(sw_match["score"].mean()) if not sw_match.empty else 0.0
        ths_score = float(top_ths["score"].mean()) if not top_ths.empty else 0.0
        etf_score = float(top_etf["score"].mean()) if not top_etf.empty else 0.0
        limit_score = min(100.0, limit_count * 8.0)
        policy = policy_by_theme.get(spec.name, {})
        policy_score = float(policy.get("score") or 0.0)
        policy_details = policy.get("top_policies") or []
        top_policy = "、".join(
            f"{item.get('published_date', '')} {item.get('source', '')} {item.get('title', '')}"
            for item in policy_details[:3]
        )

        rows.append(
            {
                "theme_id": policy.get("theme_id", ""),
                "theme": spec.name,
                "sw_score": sw_score,
                "ths_score": ths_score,
                "etf_score": etf_score,
                "limit_count": limit_count,
                "limit_score": limit_score,
                "large_net": large_net,
                "policy_score": policy_score,
                "policy_evidence_count": int(policy.get("evidence_count") or 0),
                "mainline_score_v6": float(policy.get("mainline_score_v6") or 0.0),
                "theme_score_v5": float(policy.get("theme_score_v5") or 0.0),
                "theme_score_v4_stance_adjusted": float(
                    policy.get("theme_score_v4_stance_adjusted") or policy.get("theme_score_v4") or 0.0
                ),
                "theme_score_v4": float(policy.get("theme_score_v4") or 0.0),
                "theme_score_v3_dedup": float(policy.get("theme_score_v3_dedup") or policy.get("theme_score_v3") or 0.0),
                "theme_score_v3": float(policy.get("theme_score_v3") or 0.0),
                "theme_score_v2_raw": float(policy.get("theme_score_v2_raw") or 0.0),
                "allocation_adjustment_effect": float(policy.get("allocation_adjustment_effect") or 0.0),
                "matched_event_cluster_count": int(policy.get("matched_event_cluster_count") or 0),
                "matched_allocated_event_count": int(policy.get("matched_allocated_event_count") or 0),
                "matched_policy_count_raw": int(policy.get("matched_policy_count_raw") or 0),
                "deduplication_effect": float(policy.get("deduplication_effect") or 0.0),
                "stance_adjustment_effect": float(policy.get("stance_adjustment_effect") or 0.0),
                "primary_event_count": int(policy.get("primary_event_count") or 0),
                "co_primary_event_count": int(policy.get("co_primary_event_count") or 0),
                "secondary_event_count": int(policy.get("secondary_event_count") or 0),
                "peripheral_event_count": int(policy.get("peripheral_event_count") or 0),
                "supportive_cluster_count": int(policy.get("supportive_cluster_count") or 0),
                "mildly_supportive_cluster_count": int(policy.get("mildly_supportive_cluster_count") or 0),
                "neutral_or_mixed_cluster_count": int(policy.get("neutral_or_mixed_cluster_count") or 0),
                "mildly_restrictive_cluster_count": int(policy.get("mildly_restrictive_cluster_count") or 0),
                "restrictive_cluster_count": int(policy.get("restrictive_cluster_count") or 0),
                "avg_allocation_share": float(policy.get("avg_allocation_share") or 0.0),
                "avg_cluster_relevance_score_v2": float(policy.get("avg_cluster_relevance_score_v2") or 0.0),
                "avg_cluster_policy_score_v2": float(policy.get("avg_cluster_policy_score_v2") or 0.0),
                "avg_cluster_stance_score_v2": float(policy.get("avg_cluster_stance_score_v2") or 0.0),
                "lifecycle_state": policy.get("lifecycle_state", ""),
                "state_multiplier": float(policy.get("state_multiplier") or 0.0),
                "breadth_score": float(policy.get("breadth_score") or 0.0),
                "lifecycle_quality_multiplier": float(policy.get("lifecycle_quality_multiplier") or 0.0),
                "score_7d": float(policy.get("score_7d") or 0.0),
                "score_30d": float(policy.get("score_30d") or 0.0),
                "score_31_60d": float(policy.get("score_31_60d") or 0.0),
                "score_61_90d": float(policy.get("score_61_90d") or 0.0),
                "score_90d": float(policy.get("score_90d") or 0.0),
                "older_score": float(policy.get("older_score") or 0.0),
                "undated_score": float(policy.get("undated_score") or 0.0),
                "event_count_30d": int(policy.get("event_count_30d") or 0),
                "event_count_90d": int(policy.get("event_count_90d") or 0),
                "source_org_count_90d": int(policy.get("source_org_count_90d") or 0),
                "active_window_count": int(policy.get("active_window_count") or 0),
                "persistence_score": float(policy.get("persistence_score") or 0.0),
                "acceleration_delta_30d": float(policy.get("acceleration_delta_30d") or 0.0),
                "acceleration_ratio_30d": float(policy.get("acceleration_ratio_30d") or 0.0),
                "lifecycle_reasons": policy.get("lifecycle_reasons") or [],
                "top_policy": top_policy,
                "policy_details": policy_details,
                "top_sw": join_names(sw_match.sort_values("score", ascending=False).to_dict("records"), limit=4),
                "top_ths": join_names(top_ths.to_dict("records"), limit=5),
                "top_etf": "、".join(
                    f"{row['ts_code']} {row['name']}" for _, row in top_etf.head(3).iterrows()
                ),
                "evidence_count": 0,
                "flow_rank": 0.0,
                "evidence_score": 0.0,
                "stage": "",
            }
        )

    df = pd.DataFrame(rows)
    df["flow_rank"] = percentile_rank(df["large_net"])
    df["market_score"] = (
        0.25 * df["sw_score"]
        + 0.30 * df["ths_score"]
        + 0.25 * df["etf_score"]
        + 0.10 * df["limit_score"]
        + 0.10 * df["flow_rank"] * 100
    )
    df["evidence_score"] = (1 - POLICY_WEIGHT) * df["market_score"] + POLICY_WEIGHT * df["policy_score"]

    final = []
    for _, row in df.sort_values("evidence_score", ascending=False).iterrows():
        evidence_count = 0
        evidence_count += 1 if row["sw_score"] > 0 else 0
        evidence_count += 1 if row["ths_score"] > 0 else 0
        evidence_count += 1 if row["etf_score"] > 0 else 0
        evidence_count += 1 if int(row["limit_count"]) > 0 else 0
        evidence_count += 1 if float(row["large_net"]) > 0 else 0
        evidence_count += 1 if int(row["policy_evidence_count"]) > 0 else 0
        score = float(row["evidence_score"])
        if score >= 85:
            stage = "主线确认"
        elif score >= 72:
            stage = "次主线/强修复"
        elif score >= 50:
            stage = "观察线"
        else:
            stage = "弱势/退潮"
        item = row.to_dict()
        item["evidence_count"] = evidence_count
        item["stage"] = stage
        final.append({k: clean_json_value(v) for k, v in item.items()})
    return final


def clean_json_value(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    if pd.isna(value):
        return None
    return value


def clean_records(df: pd.DataFrame, limit: int, fields: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for _, row in df.head(limit).iterrows():
        item = {}
        for field in fields:
            value = row.get(field)
            if isinstance(value, (np.integer,)):
                value = int(value)
            elif isinstance(value, (np.floating, float)):
                value = rounded(value, 6)
            item[field] = None if pd.isna(value) else value
        out.append(item)
    return out


def conclusion_lines(canonical_summary: dict[str, Any], mainline_ranking: list[dict[str, Any]], breadth: dict[str, Any]) -> list[str]:
    top = canonical_summary.get("top_mainline") or {}
    second = mainline_ranking[1] if len(mainline_ranking) > 1 else None
    lines = []
    if top:
        event_ids = "、".join(top.get("top_event_ids") or []) or "无"
        lines += [
            f"当前政策主线排序第一的是{top.get('theme_name', '')}，mainline_score_v6为{top.get('mainline_score_v6', 0):.4f}，生命周期状态为{top.get('lifecycle_state', '')}。",
            f"该主线的theme_score_v5为{top.get('theme_score_v5', 0):.4f}，30日分数为{top.get('score_30d', 0):.4f}，90日分数为{top.get('score_90d', 0):.4f}。",
            f"主要支撑事件包括：{event_ids}。",
            "本报告默认主线排序口径为mainline_score_v6，不使用旧evidence_score作为默认主线排序。",
        ]
    else:
        lines.append("当前没有可排序的政策主线。")
    if second:
        lines.append(
            f"第二梯队是{second.get('theme_name', '')}，mainline_score_v6为{second.get('mainline_score_v6', 0):.4f}，生命周期状态为{second.get('lifecycle_state', '')}。"
        )
    if breadth["r20_positive_ratio"] < 30:
        lines.append("全市场20日正收益股票比例仍低，说明行情仍偏结构性，不是全面普涨。")
    elif breadth["r20_positive_ratio"] >= 40:
        lines.append("全市场20日正收益股票比例明显抬升，市场土壤开始从结构性行情向更宽的风险偏好扩散。")
    else:
        lines.append("全市场中期广度处在修复区间，主线研究需要继续重视强弱分化。")
    return lines


def matched_keywords(evidence: list[dict[str, Any]], limit: int = 8) -> str:
    keywords = []
    seen = set()
    for item in evidence:
        keyword = str(item.get("keyword") or "")
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        keywords.append(keyword)
        if len(keywords) >= limit:
            break
    return " / ".join(keywords) if keywords else "无"


def matched_stance_keywords(evidence: list[dict[str, Any]], limit: int = 8) -> str:
    keywords = []
    seen = set()
    for item in evidence:
        keyword = str(item.get("stance_keyword") or item.get("keyword") or "")
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        keywords.append(keyword)
        if len(keywords) >= limit:
            break
    return " / ".join(keywords) if keywords else "无"


def _replace_data_quality_stage(payload: dict[str, Any], stage_status: dict[str, Any]) -> None:
    summary = payload.get("data_quality_summary") or {}
    statuses = [item for item in summary.get("stage_statuses", []) if item.get("stage") != stage_status.get("stage")]
    statuses.append(stage_status)
    payload["data_quality_summary"] = build_data_quality_summary(statuses)


def attach_contract_validation(payload: dict[str, Any], *, allow_pending_registry: bool = False) -> dict[str, Any]:
    _replace_data_quality_stage(
        payload,
        build_stage_status("contract_validation", "pass", True, 1, [], None),
    )
    summary = validate_mainline_report_contract(payload, allow_pending_registry=allow_pending_registry)
    if summary["error_count"]:
        _replace_data_quality_stage(
            payload,
            build_stage_status("contract_validation", "fail", True, 0, [], f"{summary['error_count']} contract errors"),
        )
        payload["contract_validation_summary"] = summary
        codes = ", ".join(issue["code"] for issue in summary["issues"] if issue["severity"] == "error")
        raise RuntimeError(f"Mainline report contract failed before write: {codes}")
    _replace_data_quality_stage(
        payload,
        build_stage_status("contract_validation", "pass", True, 1, [], None),
    )
    summary = validate_mainline_report_contract(payload, allow_pending_registry=allow_pending_registry)
    payload["contract_validation_summary"] = summary
    if summary["error_count"]:
        codes = ", ".join(issue["code"] for issue in summary["issues"] if issue["severity"] == "error")
        raise RuntimeError(f"Mainline report contract failed before write: {codes}")
    return summary


def render_markdown(payload: dict[str, Any]) -> str:
    basis = payload["basis_date"]
    policy_summary = payload.get("policy_summary") or {}
    theme_summary = payload.get("theme_summary") or {}
    event_cluster_summary = payload.get("event_cluster_summary") or {}
    policy_stance_summary = payload.get("policy_stance_summary") or {}
    event_theme_allocation_summary = payload.get("event_theme_allocation_summary") or {}
    mainline_lifecycle_summary = payload.get("mainline_lifecycle_summary") or {}
    policy_provenance_summary = payload.get("policy_provenance_summary") or {}
    policy_snapshot_summary = payload.get("policy_snapshot_summary") or {}
    canonical_summary = payload.get("canonical_mainline_summary") or {}
    data_quality_summary = payload.get("data_quality_summary") or {}
    contract_summary = payload.get("contract_validation_summary") or {}
    mainline_ranking = payload.get("mainline_ranking") or []
    legacy_theme_ranking = payload.get("legacy_theme_ranking") or payload.get("theme_ranking") or []
    lines = [
        f"# A股主线研究报告（基准日 {basis}）",
        "",
        f"- 生成时间：{payload['generated_at']}",
        f"- 数据边界：读取根目录 `数据源.md`，本次使用 Tushare 作为A股结构化主源，BaoStock 做主要指数轻量交叉验证；QMT本次不介入，因为任务是盘后完整日线研究，不涉及盘中价格或真实持仓导入。",
        f"- 完整性判断：名义日期 `{payload['nominal_today']}` 的最新完整行情落在 `{basis}`；Tushare 日线 {payload['completeness']['daily_rows']} 行、每日指标 {payload['completeness']['daily_basic_rows']} 行。",
        "- 结论性质：研究观察，不构成个股买卖建议。",
        "",
        "## 一句话结论",
        "",
    ]
    lines += [f"- {line}" for line in conclusion_lines(canonical_summary, mainline_ranking, payload["breadth"])]
    lines += [
        "",
        "## 默认主线口径",
        "",
        f"- 默认主线输出版本：{canonical_summary.get('scoring_version', 'canonical_mainline_output_v2')}",
        f"- 默认排序字段：{canonical_summary.get('default_score_field', 'mainline_score_v6')}",
        "- mainline_score_v6 = theme_score_v5 × lifecycle_quality_multiplier。",
        "- theme_score_v5 已包含政策强度、主题相关度、事件去重、政策方向性、事件-主题贡献分配。",
        "- 旧 evidence_score / market_score 仅作为兼容或市场背景观察，不参与默认主线排序。",
        "",
        "## 数据质量摘要",
        "",
        f"- 数据质量版本：{data_quality_summary.get('scoring_version', 'live_report_data_guard_v2')}",
        f"- 状态：{data_quality_summary.get('status', 'not_run')}",
        f"- 必需阶段失败数：{data_quality_summary.get('required_failure_count', 0)}",
        f"- 可选阶段失败数：{data_quality_summary.get('optional_failure_count', 0)}",
        f"- 空可选阶段数：{data_quality_summary.get('empty_optional_stage_count', 0)}",
        f"- 缺列阶段数：{data_quality_summary.get('missing_column_stage_count', 0)}",
        "",
        "阶段明细：",
    ]
    for status in data_quality_summary.get("stage_statuses", []):
        missing_columns = ",".join(status.get("missing_columns") or []) or "无"
        lines.append(
            f"- {status.get('stage', '')}：{status.get('status', '')}，row_count={status.get('row_count', 0)}，fallback_used={str(status.get('fallback_used', False)).lower()}，missing_columns={missing_columns}"
        )
    if data_quality_summary.get("status") == "degraded":
        lines += [
            "",
            "本报告 canonical mainline_score_v6 不受可选市场背景数据缺失影响；受影响的是旧市场证据观察区。",
        ]
    lines += [
        "",
        "## 政策来源溯源摘要",
        "",
        f"- 溯源版本：{policy_provenance_summary.get('scoring_version', POLICY_PROVENANCE_VERSION)}",
        f"- 状态：{policy_provenance_summary.get('status', 'not_run')}",
        f"- 原始政策数：{policy_provenance_summary.get('raw_policy_count', 0)}",
        f"- 纳入主线政策数：{policy_provenance_summary.get('included_policy_count', 0)}",
        f"- 排除政策数：{policy_provenance_summary.get('excluded_policy_count', 0)}",
        f"- verified/degraded/rejected：{policy_provenance_summary.get('verified_count', 0)}/{policy_provenance_summary.get('degraded_count', 0)}/{policy_provenance_summary.get('rejected_count', 0)}",
        f"- 官方域名命中数：{policy_provenance_summary.get('official_domain_match_count', 0)}；来源机构-域名严格匹配数：{policy_provenance_summary.get('source_org_domain_match_count', 0)}",
        f"- 必填字段缺失政策数：{policy_provenance_summary.get('missing_required_field_count', 0)}；日期不可解析政策数：{policy_provenance_summary.get('unparseable_date_count', 0)}",
    ]
    excluded_policies = policy_provenance_summary.get("excluded_policies") or []
    if excluded_policies:
        lines += ["", "排除样本："]
        for item in excluded_policies[:5]:
            lines.append(
                f"- {item.get('policy_id', '')}：{item.get('exclusion_reason', '')}；domain={item.get('source_domain', '')}"
            )
    lines += [
        "",
        "## 政策快照完整性摘要",
        "",
        f"- 快照版本：{policy_snapshot_summary.get('scoring_version', POLICY_SNAPSHOT_VERSION)}",
        f"- 状态：{policy_snapshot_summary.get('status', 'not_run')}",
        f"- 原始政策数：{policy_snapshot_summary.get('raw_policy_count', 0)}",
        f"- 新增政策数：{policy_snapshot_summary.get('new_policy_count', 0)}",
        f"- 未变化政策数：{policy_snapshot_summary.get('unchanged_policy_count', 0)}",
        f"- 内容变更政策数：{policy_snapshot_summary.get('changed_policy_count', 0)}",
        f"- 有说明变更数：{policy_snapshot_summary.get('changed_with_revision_note_count', 0)}",
        f"- 无说明变更数：{policy_snapshot_summary.get('changed_without_revision_note_count', 0)}",
        f"- 重复 policy_id 冲突数：{policy_snapshot_summary.get('duplicate_policy_id_conflict_count', 0)}",
        f"- 重复 source_url 冲突数：{policy_snapshot_summary.get('duplicate_source_url_conflict_count', 0)}",
        f"- 移除政策数：{policy_snapshot_summary.get('removed_policy_count', 0)}",
        f"- Registry 更新状态：{policy_snapshot_summary.get('registry_update_status', 'pending')}",
    ]
    changed_with_note = [
        item for item in policy_snapshot_summary.get("policies", []) if item.get("snapshot_status") == "changed_with_revision_note"
    ][:10]
    if changed_with_note:
        lines += ["", "有说明变更样本："]
        for item in changed_with_note:
            lines.append(
                f"- {item.get('policy_id', '')}：previous_content_hash={item.get('previous_content_hash', '')}；content_hash={item.get('content_hash', '')}；revision_id={item.get('revision_id', '')}；revision_note={item.get('revision_note', '')}"
            )
    lines += [
        "",
        "## 报告合约校验",
        "",
        f"- 校验版本：{contract_summary.get('scoring_version', 'mainline_contract_validator_v2')}",
        f"- 校验状态：{contract_summary.get('status', 'not_run')}",
        f"- Error 数：{contract_summary.get('error_count', 0)}",
        f"- Warning 数：{contract_summary.get('warning_count', 0)}",
        f"- 校验时间：{contract_summary.get('checked_at', '')}",
        f"- 已检查模块：{', '.join(key for key, value in (contract_summary.get('checked_sections') or {}).items() if value) or '无'}",
        "",
        "## 主线分层",
        "",
        "| 主题 | mainline_score_v6 | 生命周期 | theme_score_v5 | 30日分数 | 90日分数 | 事件数 | 来源机构数 | 主要支撑事件 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in mainline_ranking:
        top_events = "；".join(item.get("top_event_ids", [])[:2]) or "无"
        lines.append(
            f"| {item.get('theme_name', '')} | {item.get('mainline_score_v6', 0):.4f} | {item.get('lifecycle_state', '')} | {item.get('theme_score_v5', 0):.4f} | {item.get('score_30d', 0):.4f} | {item.get('score_90d', 0):.4f} | {item.get('matched_allocated_event_count', 0)} | {item.get('source_org_count_90d', 0)} | {top_events} |"
        )

    contract_warnings = [issue for issue in contract_summary.get("issues", []) if issue.get("severity") == "warning"][:10]
    if contract_warnings:
        lines += ["", "### 合约校验Warning样本"]
        for issue in contract_warnings:
            lines.append(f"- {issue.get('code', '')}：{issue.get('path', '')}；{issue.get('message', '')}")

    lines += [
        "",
        "## 打分口径",
        "",
        "- 行业/主题强度：1日分位25% + 5日分位35% + 20日分位25% + 热度分位15%。申万热度为当日成交额相对近20日均值；同花顺热度为换手率。",
        "- ETF强度：1日分位20% + 5日分位35% + 20日分位30% + 成交额分位15%。",
        "- 旧市场证据分：申万映射25% + 同花顺主题30% + ETF代理25% + 涨停结构10% + 大单/特大单资金排名10%，仅作为市场背景观察。",
        f"- 政策分：读取 `data/policy_signals.json`，按政策评分V2计算：权威级别35% + 行动性25% + 经济覆盖面20% + 时间衰减20%；`theme_relevance_v2` 计算政策-主题相关度，`policy_event_clustering_v2` 做事件去重，`policy_theme_stance_v2` 对监管/约束政策做方向性折扣，`event_theme_allocation_v2` 对同一政策事件的多主题贡献做预算分配，`mainline_lifecycle_v2` 识别主线生命周期。",
        "- 默认主线分：mainline_score_v6 = theme_score_v5 × lifecycle_quality_multiplier。",
        f"- 旧证据分兼容口径：旧市场证据分{(1 - policy_summary.get('policy_weight', POLICY_WEIGHT)) * 100:.0f}% + 政策分{policy_summary.get('policy_weight', POLICY_WEIGHT) * 100:.0f}%，不参与默认主线排序。",
        "- 旧阶段：85分以上为主线确认，72-85为次主线/强修复，50-72为观察线，50以下为弱势/退潮，仅用于旧市场证据观察。",
        f"- 政策库更新时间：{policy_summary.get('updated_at') or '无'}；原始政策数：{policy_summary.get('raw_policy_count', policy_summary.get('signals_count', 0))}；纳入主线政策数：{policy_summary.get('included_policy_count', policy_summary.get('signals_count', 0))}；排除政策数：{policy_summary.get('excluded_policy_count', 0)}；政策-主题相关度阈值：{policy_summary.get('min_relevance_threshold', 0.25)}；去重后事件数：{event_cluster_summary.get('cluster_count', 0)}。",
        "",
        "## 市场土壤",
        "",
        "| 指数 | 1日 | 5日 | 20日 | 收盘 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in payload["broad_indexes"]:
        lines.append(f"| {item['name']} | {fmt_pct(item['r1'])} | {fmt_pct(item['r5'])} | {fmt_pct(item['r20'])} | {item['close']:.2f} |")
    b = payload["breadth"]
    lines += [
        "",
        f"- 全市场上涨比例：{b['up_ratio']:.2f}%",
        f"- 全市场日涨跌幅中位数：{b['median_pct_chg']:.2f}%",
        f"- 5日正收益股票比例：{b['r5_positive_ratio']:.2f}%",
        f"- 20日正收益股票比例：{b['r20_positive_ratio']:.2f}%",
        f"- 单日涨幅不低于5%的股票数：{b['gt_5_count']}；跌幅不低于5%的股票数：{b['lt_minus_5_count']}",
        "",
        "## 申万一级行业强弱",
        "",
        "| 行业 | 强度分 | 1日 | 5日 | 20日 | 成交热度 | PE | PB |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in payload["sw_top"][:12]:
        lines.append(
            f"| {item['name']} | {item['score']:.2f} | {fmt_pct(item['r1'])} | {fmt_pct(item['r5'])} | {fmt_pct(item['r20'])} | {item['amount_ratio']:.2f} | {item.get('pe') or ''} | {item.get('pb') or ''} |"
        )

    lines += [
        "",
        "## 同花顺主题/概念强度",
        "",
        "| 主题/概念 | 强度分 | 1日 | 5日 | 20日 | 换手率 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in payload["ths_top"][:20]:
        lines.append(
            f"| {item['name']} | {item['score']:.2f} | {fmt_pct(item['r1'])} | {fmt_pct(item['r5'])} | {fmt_pct(item['r20'])} | {fmt_pct(item['turnover_rate'])} |"
        )

    lines += [
        "",
        "## ETF代理验证",
        "",
        "| 代码 | ETF | 强度分 | 1日 | 5日 | 20日 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in payload["etf_top"][:20]:
        lines.append(
            f"| {item['ts_code']} | {item['name']} | {item['score']:.2f} | {fmt_pct(item['r1'])} | {fmt_pct(item['r5'])} | {fmt_pct(item['r20'])} |"
        )

    lines += [
        "",
        "## 政策信号",
        "",
        "| 主题 | mainline_score_v6 | theme_score_v5 | 分配后事件数 | 主要支撑事件 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in mainline_ranking:
        top_events = "；".join(item.get("top_event_ids", [])[:3]) or "无"
        lines.append(
            f"| {item.get('theme_name', '')} | {item.get('mainline_score_v6', 0):.4f} | {item.get('theme_score_v5', 0):.4f} | {item.get('matched_allocated_event_count', 0)} | {top_events} |"
        )

    lines += [
        "",
        "## 政策事件去重摘要",
        "",
        f"- 版本：{event_cluster_summary.get('scoring_version', 'policy_event_clustering_v2')}",
        f"- 原始政策数：{event_cluster_summary.get('raw_policy_count', 0)}",
        f"- 事件聚类数：{event_cluster_summary.get('cluster_count', 0)}",
        f"- 去重政策数：{event_cluster_summary.get('deduplicated_policy_count', 0)}",
        f"- 去重比例：{event_cluster_summary.get('deduplication_ratio', 0) * 100:.1f}%",
        f"- 聚类窗口：{event_cluster_summary.get('cluster_date_window_days', 7)}天",
        f"- 标题相似度阈值：{event_cluster_summary.get('title_similarity_threshold', 0.65)}",
        f"- 关键词重叠阈值：{event_cluster_summary.get('keyword_overlap_threshold', 0.45)}",
        "",
        "## 政策方向性摘要",
        "",
        f"- 方向性评分版本：{policy_stance_summary.get('scoring_version', 'policy_theme_stance_v2')}",
        f"- 默认 stance profile：{policy_stance_summary.get('default_stance_profile', 'growth_support')}",
        f"- policy-theme pair 数：{policy_stance_summary.get('policy_theme_pair_count', 0)}",
        f"- cluster-theme pair 数：{policy_stance_summary.get('cluster_theme_pair_count', 0)}",
        f"- 扶持事件数：{policy_stance_summary.get('supportive_count', 0)}",
        f"- 温和扶持事件数：{policy_stance_summary.get('mildly_supportive_count', 0)}",
        f"- 中性/混合事件数：{policy_stance_summary.get('neutral_or_mixed_count', 0)}",
        f"- 温和约束事件数：{policy_stance_summary.get('mildly_restrictive_count', 0)}",
        f"- 明确约束事件数：{policy_stance_summary.get('restrictive_count', 0)}",
        "",
        "## 事件-主题贡献分配摘要",
        "",
        f"- 分配版本：{event_theme_allocation_summary.get('scoring_version', 'event_theme_allocation_v2')}",
        f"- 分配方法：{event_theme_allocation_summary.get('allocation_method', 'proportional_budget_cap')}",
        f"- 事件数：{event_theme_allocation_summary.get('event_cluster_count', 0)}",
        f"- 事件-主题 claim 数：{event_theme_allocation_summary.get('event_theme_claim_count', 0)}",
        f"- 多主题事件数：{event_theme_allocation_summary.get('multi_theme_event_count', 0)}",
        f"- 触发预算上限事件数：{event_theme_allocation_summary.get('capped_event_count', 0)}",
        f"- 分配前总贡献：{event_theme_allocation_summary.get('raw_contribution_total_v4', 0):.4f}",
        f"- 分配后总贡献：{event_theme_allocation_summary.get('allocated_contribution_total_v5', 0):.4f}",
        f"- 分配折减影响：{event_theme_allocation_summary.get('allocation_reduction_effect', 0):.4f}",
        f"- 平均每个事件命中主题数：{event_theme_allocation_summary.get('avg_matched_theme_count_per_event', 0):.4f}",
        "",
        "## 主线生命周期摘要",
        "",
        f"- 生命周期版本：{mainline_lifecycle_summary.get('scoring_version', 'mainline_lifecycle_v2')}",
        f"- 基准日期：{mainline_lifecycle_summary.get('as_of_date', basis)}",
        f"- 主题数量：{mainline_lifecycle_summary.get('theme_count', 0)}",
        f"- 升温主线数：{mainline_lifecycle_summary.get('accelerating_count', 0)}",
        f"- 持续主线数：{mainline_lifecycle_summary.get('sustained_count', 0)}",
        f"- 新出现主线数：{mainline_lifecycle_summary.get('emerging_count', 0)}",
        f"- 单事件新出现主线数：{mainline_lifecycle_summary.get('single_event_emerging_count', 0)}",
        f"- 降温主线数：{mainline_lifecycle_summary.get('cooling_count', 0)}",
        f"- 旧政策尾部主线数：{mainline_lifecycle_summary.get('legacy_tail_count', 0)}",
        f"- 缺日期未知主线数：{mainline_lifecycle_summary.get('undated_unknown_count', 0)}",
        f"- 休眠主线数：{mainline_lifecycle_summary.get('dormant_count', 0)}",
        "",
        "## 政策-主题事件贡献V6",
        "",
        f"- 版本：{theme_summary.get('scoring_version', 'mainline_score_v6_lifecycle_adjusted')}",
        f"- 基础相关度版本：{theme_summary.get('base_relevance_version', 'theme_relevance_v2')}",
        f"- 事件去重版本：{theme_summary.get('event_clustering_version', 'policy_event_clustering_v2')}",
        f"- 政策方向性版本：{theme_summary.get('policy_stance_version', 'policy_theme_stance_v2')}",
        f"- 事件-主题分配版本：{theme_summary.get('event_theme_allocation_version', 'event_theme_allocation_v2')}",
        f"- 主线生命周期版本：{theme_summary.get('mainline_lifecycle_version', 'mainline_lifecycle_v2')}",
        f"- 最低匹配阈值：{theme_summary.get('min_relevance_threshold', 0.25)}",
        "",
        "| 主线 | mainline_score_v6 | theme_score_v5 | 生命周期 | 生命周期乘数 | 30日分数 | 90日分数 | V4对照 | V3对照 | V2对照 | 主要支撑事件 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for theme in theme_summary.get("themes", []):
        top_titles = "；".join(item.get("event_cluster_id", "") for item in theme.get("top_event_contributors", [])[:2]) or "无"
        lines.append(
            f"| {theme.get('theme_name', '')} | {theme.get('mainline_score_v6', 0):.4f} | {theme.get('theme_score_v5', 0):.4f} | {theme.get('lifecycle_state', '')} | {theme.get('lifecycle_quality_multiplier', 0):.4f} | {theme.get('score_30d', 0):.4f} | {theme.get('score_90d', 0):.4f} | {theme.get('theme_score_v4_stance_adjusted', theme.get('theme_score_v4', 0)):.4f} | {theme.get('theme_score_v3_dedup', theme.get('theme_score_v3', 0)):.4f} | {theme.get('theme_score_v2_raw', 0):.4f} | {top_titles} |"
        )

    for theme in theme_summary.get("themes", []):
        lines += [
            "",
            f"### {theme.get('theme_name', '')}",
            f"- mainline_score_v6：{theme.get('mainline_score_v6', 0):.4f}",
            f"- theme_score_v5：{theme.get('theme_score_v5', 0):.4f}",
            f"- theme_score_v4_stance_adjusted：{theme.get('theme_score_v4_stance_adjusted', theme.get('theme_score_v4', 0)):.4f}",
            f"- theme_score_v3_dedup：{theme.get('theme_score_v3_dedup', theme.get('theme_score_v3', 0)):.4f}",
            f"- theme_score_v2_raw：{theme.get('theme_score_v2_raw', 0):.4f}",
            f"- 分配折减影响：{theme.get('allocation_adjustment_effect', 0):.4f}",
            f"- 去重影响：{theme.get('deduplication_effect', 0):.4f}",
            f"- 方向性调整影响：{theme.get('stance_adjustment_effect', 0):.4f}",
            f"- 分配后事件数：{theme.get('matched_allocated_event_count', 0)}",
            f"- 主导事件数：{theme.get('primary_event_count', 0)}",
            f"- 共同主线事件数：{theme.get('co_primary_event_count', 0)}",
            f"- 次级事件数：{theme.get('secondary_event_count', 0)}",
            f"- 边缘事件数：{theme.get('peripheral_event_count', 0)}",
            f"- 平均分配占比：{theme.get('avg_allocation_share', 0):.4f}",
            f"- 生命周期状态：{theme.get('lifecycle_state', '')}",
            f"- 生命周期质量乘数：{theme.get('lifecycle_quality_multiplier', 0):.4f}",
            f"- 状态乘数：{theme.get('state_multiplier', 0):.4f}",
            f"- 广度得分：{theme.get('breadth_score', 0):.4f}",
            f"- 30日分数：{theme.get('score_30d', 0):.4f}",
            f"- 31-60日分数：{theme.get('score_31_60d', 0):.4f}",
            f"- 61-90日分数：{theme.get('score_61_90d', 0):.4f}",
            f"- 90日分数：{theme.get('score_90d', 0):.4f}",
            f"- 30日事件数：{theme.get('event_count_30d', 0)}",
            f"- 90日事件数：{theme.get('event_count_90d', 0)}",
            f"- 90日来源机构数：{theme.get('source_org_count_90d', 0)}",
            f"- 活跃窗口数：{theme.get('active_window_count', 0)}",
            f"- 持续性得分：{theme.get('persistence_score', 0):.4f}",
            f"- 30日加速度：{theme.get('acceleration_delta_30d', 0):.4f}",
            f"- 30日加速度比例：{theme.get('acceleration_ratio_30d', 0):.4f}",
            f"- 生命周期原因：{', '.join(theme.get('lifecycle_reasons', [])) or '无'}",
            f"- 匹配事件数：{theme.get('matched_event_cluster_count', 0)}",
            f"- 原始匹配政策数：{theme.get('matched_policy_count_raw', 0)}",
            f"- 平均事件相关度：{theme.get('avg_cluster_relevance_score_v2', 0):.4f}",
            f"- 平均事件政策强度：{theme.get('avg_cluster_policy_score_v2', 0):.4f}",
            f"- 平均事件方向性：{theme.get('avg_cluster_stance_score_v2', 0):.4f}",
            f"- 扶持事件数：{theme.get('supportive_cluster_count', 0)}",
            f"- 约束事件数：{theme.get('mildly_restrictive_cluster_count', 0) + theme.get('restrictive_cluster_count', 0)}",
        ]
        contributors = theme.get("top_event_contributors", [])[:3]
        if contributors:
            lines.append("- 主要支撑事件：")
            for index, contributor in enumerate(contributors, start=1):
                lines.append(
                    f"  {index}. {contributor.get('event_cluster_id', '')}；event_activity_date={contributor.get('event_activity_date', '')}；age_days={contributor.get('age_days')}；age_bucket={contributor.get('age_bucket', '')}；allocation_role={contributor.get('allocation_role', '')}；allocation_share={contributor.get('allocation_share', 0):.4f}；primary_policy_id={contributor.get('primary_policy_id', '')}；cluster_policy_score_v2={contributor.get('cluster_policy_score_v2', 0):.4f}；cluster_relevance_score_v2={contributor.get('cluster_relevance_score_v2', 0):.4f}；cluster_stance_label={contributor.get('cluster_stance_label', '')}；分配后贡献={contributor.get('allocated_cluster_contribution', 0):.4f}；分配折减={contributor.get('theme_allocation_reduction_effect', 0):.4f}；命中证据：{matched_keywords(contributor.get('top_matched_evidence', []))}；方向性证据：{matched_stance_keywords(contributor.get('top_stance_evidence', []))}"
                )
        else:
            lines.append("- 主要支撑事件：无")

    lines += [
        "",
        "## 涨停结构",
        "",
        "| 行业 | 涨停数 | 平均换手 |",
        "| --- | --- | --- |",
    ]
    for item in payload["limit_up_top"][:20]:
        lines.append(f"| {item['industry']} | {item['limit_count']} | {fmt_pct(item['avg_turnover'])} |")

    lines += [
        "",
        "## 大单/特大单资金辅助观察",
        "",
        "说明：这里按 Tushare `moneyflow` 与 `stock_basic.industry` 聚合，只作为结构验证，不作为资金预测。",
        "",
        "| 行业 | 大单+特大单净额 | 总净额 | 股票数 |",
        "| --- | --- | --- | --- |",
    ]
    for item in payload["moneyflow_top"][:20]:
        lines.append(f"| {item['industry']} | {item['large_net']:.2f} | {item['net']:.2f} | {item['count']} |")

    lines += ["", "## 旧市场证据观察（非默认主线排序）", ""]
    lines += [
        "说明：本节保留旧 evidence_score / market_score 口径用于市场背景观察，不参与默认主线排序。",
        "",
    ]
    for item in legacy_theme_ranking:
        lines += [
            f"### {item.get('theme', '')}：{item.get('stage', '')}",
            f"- 证据分：{fmt_number(item.get('evidence_score'))}，证据项：{item.get('evidence_count') or 0}",
            f"- 市场分：{fmt_number(item.get('market_score'))}；政策分：{fmt_number(item.get('policy_score'))}；政策证据：{item.get('policy_evidence_count') or 0}",
            f"- 申万映射：{item.get('top_sw') or '无'}",
            f"- 主题指数：{item.get('top_ths') or '无'}",
            f"- ETF代理：{item.get('top_etf') or '无'}",
            f"- 政策映射：{item.get('top_policy') or '无'}",
        ]
        if item.get("stage") == "主线确认":
            lines.append("- 市场观察：价格、主题、ETF和结构资金同步度较高，但本节不是默认主线排序。")
        elif item.get("stage") == "次主线/强修复":
            lines.append("- 市场观察：有较强修复或轮动迹象，但本节不是默认主线排序。")
        elif item.get("stage") == "观察线":
            lines.append("- 市场观察：存在局部强度，但证据链尚未完整闭环。")
        else:
            lines.append("- 市场观察：当前旧证据分偏弱。")
        lines.append("")

    lines += [
        "## 数据源与可复核性",
        "",
        "- 本地：根目录 `数据源.md`、`.env`。",
        "- 政策库：`data/policy_signals.json`，由 Codex/LLM 从官方政策源抽取事实结构，Python 规则负责确定性打分；LLM 不参与政策质量评分。",
        "- Tushare：`trade_cal`、`daily`、`daily_basic`、`index_daily`、`index_classify`、`sw_daily`、`ths_index`、`ths_daily`、`fund_basic`、`fund_daily`、`limit_list_d`、`moneyflow`。",
        "- BaoStock：验证上证综指、创业板指、科创50在基准日的收盘和涨跌幅。",
        "",
        "## BaoStock交叉验证",
        "",
    ]
    for item in payload["baostock_check"]:
        lines.append(f"- {item}")
    lines += [
        "",
        "## 下一次复核建议",
        "",
        "- 每日盘后只更新强弱、涨停、资金、ETF代理，不改长期产业逻辑。",
        "- 周频复核产业证据与主线映射，避免只因单日涨跌改变长期归类。",
        "- 若全市场20日正收益比例升至四成以上，再考虑把结构性主线升级为市场级主升。",
        "",
    ]
    return "\n".join(lines)


def build_report(today: str) -> tuple[str, dict[str, Any], str]:
    pro = make_client()
    nominal_today = today
    open_days = get_trade_dates(pro, nominal_today)
    basis_raw, completeness = choose_basis_date(pro, open_days)
    idx = open_days.index(basis_raw)
    if idx < 20:
        raise RuntimeError("Not enough historical trading days for 20-day scoring.")
    window_dates = open_days[idx - 20 : idx + 1]
    d5 = open_days[idx - 5]
    d20 = open_days[idx - 20]
    basis_date = f"{basis_raw[:4]}-{basis_raw[4:6]}-{basis_raw[6:]}"
    generated_dt = datetime.now(TZ)
    generated_iso = generated_dt.isoformat(timespec="seconds")
    now = generated_dt.strftime("%Y-%m-%d %H:%M:%S CST")
    report_id = f"mainline_review_{generated_dt.strftime('%Y-%m-%d_%H%M%S')}"
    stage_statuses: list[dict[str, Any]] = []
    policy_store = load_policy_store()
    raw_policies = policy_store.get("signals", [])
    stage_statuses.append(build_stage_status("policy_store", "pass", True, len(raw_policies)))
    try:
        policy_provenance_summary = build_policy_provenance_summary(raw_policies)
        stage_statuses.append(
            build_stage_status(
                "policy_provenance",
                "pass",
                True,
                int(policy_provenance_summary.get("included_policy_count") or 0),
            )
        )
    except Exception as exc:
        stage_statuses.append(build_stage_status("policy_provenance", "fail", True, 0, [], str(exc)))
        data_quality_summary = build_data_quality_summary(stage_statuses)
        assert_required_data_quality(data_quality_summary)
        raise
    try:
        previous_snapshot_registry = load_snapshot_registry()
        policy_snapshot_summary = build_policy_snapshot_summary(
            raw_policies=raw_policies,
            provenance_summary=policy_provenance_summary,
            previous_registry=previous_snapshot_registry,
            report_id=report_id,
            generated_at=generated_iso,
        )
        assert_policy_snapshot_integrity(policy_snapshot_summary)
        policy_snapshot_summary["registry_update_version"] = SNAPSHOT_REGISTRY_FINALIZATION_VERSION
        policy_snapshot_summary["registry_update_receipt_hash"] = ""
        stage_statuses.append(
            build_stage_status(
                "policy_snapshot_integrity",
                "pass",
                True,
                int(policy_snapshot_summary.get("snapshot_policy_count") or 0),
            )
        )
    except Exception as exc:
        stage_statuses.append(build_stage_status("policy_snapshot_integrity", "fail", True, 0, [], str(exc)))
        data_quality_summary = build_data_quality_summary(stage_statuses)
        assert_required_data_quality(data_quality_summary)
        raise
    event_cluster_summary = policy_event_summary(basis_date, [spec.name for spec in THEMES])
    theme_summary = policy_theme_summary(basis_date, [spec.name for spec in THEMES])
    stage_statuses.append(build_stage_status("policy_theme_summary", "pass", True, len(theme_summary.get("themes", []))))
    stance_summary = theme_summary.get("policy_stance_summary", {})
    event_theme_allocation_summary = theme_summary.get("event_theme_allocation_summary", {})
    mainline_lifecycle_summary = theme_summary.get("mainline_lifecycle_summary", {})
    policy_by_theme = score_policy_by_theme(basis_date, [spec.name for spec in THEMES])

    breadth, breadth_status = run_optional_stage("breadth", lambda: stock_breadth(pro, basis_raw, d5, d20), default_breadth())
    broad, broad_status = run_optional_stage("broad_indexes", lambda: broad_index_data(pro, basis_raw, d5, d20), [])
    sw, sw_status = run_optional_stage(
        "sw_score",
        lambda: score_sw(pro, window_dates),
        empty_stage_frame("sw_score"),
        required_columns=DATA_QUALITY_SCHEMAS.get("sw_score", []),
        defaults=DATA_QUALITY_DEFAULTS,
    )
    ths, ths_status = run_optional_stage(
        "ths_score",
        lambda: score_ths(pro, window_dates),
        empty_stage_frame("ths_score"),
        required_columns=DATA_QUALITY_SCHEMAS.get("ths_score", []),
        defaults=DATA_QUALITY_DEFAULTS,
    )
    etf, etf_status = run_optional_stage(
        "etf_score",
        lambda: score_etf(pro, window_dates),
        empty_stage_frame("etf_score"),
        required_columns=DATA_QUALITY_SCHEMAS.get("etf_score", []),
        defaults=DATA_QUALITY_DEFAULTS,
    )
    (limit_up, limit_top), limit_status = run_optional_stage(
        "limit_up",
        lambda: limit_up_data(pro, basis_raw),
        empty_limit_up_result(),
        required_columns=DATA_QUALITY_SCHEMAS.get("limit_up", []),
        defaults=DATA_QUALITY_DEFAULTS,
    )
    (moneyflow, moneyflow_top), moneyflow_status = run_optional_stage(
        "moneyflow",
        lambda: moneyflow_data(pro, basis_raw),
        empty_moneyflow_result(),
        required_columns=DATA_QUALITY_SCHEMAS.get("moneyflow", []),
        defaults=DATA_QUALITY_DEFAULTS,
    )
    baostock_check_result, baostock_status = run_optional_stage("baostock_check", lambda: baostock_check(basis_raw), [])
    stage_statuses.extend(
        [
            breadth_status,
            broad_status,
            sw_status,
            ths_status,
            etf_status,
            limit_status,
            moneyflow_status,
            baostock_status,
        ]
    )
    ranking = theme_rows(sw, ths, etf, limit_up, moneyflow, policy_by_theme)
    mainline_ranking = build_mainline_ranking(theme_summary)
    canonical_mainline_summary = build_canonical_mainline_summary(theme_summary)
    stage_statuses.append(build_stage_status("canonical_mainline", "pass", True, len(mainline_ranking)))
    legacy_theme_ranking = build_legacy_theme_ranking(ranking)
    data_quality_summary = build_data_quality_summary(stage_statuses)
    assert_required_data_quality(data_quality_summary)

    data_sources = (ROOT / "数据源.md").read_text(encoding="utf-8") if (ROOT / "数据源.md").exists() else ""

    payload = {
        "report_id": report_id,
        "generated_at": now,
        "generated_at_iso": generated_iso,
        "basis_date": basis_date,
        "nominal_today": nominal_today,
        "data_sources_root": data_sources,
        "completeness": completeness,
        "breadth": breadth,
        "broad_indexes": broad,
        "policy_summary": {
            "updated_at": policy_store.get("updated_at", ""),
            "signals_count": int(policy_provenance_summary.get("included_policy_count") or 0),
            "raw_policy_count": int(policy_provenance_summary.get("raw_policy_count") or 0),
            "included_policy_count": int(policy_provenance_summary.get("included_policy_count") or 0),
            "excluded_policy_count": int(policy_provenance_summary.get("excluded_policy_count") or 0),
            "policy_provenance_version": POLICY_PROVENANCE_VERSION,
            "policy_snapshot_version": POLICY_SNAPSHOT_VERSION,
            "policy_weight": POLICY_WEIGHT,
            "scoring_version": "policy_score_v2",
            "theme_relevance_version": "theme_relevance_v2",
            "event_clustering_version": "policy_event_clustering_v2",
            "policy_stance_version": "policy_theme_stance_v2",
            "event_theme_allocation_version": "event_theme_allocation_v2",
            "mainline_lifecycle_version": "mainline_lifecycle_v2",
            "min_relevance_threshold": theme_summary.get("min_relevance_threshold", 0.25),
            "scoring": "authority_score 35%, actionability_score 25%, economic_scope_score 20%, time_decay_score 20%; theme_relevance_v2 maps signals; policy_event_clustering_v2 deduplicates events; policy_theme_stance_v2 applies non-boosting direction multipliers; event_theme_allocation_v2 caps repeated event-theme contribution.",
        },
        "policy_provenance_summary": policy_provenance_summary,
        "policy_snapshot_summary": policy_snapshot_summary,
        "snapshot_registry_update_summary": {
            "scoring_version": SNAPSHOT_REGISTRY_FINALIZATION_VERSION,
            "status": "pending",
            "registry_path": "data/policy_snapshot_registry.json",
            "report_id": report_id,
            "updated_at": "",
            "previous_registry_hash": "",
            "updated_registry_hash": "",
            "registry_policy_count_before": len(previous_snapshot_registry.get("policy_snapshots") or []),
            "registry_policy_count_after": 0,
            "new_policy_count": int(policy_snapshot_summary.get("new_policy_count") or 0),
            "unchanged_policy_count": int(policy_snapshot_summary.get("unchanged_policy_count") or 0),
            "changed_with_revision_note_count": int(policy_snapshot_summary.get("changed_with_revision_note_count") or 0),
            "removed_policy_count": int(policy_snapshot_summary.get("removed_policy_count") or 0),
            "json_artifact_path": f"research/mainline/{report_id}.json",
            "markdown_artifact_path": f"research/mainline/{report_id}.md",
            "write_steps": ["build_payload"],
            "error": "",
        },
        "event_cluster_summary": event_cluster_summary,
        "policy_stance_summary": stance_summary,
        "event_theme_allocation_summary": event_theme_allocation_summary,
        "mainline_lifecycle_summary": mainline_lifecycle_summary,
        "data_quality_summary": data_quality_summary,
        "canonical_mainline_summary": canonical_mainline_summary,
        "mainline_ranking": mainline_ranking,
        "theme_summary": theme_summary,
        "theme_ranking": ranking,
        "legacy_theme_ranking": legacy_theme_ranking,
        "sw_top": clean_records_safe(
            sw,
            20,
            SW_TOP_FIELDS,
            DATA_QUALITY_DEFAULTS,
        ),
        "ths_top": clean_records_safe(
            ths,
            30,
            THS_TOP_FIELDS,
            DATA_QUALITY_DEFAULTS,
        ),
        "etf_top": clean_records_safe(
            etf,
            30,
            ETF_TOP_FIELDS,
            DATA_QUALITY_DEFAULTS,
        ),
        "limit_up_top": limit_top,
        "moneyflow_top": moneyflow_top,
        "baostock_check": baostock_check_result,
        "source_links": {
            "tushare_permissions": "https://tushare.pro/document/1?doc_id=108",
            "ndrc_intelligent_economy": "https://www.ndrc.gov.cn/",
            "nea_ai_energy": "https://www.nea.gov.cn/",
            "csrc_gem_reform": "https://www.csrc.gov.cn/",
        },
    }
    contract_errors = assert_canonical_mainline_contract(payload)
    if contract_errors:
        raise RuntimeError(f"Canonical mainline contract failed: {', '.join(contract_errors)}")
    attach_contract_validation(payload, allow_pending_registry=True)
    return report_id, payload, render_markdown(payload)


def _registry_path_for_write() -> Path:
    return ROOT / "data" / "policy_snapshot_registry.json"


def updated_snapshot_registry_for_payload(report_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    contract = payload.get("contract_validation_summary") or {}
    snapshot_summary = payload.get("policy_snapshot_summary") or {}
    if contract.get("status") != "pass":
        raise RuntimeError("Contract validation must pass before registry finalization.")
    if snapshot_summary.get("status") not in {"pass", "degraded"}:
        raise RuntimeError("Policy snapshot summary must pass before registry finalization.")
    previous_registry = load_snapshot_registry()
    return build_updated_snapshot_registry(
        previous_registry,
        snapshot_summary,
        report_id,
        payload.get("generated_at_iso") or payload.get("generated_at", ""),
    )


def write_report_artifacts(report_id: str, payload: dict[str, Any], markdown: str) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / f"{report_id}.json"
    md_path = REPORT_DIR / f"{report_id}.md"
    updated_registry = updated_snapshot_registry_for_payload(report_id, payload)
    finalize_report_artifacts_with_registry(
        payload,
        markdown,
        json_path,
        md_path,
        _registry_path_for_write(),
        updated_registry,
    )
    return json_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate latest A-share mainline research report.")
    parser.add_argument("--today", default=datetime.now(TZ).strftime("%Y-%m-%d"), help="Nominal today in YYYY-MM-DD.")
    parser.add_argument("--write", action="store_true", help="Write report JSON and Markdown into research/mainline.")
    args = parser.parse_args()

    report_id, payload, markdown = build_report(args.today)
    if args.write:
        json_path, md_path = write_report_artifacts(report_id, payload, markdown)
        print(json_path)
        print(md_path)
    else:
        print(json.dumps({"report_id": report_id, "basis_date": payload["basis_date"], "top": payload["mainline_ranking"][:3]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
