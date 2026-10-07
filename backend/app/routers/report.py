"""运行月报接口：登记、按口径重算、复核/归档状态流转、口径版本与归档台账。

所有数字来自后端按口径汇总并落库的结果，列表与归档详情读同一份存储。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.report import (
    DEFAULT_REVIEW_CONCLUSION,
    STATUS_ARCHIVED,
    STATUS_ORDER,
    ReportService,
)

router = APIRouter(prefix="/api/report", tags=["运行月报"])

service = ReportService()

LIST_FIELDS = ["月报编号", "统计月份", "发电量", "等效利用小时", "综合效率PR", "设备可利用率", "故障停机时间", "月报状态"]
STATUSES = STATUS_ORDER


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按月报编号或统计月份检索"),
    status: str | None = Query(default=None, description="草稿、已复核、已归档"),
    month: str | None = Query(default=None, description="按 YYYY-MM 统计月份过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """读取月报列表；数字均为最后一次重算落库的值，不随口径变化临时计算。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, month=month, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/calibers")
def list_calibers() -> dict[str, Any]:
    """口径版本列表：当前版本 is_current=true，已归档月报引用历史版本。"""
    return {"items": service.list_calibers(), "current": service.current_caliber()}


@router.post("/calibers", response_model=ActionResult)
def create_caliber(payload: EntryPayload) -> ActionResult:
    """调整口径：生成新版本，未归档月报自动按新口径重算落库；归档月报不动。"""
    try:
        caliber = service.update_caliber(payload.values, payload.remark)
    except ValueError as exc:
        return ActionResult(ok=False, message=str(exc))
    return ActionResult(ok=True, message=f"口径已更新到 v{caliber['version']}，未归档月报已按新口径重算", entry=caliber)


@router.get("/archives")
def list_archives() -> dict[str, Any]:
    """归档台账列表：只含已归档月报的快照。"""
    return {"items": service.list_archives()}


@router.get("/archives/{report_id}")
def get_archive(report_id: int) -> dict[str, Any]:
    """归档详情：台账与月报主表联读，指标与列表读到的完全一致。"""
    archive = service.get_archive(report_id)
    if archive is None:
        raise HTTPException(status_code=404, detail=f"月报 {report_id} 未归档，归档台账中不存在")
    return archive


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条月报明细（含计算依据与复核结论）；不存在给出可读错误。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"运行月报 {entry_id} 不存在")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """按统计月份登记月报并立即重算；同月重复提交覆盖为最新版，已归档月份拒绝。"""
    entry, message = service.create_entry(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=f"{entry['统计月份']} 月报已生成并按当前口径重算", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """执行重算 / 复核（可带 conclusion、reject）/ 归档；状态机外的动作会被拦下。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出运行月报清单：返回全量落库数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "report", "total": total, "items": items}
