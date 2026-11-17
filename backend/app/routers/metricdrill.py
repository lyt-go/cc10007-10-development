"""指标归因接口：维护归因分析，登记复算核对表，识别双版本冲突与历史只读口径。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.metricdrill import MetricdrillService

router = APIRouter(prefix="/api/metricdrill", tags=["指标归因"])

service = MetricdrillService()

LIST_FIELDS = [
    "分析编号", "目标指标", "波动方向", "波动幅度", "维度下钻路径", "贡献度排名", "根因假设",
    "验证结果", "归因模型编号", "模型版本", "归因建模状态", "复算核对编号",
]
STATUSES = ["草稿", "分析中", "已确认", "已归档"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按分析编号或目标指标检索"),
    status: str | None = Query(default=None, description="草稿、分析中、已确认、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按分析编号与状态过滤指标归因列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/fields")
def field_scope() -> dict[str, Any]:
    """指标归因状态、引用字段与历史查看口径。"""
    return {
        "module": "metricdrill",
        "statuses": STATUSES,
        "list_fields": LIST_FIELDS,
        "history_view": "已确认及已归档的指标归因按确认时的归因快照只读查看，不随模型版本变化",
        "single_version_rule": "同一目标指标只展示一套当前根因与贡献度排名，并标出对应模型版本",
    }


@router.get("/pending")
def pending_details(
    metric: str | None = Query(default=None, description="按目标指标过滤待处理明细"),
) -> dict[str, Any]:
    """指标下钻待处理明细：复算核对表的登记入口。"""
    rows = service.pending_details(metric)
    return {"total": len(rows), "items": rows}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出指标归因清单。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "metricdrill", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条归因分析明细；已确认记录同时给出根因版本清单。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"归因分析 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条归因分析，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="归因分析已登记", entry=entry)


@router.post("/pending", response_model=ActionResult)
def register_pending(payload: EntryPayload) -> ActionResult:
    """把已落定的触点贡献复算核对表登记到指标下钻待处理明细。"""
    entry, message = service.register_pending(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条归因分析执行发起分析、确认结论、归档分析；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
