"""运行月报接口：指标由口径模块自动汇总，覆盖重新计算、提交复核、归档月报与归档台账。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.report import ACTIONS, STATUS_ORDER, ReportService

router = APIRouter(prefix="/api/report", tags=["运行月报"])

service = ReportService()

LIST_FIELDS = ["月报编号", "统计月份", "发电量", "等效利用小时", "综合效率PR", "设备可利用率", "故障停机时间", "口径版本", "月报状态"]
STATUSES = STATUS_ORDER


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出运行月报清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "report", "total": total, "items": items}


@router.get("/archive")
def list_archive() -> dict[str, Any]:
    """归档台账：每次归档落一条复核结论，按归档先后倒序返回。"""
    items = service.list_archive()
    return {"module": "report_archive", "total": len(items), "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按月报编号检索"),
    status: str | None = Query(default=None, description="草稿、已复核、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按月报编号与状态过滤运行月报列表；未归档月报读取时按当前口径重算落库。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条运行月报明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"运行月报 {entry_id} 不存在")
    return entry


@router.get("/{entry_id}/archive", response_model=dict)
def archive_detail(entry_id: int) -> dict:
    """归档详情：指标与月报列表读同一份冻结快照，另附台账编号、复核结论与归档时间。"""
    detail = service.archive_detail(entry_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"运行月报 {entry_id} 不存在或尚未归档")
    return detail


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """按统计月份生成运行月报：指标自动汇总，同月重复提交只留最新一版。"""
    entry, missing, message = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少或无法识别：{'、'.join(missing)}")
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条运行月报执行重新计算、提交复核、归档月报；越序或归档后复核会被退回并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
