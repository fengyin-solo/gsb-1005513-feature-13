"""运行月报回归测试：口径汇总、状态机、归档冻结、台账一致、幂等回填与重启持久化。

只依赖标准库 unittest，跑法：
    cd backend && REPORT_DB_PATH 无需设置（测试自建临时库） .venv/bin/python -m unittest discover -s tests
"""
from __future__ import annotations

import os
import tempfile
import unittest

_tmp_dir = tempfile.mkdtemp(prefix="report_test_")
os.environ["REPORT_DB_PATH"] = os.path.join(_tmp_dir, "report.db")

from app.services import report_store as db  # noqa: E402
from app.services.report import (  # noqa: E402
    STATUS_ARCHIVED,
    STATUS_DRAFT,
    STATUS_REVIEWED,
    ReportService,
)
from app.services.report_caliber import CaliberParams  # noqa: E402
from app.store import store  # noqa: E402


class ReportFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        # 每个用例一份全新库
        path = os.path.join(_tmp_dir, f"{self._testMethodName}.db")
        os.environ["REPORT_DB_PATH"] = path
        db.DB_PATH = path
        db.init_db()
        db.create_caliber(CaliberParams().to_dict(), "初始口径")
        self.service = ReportService()
        self.service.backfill_legacy(store.rows("report"))

    def _id(self, month: str) -> int:
        return db.find_by_month(month)["id"]

    def test_backfill_dedupes_same_month_and_maps_status(self) -> None:
        # 存量三个月份：07 归档 / 08 草稿 / 09 已复核
        months = {row["统计月份"]: row for row in db.list_monthly()}
        self.assertEqual(set(months), {"2026-07", "2026-08", "2026-09"})
        self.assertEqual(months["2026-07"]["status"], STATUS_ARCHIVED)
        self.assertEqual(months["2026-08"]["status"], STATUS_DRAFT)
        self.assertEqual(months["2026-09"]["status"], STATUS_REVIEWED)
        # 归档月已进台账，且冻结口径 v1
        archives = {a["统计月份"]: a for a in db.list_archives()}
        self.assertIn("2026-07", archives)
        self.assertEqual(archives["2026-07"]["口径版本"], 1)

    def test_recalculate_persists_and_reread_identical(self) -> None:
        entry_id = self._id("2026-08")
        entry, msg = self.service.recalculate(entry_id)
        self.assertTrue(entry["发电量"] > 0)
        self.assertIsNotNone(entry["最近重算时间"])
        # 新实例（模拟重启）读同一份库，值不回退
        fresh = ReportService().get_entry(entry_id)
        for field in ("发电量", "等效利用小时", "综合效率PR", "设备可利用率", "故障停机时间"):
            self.assertEqual(fresh[field], entry[field])

    def test_caliber_change_only_affects_open_months(self) -> None:
        archived_before = self.service.get_entry(self._id("2026-07"))
        reviewed_before = self.service.get_entry(self._id("2026-09"))
        db.create_caliber(
            CaliberParams(peak_sun_hours=150.0, downtime_sources=("alarm",)).to_dict(),
            "v2",
        )
        ReportService().recalculate_open_months()
        archived_after = self.service.get_entry(self._id("2026-07"))
        reviewed_after = self.service.get_entry(self._id("2026-09"))
        # 归档月报：口径版本与指标完全冻结
        self.assertEqual(archived_after["口径版本"], 1)
        self.assertEqual(archived_after["综合效率PR"], archived_before["综合效率PR"])
        # 未归档月报：按新口径重算（停机只剩未闭环告警 2 条 × 8h）
        self.assertEqual(reviewed_after["口径版本"], 2)
        self.assertEqual(reviewed_after["故障停机时间"], 16.0)
        self.assertNotEqual(reviewed_after["综合效率PR"], reviewed_before["综合效率PR"])

    def test_archived_cannot_recalculate_or_review(self) -> None:
        entry_id = self._id("2026-07")
        self.assertIsNone(self.service.recalculate(entry_id)[0])
        self.assertIsNone(self.service.review(entry_id)[0])

    def test_reviewed_review_again_returns_to_draft(self) -> None:
        entry_id = self._id("2026-09")
        entry, _ = self.service.review(entry_id, conclusion="重新核对")
        self.assertEqual(entry["月报状态"], STATUS_DRAFT)
        self.assertEqual(entry["复核结论"], "重新核对")

    def test_archive_requires_reviewed_and_writes_ledger(self) -> None:
        draft_id = self._id("2026-08")
        # 草稿不能归档
        self.assertIsNone(self.service.archive(draft_id)[0])
        # 复核 -> 归档
        self.service.review(draft_id, conclusion="数据一致，同意")
        self.service.archive(draft_id)
        # 列表行、台账列表、台账详情三处读到的数字必须一致
        monthly = db.find_monthly(draft_id)
        ledger_list = [a for a in db.list_archives() if a["月报ID"] == draft_id][0]
        ledger_detail = db.find_archive_by_report(draft_id)
        for field in ("发电量", "等效利用小时", "综合效率PR", "设备可利用率", "故障停机时间"):
            self.assertEqual(monthly[field], ledger_list[field])
            self.assertEqual(monthly[field], ledger_detail[field])
        self.assertEqual(ledger_detail["复核结论"], "数据一致，同意")
        self.assertEqual(ledger_detail["口径版本"], monthly["口径版本"])

    def test_same_month_resubmit_keeps_single_latest(self) -> None:
        entry, _ = self.service.create_entry({"统计月份": "2026-08"})
        again, _ = self.service.create_entry({"统计月份": "2026-08"})
        self.assertEqual(entry["id"], again["id"])
        rows = [r for r in db.list_monthly() if r["统计月份"] == "2026-08"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["版本号"], 3)  # 回填 v1 + 两次提交

    def test_resubmit_archived_month_rejected(self) -> None:
        entry, message = self.service.create_entry({"统计月份": "2026-07"})
        self.assertIsNone(entry)
        self.assertIn("已归档", message)

    def test_metrics_come_from_module_details(self) -> None:
        # 关口表计：(240000-198000)+(200000-162000)+(250000-218000)=112000
        entry, _ = self.service.recalculate(self._id("2026-08"))
        self.assertEqual(entry["发电量"], 112000.0)
        # 装机 500+300+200，等效利用小时 = 112000/1000
        self.assertEqual(entry["装机容量"], 1000.0)
        self.assertEqual(entry["等效利用小时"], 112.0)
        # PR = 112000 / (1000*140) = 80%
        self.assertEqual(entry["综合效率PR"], 80.0)

    def test_invalid_month_and_invalid_caliber_rejected(self) -> None:
        entry, message = self.service.create_entry({"统计月份": "九月"})
        self.assertIsNone(entry)
        with self.assertRaises(ValueError):
            self.service.update_caliber({"generation_source": "人工手填"}, None)


if __name__ == "__main__":
    unittest.main()
