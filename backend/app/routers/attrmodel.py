"""归因建模接口：维护归因模型，覆盖评审、发布（含发布前复算）、切换、归档与恢复。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.attrmodel import AttrmodelService

router = APIRouter(prefix="/api/attrmodel", tags=["归因建模"])

service = AttrmodelService()

LIST_FIELDS = [
    "模型编号", "模型名称", "归因逻辑", "目标指标", "触点权重配置", "触点权重分布", "衰减天数",
    "转化窗口", "直接贡献率", "间接贡献率", "版本号", "发布复算编号", "发布指标引用",
]
STATUSES = ["草稿", "模型评审中", "已评审", "已发布", "已下线", "已归档"]
ACTIONS = ["提交模型评审", "完成模型评审", "发布模型", "下线模型", "归档模型", "恢复模型", "切换指标引用"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按模型编号或模型名称检索"),
    status: str | None = Query(default=None, description="草稿、模型评审中、已评审、已发布、已下线、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按模型编号与状态过滤归因建模列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/fields")
def field_scope() -> dict[str, Any]:
    """归因建模生命周期、动作与可复算字段范围。"""
    return {
        "module": "attrmodel",
        "statuses": STATUSES,
        "actions": ACTIONS,
        "list_fields": LIST_FIELDS,
        "recalc_fields": ["目标指标", "触点权重配置", "贡献率下限", "贡献率上限", "数据窗口开始", "数据窗口结束"],
    }


@router.get("/candidates")
def candidates(metric: str | None = Query(default=None, description="按目标指标筛选已评审候选")) -> dict[str, Any]:
    """同一目标指标下的已评审候选模型，按评审完成时间倒序，用于切换引用。"""
    rows = service.candidates(metric)
    return {"total": len(rows), "items": rows}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出归因建模清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "attrmodel", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条归因模型明细，连同权重解析、变更留痕与下游引用。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"归因模型 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/weight-scope")
def weight_scope(entry_id: int) -> dict[str, Any]:
    """只返回复算所需的触点权重字段范围与解析结果。"""
    scope, message = service.weight_scope(entry_id)
    if scope is None:
        raise HTTPException(status_code=404, detail=message)
    return scope


@router.get("/{entry_id}/trace")
def trace(entry_id: int) -> dict[str, Any]:
    """模型生命周期变更留痕：切换、归档与恢复都应有记录。"""
    rows = service.trace(entry_id)
    if rows is None:
        raise HTTPException(status_code=404, detail=f"归因模型 {entry_id} 不存在或已归档")
    return {"total": len(rows), "items": rows}


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条归因模型，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="归因模型已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """提交评审、完成评审、发布、下线、归档、恢复、切换指标引用。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
