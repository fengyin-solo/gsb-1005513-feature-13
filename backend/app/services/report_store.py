"""运行月报持久化：SQLite 保存口径版本、月报与归档台账。

运行月报的所有读（列表、详情、归档详情）都只认这里的结果，重算直接写库，
刷新或重新进入读到的就是最后一次重算结果，不存在内存/缓存两份数据。
其他业务模块仍走内存 store，月报指标由口径引擎实时从各模块明细汇总后落库。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any

DB_PATH = os.environ.get(
    "REPORT_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "report.db"),
)

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS report_caliber (
    version        INTEGER PRIMARY KEY AUTOINCREMENT,
    params_json    TEXT NOT NULL,
    remark         TEXT,
    is_current     INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS report_monthly (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_no       TEXT NOT NULL,
    stat_month      TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT '草稿',
    version_no      INTEGER NOT NULL DEFAULT 1,
    generation      REAL,
    equiv_hours     REAL,
    pr_ratio        REAL,
    availability    REAL,
    downtime_hours  REAL,
    capacity_kw     REAL,
    basis_json      TEXT,
    caliber_version INTEGER,
    review_conclusion TEXT,
    reviewed_at     TEXT,
    recalculated_at TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS report_archive (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id       INTEGER NOT NULL UNIQUE,
    report_no       TEXT NOT NULL,
    stat_month      TEXT NOT NULL UNIQUE,
    generation      REAL,
    equiv_hours     REAL,
    pr_ratio        REAL,
    availability    REAL,
    downtime_hours  REAL,
    capacity_kw     REAL,
    caliber_version INTEGER,
    basis_json      TEXT,
    review_conclusion TEXT,
    archived_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""


def init_db() -> None:
    with _lock, _connect() as conn:
        conn.executescript(SCHEMA)


# ---- 口径版本 -------------------------------------------------------------------

def list_calibers() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM report_caliber ORDER BY version"
        ).fetchall()
    return [_caliber_row(row) for row in rows]


def current_caliber() -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM report_caliber WHERE is_current = 1 ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM report_caliber ORDER BY version DESC LIMIT 1"
            ).fetchone()
    if row is None:
        raise RuntimeError("口径尚未初始化")
    return _caliber_row(row)


def create_caliber(params: dict[str, Any], remark: str | None) -> dict[str, Any]:
    """新增一版口径并切为当前版本；历史版本原样保留，供已归档月报沿用。"""
    with _lock, _connect() as conn:
        conn.execute("UPDATE report_caliber SET is_current = 0")
        cur = conn.execute(
            "INSERT INTO report_caliber (params_json, remark, is_current) VALUES (?, ?, 1)",
            (json.dumps(params, ensure_ascii=False), remark),
        )
        row = conn.execute(
            "SELECT * FROM report_caliber WHERE version = ?", (cur.lastrowid,)
        ).fetchone()
    return _caliber_row(row)


def _caliber_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["params"] = json.loads(data.pop("params_json"))
    data["is_current"] = bool(data["is_current"])
    return data


# ---- 月报 -----------------------------------------------------------------------

MONTH_FIELDS = {
    "发电量": "generation",
    "等效利用小时": "equiv_hours",
    "综合效率PR": "pr_ratio",
    "设备可利用率": "availability",
    "故障停机时间": "downtime_hours",
    "装机容量": "capacity_kw",
}


def _month_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    result = {
        "id": data["id"],
        "月报编号": data["report_no"],
        "统计月份": data["stat_month"],
        "月报状态": data["status"],
        "status": data["status"],
        "版本号": data["version_no"],
        "口径版本": data["caliber_version"],
        "发电量": data["generation"],
        "等效利用小时": data["equiv_hours"],
        "综合效率PR": data["pr_ratio"],
        "设备可利用率": data["availability"],
        "故障停机时间": data["downtime_hours"],
        "装机容量": data["capacity_kw"],
        "复核结论": data["review_conclusion"],
        "复核时间": data["reviewed_at"],
        "最近重算时间": data["recalculated_at"],
        "更新时间": data["updated_at"],
        "计算依据": json.loads(data["basis_json"]) if data.get("basis_json") else None,
        "pending": data["status"] != "已归档",
        "abnormal": False,
    }
    return result


def list_monthly(status: str | None = None, month: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM report_monthly WHERE 1 = 1"
    args: list[Any] = []
    if status:
        sql += " AND status = ?"
        args.append(status)
    if month:
        sql += " AND stat_month LIKE ?"
        args.append(f"{month}%")
    sql += " ORDER BY stat_month DESC, id"
    with _connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_month_row(row) for row in rows]


def find_monthly(entry_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM report_monthly WHERE id = ?", (entry_id,)
        ).fetchone()
    return _month_row(row) if row else None


def find_by_month(stat_month: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM report_monthly WHERE stat_month = ?", (stat_month,)
        ).fetchone()
    return _month_row(row) if row else None


def upsert_monthly(stat_month: str, report_no: str | None = None) -> dict[str, Any]:
    """按统计月份登记：同月已存在就只留这一版（编号更新、版本号 +1、回到草稿），
    不存在则新建。已归档月份不允许覆盖（调用方先拦截）。"""
    existing = find_by_month(stat_month)
    with _lock, _connect() as conn:
        if existing is None:
            next_no = _next_report_no(conn)
            cur = conn.execute(
                """INSERT INTO report_monthly (report_no, stat_month, status, version_no)
                   VALUES (?, ?, '草稿', 1)""",
                (report_no or next_no, stat_month),
            )
            entry_id = cur.lastrowid
        else:
            entry_id = existing["id"]
            conn.execute(
                """UPDATE report_monthly
                   SET report_no = ?, version_no = version_no + 1,
                       status = '草稿', review_conclusion = NULL, reviewed_at = NULL,
                       updated_at = datetime('now', 'localtime')
                   WHERE id = ?""",
                (report_no or existing["月报编号"], entry_id),
            )
        row = conn.execute(
            "SELECT * FROM report_monthly WHERE id = ?", (entry_id,)
        ).fetchone()
    return _month_row(row)


def _next_report_no(conn: sqlite3.Connection) -> str:
    count = conn.execute("SELECT COUNT(*) AS c FROM report_monthly").fetchone()["c"]
    return f"REPO-{count + 1:04d}"


def save_metrics(entry_id: int, metrics: dict[str, Any], caliber_version: int) -> None:
    """把重算结果整体写库（覆盖式，不追加缓存行），读回时只能看到这一版。"""
    with _lock, _connect() as conn:
        conn.execute(
            """UPDATE report_monthly SET
                   generation = ?, equiv_hours = ?, pr_ratio = ?, availability = ?,
                   downtime_hours = ?, capacity_kw = ?, basis_json = ?,
                   caliber_version = ?,
                   recalculated_at = datetime('now', 'localtime'),
                   updated_at = datetime('now', 'localtime')
               WHERE id = ?""",
            (
                metrics["发电量"],
                metrics["等效利用小时"],
                metrics["综合效率PR"],
                metrics["设备可利用率"],
                metrics["故障停机时间"],
                metrics["装机容量"],
                json.dumps(metrics["计算依据"], ensure_ascii=False),
                caliber_version,
                entry_id,
            ),
        )


def update_status(entry_id: int, status: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE report_monthly SET status = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (status, entry_id),
        )


def save_review(entry_id: int, conclusion: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            """UPDATE report_monthly
               SET review_conclusion = ?, reviewed_at = datetime('now', 'localtime'),
                   updated_at = datetime('now', 'localtime')
               WHERE id = ?""",
            (conclusion, entry_id),
        )


# ---- 归档台账 -------------------------------------------------------------------

def archive_monthly(entry_id: int) -> dict[str, Any] | None:
    """归档：把月报当前值与复核结论快照写入台账。台账是插入式流水，但每月唯一；
    月报列表与归档详情读的是同一次落库的值（归档详情直接联表读两表）。"""
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM report_monthly WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            """INSERT INTO report_archive
                   (report_id, report_no, stat_month, generation, equiv_hours, pr_ratio,
                    availability, downtime_hours, capacity_kw, caliber_version, basis_json,
                    review_conclusion)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(report_id) DO UPDATE SET
                   report_no = excluded.report_no,
                   stat_month = excluded.stat_month,
                   generation = excluded.generation,
                   equiv_hours = excluded.equiv_hours,
                   pr_ratio = excluded.pr_ratio,
                   availability = excluded.availability,
                   downtime_hours = excluded.downtime_hours,
                   capacity_kw = excluded.capacity_kw,
                   caliber_version = excluded.caliber_version,
                   basis_json = excluded.basis_json,
                   review_conclusion = excluded.review_conclusion,
                   archived_at = datetime('now', 'localtime')""",
            (
                row["id"], row["report_no"], row["stat_month"], row["generation"],
                row["equiv_hours"], row["pr_ratio"], row["availability"],
                row["downtime_hours"], row["capacity_kw"], row["caliber_version"],
                row["basis_json"], row["review_conclusion"],
            ),
        )
        conn.execute(
            "UPDATE report_monthly SET status = '已归档', updated_at = datetime('now', 'localtime') WHERE id = ?",
            (entry_id,),
        )
        saved = conn.execute(
            "SELECT * FROM report_archive WHERE report_id = ?", (entry_id,)
        ).fetchone()
    return _archive_row(saved)


def list_archives() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM report_archive ORDER BY archived_at DESC, id"
        ).fetchall()
    return [_archive_row(row) for row in rows]


def find_archive_by_report(report_id: int) -> dict[str, Any] | None:
    """归档详情：以归档台账为主，联月报主表取最新状态/复核时间。
    列表和详情都源自同一份持久化记录，保证两处一致。"""
    with _connect() as conn:
        row = conn.execute(
            """SELECT a.*, m.status AS current_status, m.reviewed_at AS reviewed_at,
                      m.version_no AS version_no
               FROM report_archive a
               JOIN report_monthly m ON m.id = a.report_id
               WHERE a.report_id = ?""",
            (report_id,),
        ).fetchone()
    return _archive_row(row) if row else None


def _archive_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    result = {
        "id": data["id"],
        "月报ID": data["report_id"],
        "月报编号": data["report_no"],
        "统计月份": data["stat_month"],
        "发电量": data["generation"],
        "等效利用小时": data["equiv_hours"],
        "综合效率PR": data["pr_ratio"],
        "设备可利用率": data["availability"],
        "故障停机时间": data["downtime_hours"],
        "装机容量": data["capacity_kw"],
        "口径版本": data["caliber_version"],
        "复核结论": data["review_conclusion"],
        "归档时间": data["archived_at"],
        "计算依据": json.loads(data["basis_json"]) if data.get("basis_json") else None,
    }
    if "current_status" in data:
        result["月报状态"] = data["current_status"]
        result["复核时间"] = data["reviewed_at"]
        result["版本号"] = data["version_no"]
    else:
        result["月报状态"] = "已归档"
    return result


def count_monthly() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM report_monthly").fetchone()["c"]
