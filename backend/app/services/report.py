"""运行月报业务规则：状态流转、口径重算、同月去重与归档台账都收在这里。

指标一律由 app.services.report_caliber 按当月口径从各模块明细汇总，路由层不做业务判断。
所有读写直接落在 store 上，不留缓存副本，刷新或重新进入读到的就是最后一次落库结果。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services import report_caliber
from app.services.report_caliber import METRIC_FIELDS, normalize_month
from app.store import store

MODULE = "report"
ARCHIVE_MODULE = "report_archive"
REQUIRED_FIELDS = ["统计月份"]
STATUS_ORDER = ["草稿", "已复核", "已归档"]
ACTIONS = ["重新计算", "提交复核", "归档月报"]


class ReportService:
    # ---- 内部：口径同步与落库 ----

    def _sync_entry(self, entry: dict[str, Any]) -> None:
        """存量月报按统计月份回填；未归档且口径过期的按当前口径重算落库。归档快照冻结不动。"""
        month = normalize_month(entry.get("统计月份"))
        if month is None:
            return
        if entry.get("统计月份") != month:
            entry["统计月份"] = month
        entry["月报状态"] = entry.get("status", STATUS_ORDER[0])
        if entry.get("status") == STATUS_ORDER[-1]:
            return
        fresh = entry.get("口径版本") == report_caliber.CALIBER_VERSION and all(
            entry.get(field) not in (None, "") for field in METRIC_FIELDS
        )
        if not fresh:
            entry.update(report_caliber.compute_month_metrics(month))

    def _sync_all(self) -> None:
        for entry in store.rows(MODULE):
            self._sync_entry(entry)

    # ---- 查询 ----

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        self._sync_all()
        rows = sorted(store.rows(MODULE), key=lambda row: str(row.get("统计月份", "")), reverse=True)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("月报编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is not None:
            self._sync_entry(entry)
        return entry

    # ---- 生成：同一个月只留最新一版 ----

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str], str]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing, ""
        month = normalize_month(values.get("统计月份"))
        if month is None:
            return None, ["统计月份（格式如 2026-09）"], ""
        rows = store.rows(MODULE)
        same_month = [row for row in rows if normalize_month(row.get("统计月份")) == month]
        if any(row.get("status") == STATUS_ORDER[-1] for row in same_month):
            return None, [f"{month} 月报已归档，同月不允许重复生成"], ""
        for row in same_month:
            rows.remove(row)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry["月报编号"] = str(values.get("月报编号") or "").strip() or f"REPO-{month}"
        entry["统计月份"] = month
        entry["status"] = STATUS_ORDER[0]
        entry["月报状态"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry.update(report_caliber.compute_month_metrics(month))
        rows.append(entry)
        note = f"，已替换同月旧版 {len(same_month)} 份" if same_month else ""
        return entry, [], f"{month} 运行月报已按口径 {report_caliber.CALIBER_VERSION} 生成{note}"

    # ---- 动作：重新计算 / 提交复核 / 归档月报 ----

    def run_action(self, entry_id: int, action: str, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"运行月报 {entry_id} 不存在"
        if action not in ACTIONS:
            return None, f"动作「{action}」不属于运行月报可执行范围"
        status = str(entry.get("status") or STATUS_ORDER[0])
        if action == "重新计算":
            return self._recalculate(entry, status)
        if action == "提交复核":
            return self._submit_review(entry, status, values)
        return self._archive(entry, status, values)

    def _recalculate(self, entry: dict[str, Any], status: str) -> tuple[dict[str, Any] | None, str]:
        if status == STATUS_ORDER[-1]:
            return None, f"月报已归档，沿用归档时口径 {entry.get('口径版本')}，不再重算"
        month = normalize_month(entry.get("统计月份"))
        if month is None:
            return None, "统计月份不规范，无法重算"
        entry["统计月份"] = month
        entry.update(report_caliber.compute_month_metrics(month))
        entry["月报状态"] = status
        return entry, f"已按口径 {report_caliber.CALIBER_VERSION} 重新计算并落库"

    def _submit_review(
        self, entry: dict[str, Any], status: str, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        if status == STATUS_ORDER[-1]:
            return None, "月报已归档，复核请求已退回"
        if status == STATUS_ORDER[1]:
            return None, "月报已是已复核状态，请勿重复提交"
        conclusion = str(values.get("复核结论") or "").strip()
        if conclusion:
            entry["复核结论"] = conclusion
        entry["status"] = STATUS_ORDER[1]
        entry["月报状态"] = STATUS_ORDER[1]
        entry["pending"] = True
        return entry, "月报已提交复核"

    def _archive(
        self, entry: dict[str, Any], status: str, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        if status == STATUS_ORDER[0]:
            return None, "草稿需先提交复核，复核通过后才能归档"
        if status == STATUS_ORDER[-1]:
            return None, "月报已归档，请勿重复归档"
        conclusion = str(values.get("复核结论") or entry.get("复核结论") or "复核通过").strip()
        entry["复核结论"] = conclusion
        entry["status"] = STATUS_ORDER[-1]
        entry["月报状态"] = STATUS_ORDER[-1]
        entry["pending"] = False
        ledger = store.rows(ARCHIVE_MODULE)
        record = {
            "id": max((int(row.get("id", 0)) for row in ledger), default=0) + 1,
            "月报ID": entry["id"],
            "月报编号": entry.get("月报编号"),
            "统计月份": entry.get("统计月份"),
            "复核结论": conclusion,
            "口径版本": entry.get("口径版本"),
            "归档时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        record["台账编号"] = f"ARCH-{record['id']:04d}"
        ledger.append(record)
        return entry, "月报已归档，复核结论已写入归档台账"

    # ---- 归档台账：列表与详情读同一份冻结数据 ----

    def list_archive(self) -> list[dict[str, Any]]:
        return sorted(store.rows(ARCHIVE_MODULE), key=lambda row: int(row.get("id", 0)), reverse=True)

    def archive_detail(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None or entry.get("status") != STATUS_ORDER[-1]:
            return None
        detail = dict(entry)
        record = next(
            (row for row in store.rows(ARCHIVE_MODULE) if int(row.get("月报ID", 0)) == entry_id),
            None,
        )
        if record is not None:
            detail["台账编号"] = record.get("台账编号")
            detail["归档时间"] = record.get("归档时间")
            detail["复核结论"] = record.get("复核结论")
        return detail
