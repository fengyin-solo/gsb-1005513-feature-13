"""运行月报计算口径：全系统只此一份，发电量、等效利用小时、综合效率PR、设备可利用率都在这算。

当前口径版本 v2026.10：
- 发电量（MWh）：关口计量模块全部表计「正向有功电量」之和（表计为当月抄表快照）；
- 装机容量（MW）：光伏阵列模块「总装机容量」之和；
- 等效利用小时（h）＝ 发电量 ÷ 装机容量；
- 月累计辐照度（kWh/㎡）：环境监测站「辐照度」的站间平均值；
- 综合效率PR（%）＝ 发电量 ÷（月累计辐照度 × 装机容量）× 100；
- 故障停机时间（h）：告警事件模块「触发时间」落在统计月份内的「停机时长」之和；
- 设备可利用率（%）＝（当月自然小时 − 故障停机时间）÷ 当月自然小时 × 100。

结果统一保留 1 位小数。调整口径只改这个文件并升 CALIBER_VERSION：
未归档月报会在读取时按新口径自动重算落库，已归档月报继续沿用归档时的口径快照。
"""
from __future__ import annotations

import calendar
import re
from typing import Any

from app.store import store

CALIBER_VERSION = "v2026.10"

METRIC_FIELDS = ["发电量", "等效利用小时", "综合效率PR", "设备可利用率", "故障停机时间"]

_MONTH_PATTERN = re.compile(r"^(\d{4})\s*[年\-/.]?\s*(\d{1,2})\s*月?")


def normalize_month(raw: Any) -> str | None:
    """把「2026-9」「2026/09」「2026年9月」「2026-09-01」等写法统一规整成 YYYY-MM。"""
    text = str(raw or "").strip()
    if not text:
        return None
    match = _MONTH_PATTERN.match(text)
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return f"{year:04d}-{month:02d}"


def _to_float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _sum_field(module: str, field: str) -> float:
    total = 0.0
    for row in store.rows(module):
        number = _to_float(row.get(field))
        if number is not None:
            total += number
    return total


def _avg_field(module: str, field: str) -> float:
    values = [
        number
        for row in store.rows(module)
        if (number := _to_float(row.get(field))) is not None
    ]
    return sum(values) / len(values) if values else 0.0


def _month_downtime(month: str) -> float:
    total = 0.0
    for row in store.rows("alarm"):
        if normalize_month(row.get("触发时间")) != month:
            continue
        hours = _to_float(row.get("停机时长"))
        if hours is not None:
            total += hours
    return total


def compute_month_metrics(month: str) -> dict[str, Any]:
    """按当前口径从各模块明细汇总一个月的全部月报指标。"""
    year, month_num = (int(part) for part in month.split("-"))
    natural_hours = calendar.monthrange(year, month_num)[1] * 24

    energy = _sum_field("meter", "正向有功电量")
    capacity = _sum_field("pv_array", "总装机容量")
    irradiation = _avg_field("environment", "辐照度")
    downtime = _month_downtime(month)

    equivalent_hours = energy / capacity if capacity > 0 else 0.0
    pr = energy / (irradiation * capacity) * 100 if irradiation > 0 and capacity > 0 else 0.0
    availability = (natural_hours - downtime) / natural_hours * 100 if natural_hours > 0 else 0.0

    return {
        "发电量": round(energy, 1),
        "等效利用小时": round(equivalent_hours, 1),
        "综合效率PR": round(pr, 1),
        "设备可利用率": round(availability, 1),
        "故障停机时间": round(downtime, 1),
        "口径版本": CALIBER_VERSION,
    }
