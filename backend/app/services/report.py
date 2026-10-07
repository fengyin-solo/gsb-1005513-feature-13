"""运行月报业务规则：口径重算、草稿→已复核→已归档状态流转、归档台账与存量回填。

计算数字一律来自 report_caliber.compute_monthly（口径只此一份），
状态与结果一律落 report_store（SQLite），service 不持有任何内存缓存。
"""
from __future__ import annotations

from typing import Any

from app.services import report_caliber as caliber_mod
from app.services import report_store as db
from app.services.report_caliber import CaliberParams

MODULE = "report"

STATUS_DRAFT = "草稿"
STATUS_REVIEWED = "已复核"
STATUS_ARCHIVED = "已归档"
STATUS_ORDER = [STATUS_DRAFT, STATUS_REVIEWED, STATUS_ARCHIVED]

# 存量旧版状态 → 新状态机
LEGACY_STATUS_MAP = {
    "待填写": STATUS_DRAFT,
    "已填写": STATUS_DRAFT,
    "已审核": STATUS_REVIEWED,
    "已发布": STATUS_ARCHIVED,
}

# 旧前端/旧脚本动作名兼容
ACTION_ALIAS = {
    "填写月报": "recalculate",
    "提交审核": "review",
    "发布月报": "archive",
}

DEFAULT_REVIEW_CONCLUSION = "复核通过：指标由系统按当月口径自动汇总，数据一致。"


def _normalize_month(value: Any) -> str:
    """把统计月份归一成 YYYY-MM；已是该形式的原样返回。"""
    text = str(value or "").strip()
    if len(text) >= 7 and text[4] in "-/":
        return f"{text[:4]}-{text[5:7]}"
    raise ValueError("统计月份格式应为 YYYY-MM")


class ReportService:
    # ---- 口径 ----------------------------------------------------------------

    def list_calibers(self) -> list[dict[str, Any]]:
        return db.list_calibers()

    def current_caliber(self) -> dict[str, Any]:
        return db.current_caliber()

    def update_caliber(self, values: dict[str, Any], remark: str | None) -> dict[str, Any]:
        """口径调整：新增版本并把所有未归档月报按新口径重算落库；
        已归档月报不动，继续沿用归档时冻结的口径版本。"""
        params = _params_from_values(values)
        saved = db.create_caliber(params.to_dict(), remark)
        for entry in db.list_monthly():
            if entry["月报状态"] != STATUS_ARCHIVED:
                self.recalculate(entry["id"])
        return saved

    # ---- 列表 / 详情 ----------------------------------------------------------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        month: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = db.list_monthly(status=status, month=month)
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("月报编号", "")) or keyword in str(row.get("统计月份", ""))
            ]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def list_archives(self) -> list[dict[str, Any]]:
        return db.list_archives()

    def get_archive(self, report_id: int) -> dict[str, Any] | None:
        return db.find_archive_by_report(report_id)

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return db.find_monthly(entry_id)

    # ---- 登记 / 回填 ----------------------------------------------------------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """按统计月份登记月报并立刻按当前口径重算。同月重复提交只保留最新一版；
        已归档月份拒绝覆盖。"""
        try:
            month = _normalize_month(values.get("统计月份"))
        except ValueError as exc:
            return None, str(exc)
        existing = db.find_by_month(month)
        if existing and existing["月报状态"] == STATUS_ARCHIVED:
            return None, f"{month} 月报已归档，历史台账不允许重新提交"
        entry = db.upsert_monthly(month, report_no=_opt_str(values.get("月报编号")))
        self.recalculate(entry["id"])
        return db.find_monthly(entry["id"]), ""

    def backfill_legacy(self, legacy_rows: list[dict[str, Any]]) -> dict[str, int]:
        """存量月报按统计月份回填：同月多行只留 id 最大的一版；
        回填即按当时/当前口径算好落库；已归档的同步生成归档台账（冻结口径版本）。

        只在月报库为空时执行，避免每次启动重复写。
        """
        if db.count_monthly() > 0:
            return {"backfilled": 0, "archived": 0}

        latest_by_month: dict[str, dict[str, Any]] = {}
        for row in legacy_rows:
            raw_month = str(row.get("统计月份") or "").strip()
            try:
                month = _normalize_month(raw_month)
            except ValueError:
                continue
            if month not in latest_by_month or int(row.get("id", 0)) > int(latest_by_month[month].get("id", 0)):
                latest_by_month[month] = row

        backfilled = archived = 0
        for month, row in sorted(latest_by_month.items()):
            entry = db.upsert_monthly(month, report_no=str(row.get("月报编号") or ""))
            self.recalculate(entry["id"])
            entry = db.find_monthly(entry["id"])
            mapped = LEGACY_STATUS_MAP.get(str(row.get("status") or ""), STATUS_DRAFT)
            if mapped == STATUS_REVIEWED:
                db.update_status(entry["id"], STATUS_REVIEWED)
                db.save_review(entry["id"], "历史复核结论（存量回填）")
            elif mapped == STATUS_ARCHIVED:
                db.update_status(entry["id"], STATUS_REVIEWED)
                db.save_review(entry["id"], "历史复核结论（存量回填）")
                db.archive_monthly(entry["id"])
                archived += 1
            backfilled += 1
        return {"backfilled": backfilled, "archived": archived}

    # ---- 重算 ----------------------------------------------------------------

    def recalculate(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """按「当月口径」重算：未归档月报用当前最新口径；已归档月报拒绝重算，
        继续沿用归档时冻结的版本。结果整体覆盖落库，不保留上一版缓存。"""
        entry = db.find_monthly(entry_id)
        if entry is None:
            return None, f"运行月报 {entry_id} 不存在"
        if entry["月报状态"] == STATUS_ARCHIVED:
            return None, "月报已归档，历史口径已冻结，不能重算"
        caliber = db.current_caliber()
        metrics = caliber_mod.compute_monthly(
            entry["统计月份"], CaliberParams.from_dict(caliber["params"])
        )
        db.save_metrics(entry_id, metrics, caliber["version"])
        return db.find_monthly(entry_id), ""

    def recalculate_open_months(self) -> int:
        count = 0
        for entry in db.list_monthly():
            if entry["月报状态"] != STATUS_ARCHIVED:
                self.recalculate(entry["id"])
                count += 1
        return count

    # ---- 复核 / 归档 / 退回 ---------------------------------------------------

    def review(
        self, entry_id: int, *, conclusion: str | None = None, reject: bool = False
    ) -> tuple[dict[str, Any] | None, str]:
        """复核：草稿可通过（→已复核）或退回（留在草稿）；
        已复核的报告再次点复核退回草稿；归档后点复核一律拒绝。"""
        entry = db.find_monthly(entry_id)
        if entry is None:
            return None, f"运行月报 {entry_id} 不存在"
        status = entry["月报状态"]
        if status == STATUS_ARCHIVED:
            return None, "月报已归档，复核已关闭，请在归档台账中查看历史结论"
        if reject:
            db.update_status(entry_id, STATUS_DRAFT)
            db.save_review(entry_id, conclusion or "复核退回：需按明细重新核对。")
            return db.find_monthly(entry_id), "复核已退回草稿"
        if status == STATUS_REVIEWED:
            # 已复核再次点「复核」按退回处理
            db.update_status(entry_id, STATUS_DRAFT)
            db.save_review(entry_id, conclusion or "复核退回：重新核对后再提交。")
            return db.find_monthly(entry_id), "已复核月报再次复核，已退回草稿"
        db.update_status(entry_id, STATUS_REVIEWED)
        db.save_review(entry_id, conclusion or DEFAULT_REVIEW_CONCLUSION)
        return db.find_monthly(entry_id), "月报已复核"

    def archive(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """归档：只有已复核月报可归档；归档时冻结指标与口径版本、写入归档台账，
        复核结论随快照进入台账。"""
        entry = db.find_monthly(entry_id)
        if entry is None:
            return None, f"运行月报 {entry_id} 不存在"
        if entry["月报状态"] == STATUS_ARCHIVED:
            return None, "月报已归档，请勿重复归档"
        if entry["月报状态"] != STATUS_REVIEWED:
            return None, "只有已复核月报才能归档，请先完成复核"
        archived = db.archive_monthly(entry_id)
        return archived, "月报已归档，复核结论已写入归档台账"

    def run_action(self, entry_id: int, action: str, values: dict[str, Any] | None = None):
        """统一动作入口（兼容旧动作名）。"""
        action = ACTION_ALIAS.get(action, action)
        values = values or {}
        if action == "recalculate":
            return self.recalculate(entry_id)
        if action == "review":
            return self.review(
                entry_id,
                conclusion=_opt_str(values.get("conclusion")),
                reject=bool(values.get("reject")),
            )
        if action == "archive":
            return self.archive(entry_id)
        return None, f"动作「{action}」不属于运行月报可执行范围"


def _opt_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _params_from_values(values: dict[str, Any]) -> CaliberParams:
    """从提交值构造口径参数：只接受白名单字段，非法取值直接报错。"""
    current = db.current_caliber()
    params = dict(current["params"])

    if "generation_source" in values:
        source = str(values["generation_source"])
        if source not in caliber_mod.GENERATION_SOURCES:
            raise ValueError(f"发电量取数来源仅支持：{'、'.join(caliber_mod.GENERATION_SOURCES)}")
        params["generation_source"] = source
    if "pr_basis" in values:
        basis = str(values["pr_basis"])
        if basis not in caliber_mod.PR_BASES:
            raise ValueError(f"PR理论发电口径仅支持：{'、'.join(caliber_mod.PR_BASES)}")
        params["pr_basis"] = basis
    for key in ("peak_sun_hours", "alarm_downtime_hours", "defect_downtime_hours"):
        if key in values and str(values[key]) != "":
            try:
                num = float(values[key])
            except (TypeError, ValueError):
                raise ValueError(f"{key} 必须是数字")
            if num < 0:
                raise ValueError(f"{key} 不能为负")
            params[key] = num
    if "downtime_sources" in values:
        sources = values["downtime_sources"]
        if isinstance(sources, str):
            sources = [item.strip() for item in sources.split(",") if item.strip()]
        bad = [item for item in sources if item not in caliber_mod.DOWNTIME_SOURCES]
        if bad:
            raise ValueError(f"停机时间统计范围仅支持：{'、'.join(caliber_mod.DOWNTIME_SOURCES)}")
        params["downtime_sources"] = list(sources)
    return CaliberParams.from_dict(params)
