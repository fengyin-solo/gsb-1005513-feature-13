"""运行月报计算口径：发电量、等效利用小时、综合效率PR、设备可利用率的唯一出处。

全项目只有这一份公式：service 重算、存量回填、接口展示都调用 ``compute_monthly``，
避免「这个月填完、下个月口径一改对不上」。口径调整通过新增参数版本实现，
已归档月报保留归档时的版本，未归档月报按最新版本重算。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from app.store import store

# ---- 口径参数（随版本变化，所有公式只读这一份） --------------------------------

# 发电量取数来源：meter=关口表计（本月示数-上月示数），inverter=逆变器日均发电量汇总
GENERATION_SOURCE_METER = "meter"
GENERATION_SOURCE_INVERTER = "inverter"
GENERATION_SOURCES = [GENERATION_SOURCE_METER, GENERATION_SOURCE_INVERTER]

# PR 理论发电口径：hours=等效峰值日照小时数，irradiance=环境监测站实测辐照度折算
PR_BASIS_HOURS = "peak_sun_hours"
PR_BASIS_IRRADIANCE = "irradiance"
PR_BASES = [PR_BASIS_HOURS, PR_BASIS_IRRADIANCE]

# 设备可利用率 = 1 - 故障停机小时 / 统计周期小时；停机小时的统计范围按开关拼口径
DOWNTIME_SOURCE_ALARM = "alarm"        # 未闭环告警（未确认/处理中）
DOWNTIME_SOURCE_DEFECT = "defect"      # 未闭环缺陷（待分派/处理中）
DOWNTIME_SOURCES = [DOWNTIME_SOURCE_ALARM, DOWNTIME_SOURCE_DEFECT]


@dataclass(frozen=True)
class CaliberParams:
    """某个口径版本的全部可调参数；新增版本只改参数，不改公式。"""

    generation_source: str = GENERATION_SOURCE_METER
    peak_sun_hours: float = 140.0
    pr_basis: str = PR_BASIS_HOURS
    downtime_sources: tuple[str, ...] = (DOWNTIME_SOURCE_ALARM, DOWNTIME_SOURCE_DEFECT)
    alarm_downtime_hours: float = 8.0       # 每条未闭环告警折算停机小时
    defect_downtime_hours: float = 4.0      # 每条未闭环缺陷折算停机小时
    defect_pending_statuses: tuple[str, ...] = ("待分派", "处理中")
    alarm_pending_statuses: tuple[str, ...] = ("未确认", "处理中")

    def to_dict(self) -> dict[str, Any]:
        return {
            "generation_source": self.generation_source,
            "peak_sun_hours": self.peak_sun_hours,
            "pr_basis": self.pr_basis,
            "downtime_sources": list(self.downtime_sources),
            "alarm_downtime_hours": self.alarm_downtime_hours,
            "defect_downtime_hours": self.defect_downtime_hours,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CaliberParams":
        sources = tuple(raw.get("downtime_sources") or [DOWNTIME_SOURCE_ALARM, DOWNTIME_SOURCE_DEFECT])
        return cls(
            generation_source=str(raw.get("generation_source") or GENERATION_SOURCE_METER),
            peak_sun_hours=float(raw.get("peak_sun_hours") or 140.0),
            pr_basis=str(raw.get("pr_basis") or PR_BASIS_HOURS),
            downtime_sources=sources,
            alarm_downtime_hours=float(raw.get("alarm_downtime_hours") or 8.0),
            defect_downtime_hours=float(raw.get("defect_downtime_hours") or 4.0),
        )


# ---- 明细读取：数字字段容错解析（示例数据里非数字占位按 0 处理） ----------------

def _number(value: Any) -> float:
    """只有「整串就是数字」才认，像 '12kWh'、'样例1' 这类一律按缺测 0 处理。"""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _month_matches(value: Any, month: str) -> bool:
    """日期/时间字段是否落在统计月份（YYYY-MM）。没有可识别月份的记录算全厂常驻口径。"""
    return str(value or "").strip().startswith(month)


def _rows_for_month(module: str, month: str, date_fields: Iterable[str]) -> list[dict[str, Any]]:
    """按月份筛明细：日期字段落在该月的纳入；记录没有任何可识别日期时，
    视为全厂常驻数据（如关口表计的当月示数），也纳入该月汇总。"""
    result: list[dict[str, Any]] = []
    for row in store.rows(module):
        matched: bool | None = None  # None = 记录里没找到任何日期
        for field in date_fields:
            raw = str(row.get(field) or "").strip()
            if raw:
                matched = _month_matches(raw, month)
                if matched:
                    break
        if matched is not False:
            result.append(row)
    return result


def _sum_field(rows: Iterable[dict[str, Any]], field: str) -> float:
    return sum(_number(row.get(field)) for row in rows)


# ---- 四项指标（公式只此一份） ---------------------------------------------------

def generation_kwh(month: str, params: CaliberParams) -> tuple[float, dict[str, Any]]:
    """发电量（kWh）：关口表计本月示数-上月示数之和，或逆变器日均发电量之和。"""
    detail: dict[str, Any] = {"source": params.generation_source, "meters": 0, "inverters": 0}
    if params.generation_source == GENERATION_SOURCE_INVERTER:
        rows = _rows_for_month("inverter", month, [])
        detail["inverters"] = len(rows)
        # 逆变器明细登记的是日均发电量，按 30 天折算成月电量
        return round(_sum_field(rows, "日均发电量") * 30.0, 2), detail
    rows = _rows_for_month("meter", month, [])
    detail["meters"] = len(rows)
    total = sum(
        max(_number(row.get("本月示数")) - _number(row.get("上月示数")), 0.0)
        for row in rows
    )
    return round(total, 2), detail


def installed_capacity_kw(month: str) -> float:
    """装机容量（kW）：统计月末已投运的光伏阵列总装机容量之和，
    作为等效利用小时与理论发电量的分母。"""
    total = 0.0
    for row in store.rows("pv_array"):
        commission = str(row.get("投运日期") or "").strip()
        # 没有投运日期的按存量设备计入；有日期的只统计当月及以前投运的
        if not commission or commission[:7] <= month:
            total += _number(row.get("总装机容量"))
    return round(total, 2)


def equivalent_hours(month: str, energy: float, capacity: float) -> float:
    """等效利用小时 = 发电量(kWh) / 装机容量(kW)。"""
    if capacity <= 0:
        return 0.0
    return round(energy / capacity, 2)


def pr_ratio(
    month: str,
    energy: float,
    capacity: float,
    params: CaliberParams,
) -> tuple[float, dict[str, Any]]:
    """综合效率PR = 实际发电量 / 理论应发电量（百分比）。

    理论应发电量两种口径：
    - peak_sun_hours：装机容量 × 等效峰值日照小时数
    - irradiance：装机容量 ×（环境站实测辐照度均值 W/㎡ / 1000 × 当月小时数）
    """
    detail: dict[str, Any] = {"basis": params.pr_basis}
    if params.pr_basis == PR_BASIS_IRRADIANCE:
        rows = _rows_for_month("environment", month, [])
        irradiances = [_number(row.get("辐照度")) for row in rows]
        measured = [value for value in irradiances if value > 0]
        avg_irradiance = sum(measured) / len(measured) if measured else 0.0
        theoretical = capacity * (avg_irradiance / 1000.0) * _period_hours(month)
        detail["avg_irradiance"] = round(avg_irradiance, 2)
    else:
        theoretical = capacity * params.peak_sun_hours
    pr = (energy / theoretical * 100.0) if theoretical > 0 else 0.0
    detail["theoretical_kwh"] = round(theoretical, 2)
    return round(pr, 2), detail


def _period_hours(month: str) -> float:
    """统计周期小时：平年按自然月天数 × 24，无法解析时按 30 天。"""
    year, mon = month[:4], month[5:7]
    try:
        import calendar
        days = calendar.monthrange(int(year), int(mon))[1]
    except (ValueError, IndexError):
        days = 30
    return float(days * 24)


def downtime_hours(month: str, params: CaliberParams) -> tuple[float, dict[str, Any]]:
    """故障停机时间（小时）：按口径开关，从未闭环告警/缺陷折算汇总。"""
    detail: dict[str, Any] = {}
    total = 0.0
    if DOWNTIME_SOURCE_ALARM in params.downtime_sources:
        rows = [
            row for row in _rows_for_month("alarm", month, ["触发时间"])
            if str(row.get("status") or "") in params.alarm_pending_statuses
        ]
        detail["open_alarms"] = len(rows)
        total += len(rows) * params.alarm_downtime_hours
    if DOWNTIME_SOURCE_DEFECT in params.downtime_sources:
        rows = [
            row for row in _rows_for_month("defect", month, ["发现日期"])
            if str(row.get("status") or "") in params.defect_pending_statuses
        ]
        detail["open_defects"] = len(rows)
        total += len(rows) * params.defect_downtime_hours
    return round(total, 2), detail


def availability(month: str, downtime: float) -> float:
    """设备可利用率 = 1 - 故障停机小时 / 统计周期小时（0~100%）。"""
    period = _period_hours(month)
    if period <= 0:
        return 0.0
    return round(max(0.0, 1.0 - downtime / period) * 100.0, 2)


def compute_monthly(month: str, params: CaliberParams) -> dict[str, Any]:
    """按给定口径版本汇总某统计月份的全部月报指标。

    任何接口/回填/重算需要月报数字时都走这里，保证口径只有一份。
    """
    capacity = installed_capacity_kw(month)
    energy, gen_detail = generation_kwh(month, params)
    hours = equivalent_hours(month, energy, capacity)
    pr, pr_detail = pr_ratio(month, energy, capacity, params)
    downtime, down_detail = downtime_hours(month, params)
    usable = availability(month, downtime)
    return {
        "发电量": energy,
        "等效利用小时": hours,
        "综合效率PR": pr,
        "设备可利用率": usable,
        "故障停机时间": downtime,
        "装机容量": capacity,
        "计算依据": {
            "generation": gen_detail,
            "pr": pr_detail,
            "downtime": down_detail,
            "period_hours": _period_hours(month),
        },
    }
