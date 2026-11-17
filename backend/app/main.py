"""智能运营决策分析平台 后端服务入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8000
健康检查：GET /api/health
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import ROUTERS
from app.store import store

MODULE_LABELS = {
    "sequence": "行为序列", "rfm": "RFM分层", "churn": "流失预警", "recommend": "智能推荐",
    "attrmodel": "归因建模", "activity": "活动评估", "realtime": "实时决策", "budget": "预算分配",
    "lifetime": "LTV估算", "abvariant": "实验变体", "dashboard": "运营看板", "scorecard": "评分卡",
    "cluster": "聚类分群", "comparison": "对比分析", "datadrift": "数据漂移", "pushplan": "推送计划",
    "metricdrill": "指标归因", "tacticlib": "策略库",
}

app = FastAPI(title="智能运营决策分析平台", version="1.0.0")

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


@app.get("/api/overview/modules")
def overview_modules() -> dict[str, object]:
    """业务模块视图：模块名 → 中文名与归属，供列表页关联核验。"""
    return {
        "items": [
            {"module": item["name"], "label": MODULE_LABELS.get(item["name"], item["name"])}
            for item in store.overview()["modules"]
        ],
        "total": len(store.overview()["modules"]),
    }


@app.post("/api/admin/reset")
def reset_demo_data() -> dict[str, object]:
    """重置示例数据：仅用于本地演示与回归样例（METR-0002）重复核对。"""
    if settings.env != "local":
        return {"ok": False, "message": "仅本地环境允许重置示例数据"}
    store.reset()
    return {"ok": True, "message": "示例数据已重置为初始状态"}
