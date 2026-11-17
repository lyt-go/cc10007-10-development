"""触点贡献复算接口：按新字段范围读取触点权重配置，生成复算明细与核对表。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.attributionrecalc import (
    MODULE,
    STATUS_ORDER,
    AttributionRecalcService,
    SOURCE_NOTE,
    WEIGHT_FIELDS,
)

router = APIRouter(prefix="/api/attributionrecalc", tags=["触点贡献复算"])

service = AttributionRecalcService()

LIST_FIELDS = [
    "复算编号", "批次编号", "归因模型编号", "模型版本", "目标指标", "指标分析编号",
    "数据窗口开始", "数据窗口结束", "状态", "核对结果", "边界状态", "贡献率合计",
    "触点贡献值合计", "明细数", "触发方式",
]
ACTIONS = ["执行复算", "落定核对", "归档复算"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按复算编号或归因模型编号检索"),
    status: str | None = Query(default=None, description="待复算、复算完成、已落定、已归档"),
    metric: str | None = Query(default=None, description="按目标指标过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """复算批次列表；没有数据时返回空页。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, metric=metric, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/fields")
def field_scope() -> dict[str, Any]:
    """复算读取的字段范围与计算口径，便于核对“按新字段范围读取”是否生效。"""
    return {
        "module": MODULE,
        "fields": list(WEIGHT_FIELDS),
        "statuses": STATUS_ORDER,
        "actions": ACTIONS,
        "source_note": SOURCE_NOTE,
    }


@router.get("/details")
def list_details(
    recalc_number: str | None = Query(default=None),
    metric: str | None = Query(default=None),
) -> dict[str, Any]:
    """触点贡献复算明细表；每行保留来源映射记录。"""
    rows = service.list_details(recalc_number=recalc_number, metric=metric)
    return {"total": len(rows), "items": rows}


@router.get("/checks")
def list_checks(
    settled_only: bool = Query(default=False, description="只看已落定核对表"),
    metric: str | None = Query(default=None),
) -> dict[str, Any]:
    """触点评分核对表；并发核对只采信已落定的记录。"""
    rows = service.list_checks(settled_only=settled_only, metric=metric)
    return {"total": len(rows), "items": rows}


@router.get("/verify")
def verify_concurrency(
    metric: str = Query(description="目标指标"),
    model_number: str | None = Query(default=None, description="可指定候选模型，未指定时取当前引用"),
) -> dict[str, Any]:
    """并发核对：只采用已落定核对表，冲突时给出说明而不是自动覆盖结论。"""
    return service.verify_concurrency(metric, model_number or "")


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出触点贡献复算清单。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": MODULE, "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条复算批次，连同明细表与核对表一起返回。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"触点贡献复算 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """新增复算批次：接收归因模型编号、目标指标与可选事件记录。"""
    entry, message, _missing = service.create_entry(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    blocked = entry.get("边界状态") == "越界" or entry.get("核对结果") == "越界阻断"
    return ActionResult(
        ok=True,
        message="触点贡献复算已生成" + ("，存在越界阻断项，不能落定" if blocked else "，待落定核对"),
        entry=entry,
    )


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """执行复算、落定核对、归档复算。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
