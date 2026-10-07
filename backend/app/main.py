"""光伏电站运维管理平台 后端服务入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8000
健康检查：GET /api/health
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import ROUTERS
from app.services import report_store as report_db
from app.services.report import ReportService
from app.services.report_caliber import CaliberParams
from app.store import store


@asynccontextmanager
async def lifespan(_: FastAPI):
    """启动时为月报模块建表、落默认口径 v1，并把存量月报按统计月份回填重算。

    回填只在月报库为空时发生；之后所有月报数字都以数据库为准，重启不回退。
    """
    report_db.init_db()
    if not report_db.list_calibers():
        report_db.create_caliber(
            CaliberParams().to_dict(),
            "初始口径：关口表计电量、峰值日照小时PR、告警+缺陷折算停机",
        )
    ReportService().backfill_legacy(store.rows("report"))
    yield


app = FastAPI(title="光伏电站运维管理平台", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in ROUTERS:
    app.include_router(module.router)


@app.get("/api/health")
def health() -> dict[str, object]:
    """健康检查：确认服务已经监听、示例数据已经就绪。"""
    return {"ok": True, "app": settings.app_name, "modules": len(store.module_names())}


@app.get("/api/overview")
def overview() -> dict[str, object]:
    """运营概览：把各业务模块的待处理量汇总成看板卡片。"""
    return store.overview()
