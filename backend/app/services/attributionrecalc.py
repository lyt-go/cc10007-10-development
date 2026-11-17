"""触点贡献复算业务规则：读取触点权重配置、计算触点贡献、落定核对表。

复算只按新字段范围读取归因模型配置，不读模型的历史展示字段；明细表保留来源映射，
发布/核对/并发判断都以“已落定”的核对表为准。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "attributionrecalc"
DETAIL_MODULE = "attributionrecalc_detail"
CHECK_MODULE = "attributionrecalc_check"
EVENT_MODULE = "attributionevent"
METRIC_MODULE = "metricdrill"

# 复算读取的字段范围：只认这几个新字段，展示字段（触点权重分布等）不参与计算。
WEIGHT_FIELDS = ["目标指标", "触点权重配置", "贡献率下限", "贡献率上限", "数据窗口开始", "数据窗口结束"]

STATUS_ORDER = ["待复算", "复算完成", "已落定", "已归档"]
ACTION_RULES = {"执行复算": "复算完成", "落定核对": "已落定", "归档复算": "已归档"}
CHECK_PENDING = "待核对"
CHECK_SETTLED = "已落定"
CHECK_BLOCKED = "越界阻断"
BOUNDARY_OK = "正常"
BOUNDARY_OVER = "越界"
TOLERANCE = 1e-6
SOURCE_NOTE = "触点权重配置取自归因模型新字段；转化值取自目标指标事件记录；仅按数据窗口内事件计算"


@dataclass
class WeightItem:
    name: str
    weight: float


@dataclass
class ComputeResult:
    ok: bool
    errors: list[str]
    details: list[dict[str, Any]]
    ranking: list[dict[str, Any]]
    total_value: float
    contribution_sum: float
    boundary: str
    weight_config: str
    window_start: str
    window_end: str
    source_map: dict[str, Any]

    @property
    def message(self) -> str:
        return "；".join(self.errors) if self.errors else "触点贡献复算通过"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def parse_weight_config(text: str | None) -> tuple[list[WeightItem], list[str]]:
    """解析“触点:权重”配置，权重和必须为 1，单项必须在 0~1 之间。"""
    items: list[WeightItem] = []
    errors: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,，;；|]", str(text or "")):
        part = part.strip()
        if not part:
            continue
        pieces = re.split(r"[:：]", part, maxsplit=1)
        if len(pieces) != 2:
            errors.append(f"触点权重片段「{part}」缺少“触点:权重”格式")
            continue
        name, raw = pieces[0].strip(), pieces[1].strip()
        if not name:
            errors.append(f"触点权重片段「{part}」缺少触点名称")
            continue
        if name in seen:
            errors.append(f"触点「{name}」重复配置权重")
            continue
        try:
            weight = float(raw)
        except ValueError:
            errors.append(f"触点「{name}」权重「{raw}」不是数字")
            continue
        if weight < 0 or weight > 1:
            errors.append(f"触点「{name}」权重 {weight:g} 越界（应在 0~1 之间）")
        seen.add(name)
        items.append(WeightItem(name=name, weight=weight))
    if not items:
        errors.append("触点权重配置为空，无法复算")
    else:
        total = sum(item.weight for item in items)
        if abs(total - 1.0) > TOLERANCE:
            errors.append(f"触点权重合计 {total:.4f} 不等于 1，贡献率越界")
    return items, errors


def read_weight_scope(model: dict[str, Any]) -> dict[str, Any]:
    """按新字段范围读取模型配置，顺带给出字段清单，便于核对读取口径。"""
    return {
        "字段范围": list(WEIGHT_FIELDS),
        "取值": {field: model.get(field) for field in WEIGHT_FIELDS},
    }


def _window_bounds(model: dict[str, Any], metric: dict[str, Any] | None) -> tuple[str, str]:
    start = str(model.get("数据窗口开始") or "").strip()
    end = str(model.get("数据窗口结束") or "").strip()
    if not start and metric is not None:
        start = str(metric.get("数据窗口开始") or "").strip()
    if not end and metric is not None:
        end = str(metric.get("数据窗口结束") or "").strip()
    return start, end


def _events_for(metric_name: str, start: str, end: str) -> list[dict[str, Any]]:
    events = [row for row in store.rows(EVENT_MODULE) if row.get("目标指标") == metric_name]
    if start:
        events = [row for row in events if str(row.get("事件时间", "")) >= start]
    if end:
        events = [row for row in events if str(row.get("事件时间", "")) <= end]
    return events


def compute(model: dict[str, Any], metric_name: str, metric_entry: dict[str, Any] | None = None) -> ComputeResult:
    """核心复算：权重 × 触点转化值，输出明细、排名与边界判定。"""
    weights, errors = parse_weight_config(model.get("触点权重配置"))
    model_number = str(model.get("模型编号") or "")
    version = str(model.get("版本号") or "")
    configured_metric = str(model.get("目标指标") or "")
    if configured_metric and metric_name and configured_metric != metric_name:
        # 配置目标指标与分析目标不一致时不产出排名，避免生成误导性结论。
        errors.append(f"归因模型配置的目标指标为「{configured_metric}」，与复算目标指标「{metric_name}」不一致")
        return ComputeResult(
            ok=False,
            errors=errors,
            details=[],
            ranking=[],
            total_value=0.0,
            contribution_sum=0.0,
            boundary=BOUNDARY_OVER,
            weight_config=str(model.get("触点权重配置") or ""),
            window_start="",
            window_end="",
            source_map={"归因模型": f"{model_number} {version}".strip(), "目标指标": metric_name, "说明": "目标指标不一致，未执行计算"},
        )
    start, end = _window_bounds(model, metric_entry)
    if start and end and start > end:
        errors.append(f"数据窗口 {start} ~ {end} 起止顺序不正确")
    try:
        lower = float(model.get("贡献率下限") if model.get("贡献率下限") not in (None, "") else 0.0)
        upper = float(model.get("贡献率上限") if model.get("贡献率上限") not in (None, "") else 1.0)
    except (TypeError, ValueError):
        lower, upper = 0.0, 1.0
        errors.append("贡献率上下限不是数字，无法判定边界")
    if lower > upper:
        errors.append(f"贡献率下限 {lower:g} 大于上限 {upper:g}，边界配置无效")

    events = _events_for(metric_name, start, end) if metric_name else []
    totals: dict[str, float] = {item.name: 0.0 for item in weights}
    unknown: set[str] = set()
    for event in events:
        touch = str(event.get("触点") or "")
        if touch not in totals:
            unknown.add(touch)
            continue
        try:
            totals[touch] += float(event.get("转化值") or 0)
        except (TypeError, ValueError):
            errors.append(f"事件记录 {event.get('id')} 的转化值不是数字")
    if unknown:
        errors.append(f"目标指标事件记录包含未配置权重的触点：{'、'.join(sorted(unknown))}")
    if not events:
        errors.append(f"目标指标「{metric_name}」在数据窗口 {start or '不限'} ~ {end or '不限'} 内没有事件记录")
    if not metric_name:
        errors.append("复算缺少目标指标")
    if start and end and start > end:
        events = []

    weighted = {item.name: item.weight * totals.get(item.name, 0.0) for item in weights}
    total_value = sum(weighted.values())
    if total_value <= 0:
        errors.append("目标指标在数据窗口内的触点贡献值合计为 0，无法计算贡献占比")

    details: list[dict[str, Any]] = []
    ranking: list[dict[str, Any]] = []
    contribution_sum = 0.0
    boundary = BOUNDARY_OK
    if total_value > 0:
        ordered = sorted(weights, key=lambda item: (-weighted[item.name], -item.weight, item.name))
        for rank, item in enumerate(ordered, start=1):
            share = weighted[item.name] / total_value
            contribution_sum += share
            out_of_bound = share < lower - TOLERANCE or share > upper + TOLERANCE
            if out_of_bound:
                boundary = BOUNDARY_OVER
                errors.append(
                    f"触点「{item.name}」贡献率 {_percent(share)} 越界"
                    f"（允许范围 {_percent(lower)} ~ {_percent(upper)}）"
                )
            ranking.append({
                "排名": rank,
                "触点": item.name,
                "触点权重": round(item.weight, 4),
                "触点转化值": round(totals.get(item.name, 0.0), 2),
                "触点贡献值": round(weighted[item.name], 2),
                "贡献占比": _percent(share),
                "贡献占比数值": round(share, 4),
                "边界状态": BOUNDARY_OVER if out_of_bound else BOUNDARY_OK,
            })
        by_name = {item["触点"]: item for item in ranking}
        for item in weights:
            hit = by_name[item.name]
            details.append({
                "复算编号": "",
                "批次编号": "",
                "归因模型编号": model_number,
                "模型版本": version,
                "目标指标": metric_name,
                "指标分析编号": str((metric_entry or {}).get("分析编号") or ""),
                "触点": hit["触点"],
                "触点权重": hit["触点权重"],
                "触点转化值": hit["触点转化值"],
                "触点贡献值": hit["触点贡献值"],
                "贡献占比": hit["贡献占比"],
                "贡献占比数值": hit["贡献占比数值"],
                "贡献度排名": hit["排名"],
                "边界状态": hit["边界状态"],
                "贡献率下限": round(lower, 4),
                "贡献率上限": round(upper, 4),
                "数据窗口开始": start,
                "数据窗口结束": end,
                "来源映射": (
                    f"attrmodel:{model_number}/触点权重配置; "
                    f"attributionevent:{metric_name}; "
                    f"metricdrill:{str((metric_entry or {}).get('分析编号') or '-')}"
                ),
            })
        details.sort(key=lambda row: row["贡献度排名"])

    source_map = {
        "归因模型": f"{model_number} {version}".strip(),
        "触点权重字段范围": list(WEIGHT_FIELDS),
        "事件记录表": EVENT_MODULE,
        "指标分析": str((metric_entry or {}).get("分析编号") or ""),
        "目标指标": metric_name,
        "数据窗口": f"{start or '不限'} ~ {end or '不限'}",
        "事件条数": len(events),
        "计算口径": "触点贡献值 = 触点权重 × 窗口内触点转化值；贡献占比 = 触点贡献值 / 合计",
    }
    return ComputeResult(
        ok=not errors and boundary == BOUNDARY_OK,
        errors=errors,
        details=details,
        ranking=ranking,
        total_value=round(total_value, 2),
        contribution_sum=round(contribution_sum, 4),
        boundary=boundary,
        weight_config=str(model.get("触点权重配置") or ""),
        window_start=start,
        window_end=end,
        source_map=source_map,
    )


def ranking_text(ranking: list[dict[str, Any]]) -> str:
    if not ranking:
        return ""
    return " > ".join(f"{item['触点']} {item['贡献占比']}" for item in ranking)


def root_cause_text(check: dict[str, Any]) -> str:
    ranking = check.get("贡献度排名") or []
    if not ranking:
        return "待核对"
    top = ranking[0]
    return f"{top.get('触点')} 贡献占比最高（{top.get('贡献占比')}），是当前波动主要根因"


def current_recalc_number(metric_name: str, model_number: str = "", version: str = "") -> str:
    """取某指标当前已落定的复算编号；历史已确认分析优先用自身快照。"""
    for check in store.rows(CHECK_MODULE):
        if check.get("目标指标") != metric_name:
            continue
        if not check.get("已落定"):
            continue
        if model_number and check.get("归因模型编号") != model_number:
            continue
        if version and check.get("模型版本") != version:
            continue
        return str(check.get("复算编号") or "")
    return ""


def find_metric_entry(
    metric_name: str, model_number: str = "", entry_id: int | None = None
) -> dict[str, Any] | None:
    """同一目标指标可能有多条分析记录：优先取已绑定该模型的、再按分析进度取一条。"""
    if entry_id:
        found = store.find(METRIC_MODULE, entry_id)
        if found is not None:
            return found
    rows = [row for row in store.rows(METRIC_MODULE) if row.get("目标指标") == metric_name]
    if not rows:
        return None
    if model_number:
        bound = [row for row in rows if row.get("归因模型编号") == model_number]
        if bound:
            return bound[0]
        for row in rows:
            if any(
                item.get("归因模型编号") == model_number and item.get("指标分析编号") == row.get("分析编号")
                for item in store.rows(MODULE)
            ):
                return row
    for preferred in ("已确认", "分析中", "草稿", "已归档"):
        hit = [row for row in rows if row.get("status") == preferred]
        if hit:
            return hit[0]
    return rows[0]


def find_metric_by_number(analysis_number: str) -> dict[str, Any] | None:
    if not analysis_number:
        return None
    for row in store.rows(METRIC_MODULE):
        if row.get("分析编号") == analysis_number:
            return row
    return None


class AttributionRecalcService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        metric: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(reversed(store.rows(MODULE)))
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("复算编号", ""))
                or keyword in str(row.get("批次编号", ""))
                or keyword in str(row.get("归因模型编号", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("状态") == status]
        if metric:
            rows = [row for row in rows if metric in str(row.get("目标指标", ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        enriched = dict(entry)
        enriched["明细表"] = self.details_for(entry_id)
        enriched["核对表"] = self.check_for(entry_id)
        enriched["触点权重配置"] = self._model_config(entry)
        return enriched

    def details_for(self, entry_id: int) -> list[dict[str, Any]]:
        header = store.find(MODULE, entry_id)
        number = str((header or {}).get("复算编号") or "")
        rows = [row for row in store.rows(DETAIL_MODULE) if row.get("复算编号") == number]
        return sorted(rows, key=lambda row: int(row.get("贡献度排名") or 0))

    def check_for(self, entry_id: int) -> dict[str, Any] | None:
        header = store.find(MODULE, entry_id)
        number = str((header or {}).get("复算编号") or "")
        for row in store.rows(CHECK_MODULE):
            if row.get("复算编号") == number:
                return row
        return None

    def list_details(
        self, recalc_number: str | None = None, metric: str | None = None
    ) -> list[dict[str, Any]]:
        rows = list(store.rows(DETAIL_MODULE))
        if recalc_number:
            rows = [row for row in rows if recalc_number in str(row.get("复算编号", ""))]
        if metric:
            rows = [row for row in rows if metric in str(row.get("目标指标", ""))]
        return sorted(
            rows,
            key=lambda row: (str(row.get("复算编号", "")), int(row.get("贡献度排名") or 0)),
        )

    def list_checks(
        self, *, settled_only: bool = False, metric: str | None = None
    ) -> list[dict[str, Any]]:
        rows = [row for row in store.rows(CHECK_MODULE)]
        if settled_only:
            rows = [row for row in rows if row.get("已落定")]
        if metric:
            rows = [row for row in rows if metric in str(row.get("目标指标", ""))]
        return list(reversed(rows))

    def _model_config(self, entry: dict[str, Any]) -> dict[str, Any]:
        model = self._find_model(str(entry.get("归因模型编号") or ""))
        if model is None:
            return {}
        return read_weight_scope(model)

    def _find_model(self, model_number: str) -> dict[str, Any] | None:
        for row in store.rows("attrmodel"):
            if row.get("模型编号") == model_number:
                return row
        return None

    def _find_metric(self, metric_entry_id: int | None = None, metric_name: str = "", model_number: str = "") -> dict[str, Any] | None:
        return find_metric_entry(metric_name, model_number, metric_entry_id)

    def create_entry(self, values: dict[str, Any], operator: str = "值班管理员") -> tuple[dict[str, Any] | None, str, list[str]]:
        """新增复算批次：接收模型编号、目标指标与可选的事件记录，按新字段范围读配置。"""
        missing = [
            field for field in ["归因模型编号", "目标指标"]
            if not str(values.get(field) or "").strip()
        ]
        if missing:
            return None, "缺少必填字段：" + "、".join(missing), missing
        model_number = str(values.get("归因模型编号")).strip()
        model = self._find_model(model_number)
        if model is None:
            return None, f"归因模型 {model_number} 不存在或已归档", ["归因模型编号"]
        if model.get("status") == "已归档":
            return None, f"归因模型 {model_number} 已归档，不能发起复算", ["归因模型编号"]
        metric_name = str(values.get("目标指标")).strip()
        metric_entry = self._find_metric(metric_name=metric_name, model_number=model_number)
        errors = self._ingest_events(values.get("事件记录"), metric_name)
        if errors:
            return None, "；".join(errors), ["事件记录"]
        result = compute(model, metric_name, metric_entry)
        rows = store.rows(MODULE)
        number = f"ATTRECALC-{max((int(row.get('序号') or 0) for row in rows), default=0) + 1:04d}"
        batch = f"BATCH-{datetime.now().strftime('%Y%m%d')}-{len(rows) + 1:02d}"
        header = {
            "id": store.next_id(MODULE),
            "序号": int(number.split("-")[-1]),
            "复算编号": number,
            "批次编号": batch,
            "归因模型编号": model_number,
            "模型版本": str(model.get("版本号") or ""),
            "目标指标": metric_name,
            "指标分析编号": str((metric_entry or {}).get("分析编号") or ""),
            "数据窗口开始": result.window_start,
            "数据窗口结束": result.window_end,
            "状态": "复算完成" if result.ok else "待复算",
            "核对结果": CHECK_PENDING if result.ok else CHECK_BLOCKED,
            "边界状态": result.boundary,
            "贡献率合计": result.contribution_sum,
            "触点贡献值合计": result.total_value,
            "明细数": len(result.details),
            "触发方式": "新增入口",
            "创建时间": _now(),
            "落定时间": "",
            "来源映射": result.source_map,
            "备注": "；".join(result.errors) if result.errors else "复算通过，待落定核对",
            "pending": True,
            "abnormal": not result.ok,
        }
        store.append(MODULE, header)
        self._write_details(header, result)
        self._write_check(header, result, settled=False)
        return self.get_entry(header["id"]), "", []

    def _ingest_events(self, events: Any, metric_name: str) -> list[str]:
        if events in (None, ""):
            return []
        if not isinstance(events, list):
            return ["事件记录应为数组"]
        errors: list[str] = []
        for index, event in enumerate(events, start=1):
            if not isinstance(event, dict):
                errors.append(f"事件记录第 {index} 条不是对象")
                continue
            touch = str(event.get("触点") or "").strip()
            if not touch:
                errors.append(f"事件记录第 {index} 条缺少触点")
            try:
                float(event.get("转化值"))
            except (TypeError, ValueError):
                errors.append(f"事件记录第 {index} 条转化值不是数字")
            if not str(event.get("事件时间") or "").strip():
                errors.append(f"事件记录第 {index} 条缺少事件时间")
        if errors:
            return errors
        for event in events:
            store.append(EVENT_MODULE, {
                "id": store.next_id(EVENT_MODULE),
                "目标指标": metric_name,
                "触点": str(event.get("触点")).strip(),
                "事件类型": str(event.get("事件类型") or "转化"),
                "转化值": float(event.get("转化值")),
                "事件时间": str(event.get("事件时间")).strip(),
                "来源": "触点贡献复算登记",
            })
        return []

    def _write_details(self, header: dict[str, Any], result: ComputeResult) -> None:
        for detail in result.details:
            row = dict(detail)
            row["id"] = store.next_id(DETAIL_MODULE)
            row["复算编号"] = header["复算编号"]
            row["批次编号"] = header["批次编号"]
            store.append(DETAIL_MODULE, row)

    def _write_check(
        self, header: dict[str, Any], result: ComputeResult, *, settled: bool
    ) -> dict[str, Any]:
        existing = self.check_for(int(header["id"]))
        check = existing or {
            "id": store.next_id(CHECK_MODULE),
            "核对编号": f"CHECK-{header['复算编号'].split('-')[-1]}",
            "复算编号": header["复算编号"],
            "批次编号": header["批次编号"],
            "目标指标": header["目标指标"],
            "指标分析编号": header["指标分析编号"],
            "归因模型编号": header["归因模型编号"],
            "模型版本": header["模型版本"],
        }
        check.update({
            "贡献度排名": result.ranking,
            "贡献度排名文本": ranking_text(result.ranking),
            "贡献率合计": result.contribution_sum,
            "边界状态": result.boundary,
            "核对状态": CHECK_SETTLED if settled and result.ok else (CHECK_BLOCKED if not result.ok else CHECK_PENDING),
            "已落定": bool(settled and result.ok),
            "落定时间": _now() if settled and result.ok else "",
            "明细数": len(result.details),
            "来源映射": result.source_map,
            "异常说明": "" if result.ok else "；".join(result.errors),
        })
        if existing is None:
            store.append(CHECK_MODULE, check)
        return check

    def run_action(
        self, entry_id: int, action: str, operator: str = "值班管理员"
    ) -> tuple[dict[str, Any] | None, str]:
        entry, message = self._run_action(entry_id, action, operator)
        if entry is None:
            return None, message
        return self.get_entry(int(entry["id"])), message

    def _run_action(
        self, entry_id: int, action: str, operator: str = "值班管理员"
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"触点贡献复算 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于触点贡献复算可执行范围"
        model = self._find_model(str(entry.get("归因模型编号") or ""))
        if model is None:
            return None, f"归因模型 {entry.get('归因模型编号')} 不存在或已归档"
        metric_entry = find_metric_by_number(str(entry.get("指标分析编号") or "")) or self._find_metric(
            metric_name=str(entry.get("目标指标") or ""),
            model_number=str(entry.get("归因模型编号") or ""),
        )
        result = compute(model, str(entry.get("目标指标") or ""), metric_entry)
        if action == "执行复算":
            if entry.get("状态") == "已归档":
                return None, "已归档的复算批次不能再执行"
            if entry.get("状态") == "已落定":
                return None, "已落定的复算批次不能重跑，避免并发核对读到过期结论；如需重算请新建批次"
            self._replace_details(entry, result)
            self._write_check(entry, result, settled=False)
            entry["状态"] = "复算完成" if result.ok else "待复算"
            entry["核对结果"] = CHECK_PENDING if result.ok else CHECK_BLOCKED
            entry["边界状态"] = result.boundary
            entry["贡献率合计"] = result.contribution_sum
            entry["触点贡献值合计"] = result.total_value
            entry["明细数"] = len(result.details)
            entry["来源映射"] = result.source_map
            entry["备注"] = "；".join(result.errors) if result.errors else "复算通过，待落定核对"
            entry["abnormal"] = not result.ok
            entry["pending"] = True
            return entry, (
                "触点贡献复算已执行" if result.ok else f"复算存在阻断项：{result.message}"
            )
        if action == "落定核对":
            if entry.get("状态") == "已归档":
                return None, "已归档的复算批次不能再落定"
            if not result.ok:
                return None, f"贡献率或配置越界，核对表不能落定：{result.message}"
            self._replace_details(entry, result)
            check = self._write_check(entry, result, settled=True)
            entry["状态"] = "已落定"
            entry["核对结果"] = CHECK_SETTLED
            entry["边界状态"] = result.boundary
            entry["贡献率合计"] = result.contribution_sum
            entry["触点贡献值合计"] = result.total_value
            entry["明细数"] = len(result.details)
            entry["来源映射"] = result.source_map
            entry["落定时间"] = _now()
            entry["备注"] = "核对表已落定，可供指标下钻与并发核对引用"
            entry["pending"] = False
            entry["abnormal"] = False
            self._register_pending(entry, check, operator)
            return entry, "触点贡献复算核对表已落定并登记到指标下钻待处理明细"
        # 归档复算
        if entry.get("状态") != "已落定":
            return None, "只有已落定的复算批次才能归档，避免未核对结果被隐藏"
        entry["状态"] = "已归档"
        entry["pending"] = False
        entry["abnormal"] = False
        entry["备注"] = "复算批次已归档，历史核对表保留只读"
        return entry, "触点贡献复算已归档"

    def _replace_details(self, header: dict[str, Any], result: ComputeResult) -> None:
        number = str(header.get("复算编号") or "")
        rows = store.rows(DETAIL_MODULE)
        store.replace_rows(DETAIL_MODULE, [row for row in rows if row.get("复算编号") != number])
        self._write_details(header, result)

    def _register_pending(
        self, header: dict[str, Any], check: dict[str, Any], operator: str
    ) -> dict[str, Any]:
        metric_entry = find_metric_by_number(str(header.get("指标分析编号") or "")) or self._find_metric(
            metric_name=str(header.get("目标指标") or ""),
            model_number=str(header.get("归因模型编号") or ""),
        )
        row = {
            "id": store.next_id(METRIC_MODULE + "_pending"),
            "指标分析编号": header.get("指标分析编号"),
            "目标指标": header.get("目标指标"),
            "核对编号": check.get("核对编号"),
            "复算编号": header.get("复算编号"),
            "批次编号": header.get("批次编号"),
            "归因模型编号": header.get("归因模型编号"),
            "模型版本": header.get("模型版本"),
            "发布批次": str((self._find_model(str(header.get("归因模型编号") or "")) or {}).get("发布批次") or ""),
            "贡献度排名文本": check.get("贡献度排名文本"),
            "贡献率合计": check.get("贡献率合计"),
            "边界状态": check.get("边界状态"),
            "核对状态": check.get("核对状态"),
            "登记时间": _now(),
            "操作人": operator,
            "说明": "触点贡献复算核对表已落定，供指标下钻待处理明细引用",
        }
        store.append(METRIC_MODULE + "_pending", row)
        if metric_entry is not None:
            metric_entry["复算核对编号"] = header.get("复算编号")
            if not metric_entry.get("归因模型编号"):
                # 未绑定模型的分析记录由登记入口带回当前核对表结论；
                # 已绑定其他模型时不覆盖，必须走显式切换。
                metric_entry["归因模型编号"] = header.get("归因模型编号")
                metric_entry["模型版本"] = header.get("模型版本")
                metric_entry["贡献度排名"] = check.get("贡献度排名文本") or metric_entry.get("贡献度排名")
                metric_entry["根因假设"] = root_cause_text(check)
        return row

    def verify_concurrency(self, metric_name: str, model_number: str = "") -> dict[str, Any]:
        """并发核对：只采信已落定的核对表，未落定/越界批次一律不参与结论。"""
        checks = [row for row in store.rows(CHECK_MODULE) if row.get("目标指标") == metric_name]
        settled = [row for row in checks if row.get("已落定")]
        ignored = [
            {
                "复算编号": row.get("复算编号"),
                "归因模型编号": row.get("归因模型编号"),
                "模型版本": row.get("模型版本"),
                "核对状态": row.get("核对状态"),
                "原因": "核对表未落定，不参与并发核对",
            }
            for row in checks if not row.get("已落定")
        ]
        current = None
        bound = [
            row for row in store.rows(METRIC_MODULE)
            if row.get("目标指标") == metric_name and row.get("归因模型编号")
        ]
        for preferred in ("已确认", "分析中", "草稿"):
            hit = [row for row in bound if row.get("status") == preferred]
            if hit:
                current = hit[0]
                break
        if current is None and bound:
            current = bound[0]
        adopted = None
        if current is not None:
            for row in settled:
                if (
                    row.get("归因模型编号") == current.get("归因模型编号")
                    and row.get("模型版本") == current.get("模型版本")
                ):
                    adopted = row
                    break
        if adopted is None and settled:
            adopted = max(settled, key=lambda row: str(row.get("落定时间") or ""))
        versions = {
            (row.get("归因模型编号"), row.get("模型版本")) for row in settled
        }
        conflict = len(versions) > 1
        return {
            "目标指标": metric_name,
            "采用规则": "并发核对只采用已落定触点贡献复算核对表",
            "当前引用模型": f"{current.get('归因模型编号') or '未绑定'} {current.get('模型版本') or ''}".strip() if current else "未绑定",
            "是否冲突": conflict,
            "冲突说明": (
                "同一目标指标存在多个已落定模型版本，当前指标只引用一个归因版本，需显式切换"
                if conflict else "已落定核对表版本一致或仅有一套"
            ),
            "采用核对表": adopted,
            "已落定核对表": sorted(settled, key=lambda row: str(row.get("落定时间") or ""), reverse=True),
            "未采用批次": ignored,
        }

    def weight_scope(self, model_number: str) -> tuple[dict[str, Any] | None, str]:
        model = self._find_model(model_number)
        if model is None:
            return None, f"归因模型 {model_number} 不存在或已归档"
        scope = read_weight_scope(model)
        scope["说明"] = SOURCE_NOTE
        scope["解析结果"] = [
            {"触点": item.name, "权重": item.weight}
            for item in parse_weight_config(model.get("触点权重配置"))[0]
        ]
        scope["解析错误"] = parse_weight_config(model.get("触点权重配置"))[1]
        return scope, ""

    # 供归因建模发布时复用：先复算校验，再一起提交状态与复算结果。
    def prepare(self, model: dict[str, Any], metric_entry: dict[str, Any] | None) -> ComputeResult:
        return compute(model, str(model.get("目标指标") or ""), metric_entry)

    def persist(
        self,
        model: dict[str, Any],
        result: ComputeResult,
        batch: str,
        operator: str,
        metric_entry: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rows = store.rows(MODULE)
        number = f"ATTRECALC-{max((int(row.get('序号') or 0) for row in rows), default=0) + 1:04d}"
        metric_entry = metric_entry or find_metric_entry(
            str(model.get("目标指标") or ""), str(model.get("模型编号") or "")
        )
        header = {
            "id": store.next_id(MODULE),
            "序号": int(number.split("-")[-1]),
            "复算编号": number,
            "批次编号": batch,
            "归因模型编号": str(model.get("模型编号") or ""),
            "模型版本": str(model.get("版本号") or ""),
            "目标指标": str(model.get("目标指标") or ""),
            "指标分析编号": str((metric_entry or {}).get("分析编号") or ""),
            "数据窗口开始": result.window_start,
            "数据窗口结束": result.window_end,
            "状态": "已落定",
            "核对结果": CHECK_SETTLED,
            "边界状态": result.boundary,
            "贡献率合计": result.contribution_sum,
            "触点贡献值合计": result.total_value,
            "明细数": len(result.details),
            "触发方式": "模型发布前复算",
            "创建时间": _now(),
            "落定时间": _now(),
            "来源映射": result.source_map,
            "备注": "模型发布前复算通过，核对表已落定",
            "pending": False,
            "abnormal": False,
        }
        store.append(MODULE, header)
        self._write_details(header, result)
        self._write_check(header, result, settled=True)
        return header

    def register_check(self, entry: dict[str, Any], metric_entry: dict[str, Any], operator: str) -> dict[str, Any]:
        check = self.check_for(int(entry["id"]))
        if check is None or not check.get("已落定"):
            raise ValueError("复算批次未落定，不能登记到指标下钻待处理明细")
        return self._register_pending(entry, check, operator)
