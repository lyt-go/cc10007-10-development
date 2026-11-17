"""归因建模业务规则：生命周期流转、模型发布前复算、变更切换与归档恢复留痕。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.attributionrecalc import (
    METRIC_MODULE,
    AttributionRecalcService,
    current_recalc_number,
    find_metric_by_number,
    parse_weight_config,
    root_cause_text,
)
from app.store import store

MODULE = "attrmodel"
REQUIRED_FIELDS = ["模型编号", "模型名称", "归因逻辑"]
PUBLISH_FIELDS = ["目标指标", "触点权重配置", "数据窗口开始", "数据窗口结束", "版本号"]
STATUS_ORDER = ["草稿", "模型评审中", "已评审", "已发布", "已下线", "已归档"]
ACTION_LABELS = {
    "提交模型评审": "已提交模型评审",
    "完成模型评审": "已完成模型评审",
    "发布模型": "已发布",
    "下线模型": "已下线",
    "归档模型": "已归档",
}
ACTION_RULES = {
    "提交模型评审": "模型评审中",
    "完成模型评审": "已评审",
    "发布模型": "已发布",
    "下线模型": "已下线",
    "归档模型": "已归档",
}
ALLOWED_FROM = {
    "提交模型评审": ["草稿"],
    "完成模型评审": ["模型评审中"],
    "发布模型": ["已评审"],
    "下线模型": ["已发布"],
    "归档模型": ["已下线", "已评审"],
}
NEGATIVE_ACTIONS = ["下线模型", "归档模型"]
FROZEN_STATUSES = ["已确认", "已归档"]
recalc_service = AttributionRecalcService()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class AttrmodelService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("模型编号", "")) or keyword in str(row.get("模型名称", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        enriched = dict(entry)
        enriched["触点权重解析"] = self._weight_preview(entry)
        enriched["变更留痕"] = list(entry.get("变更留痕") or [])
        enriched["下游引用"] = self._references(str(entry.get("模型编号") or ""), str(entry.get("版本号") or ""))
        return enriched

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in [
            "目标指标", "触点权重配置", "衰减天数", "转化窗口", "直接贡献率", "间接贡献率",
            "版本号", "数据窗口开始", "数据窗口结束",
        ]:
            if values.get(field) not in (None, ""):
                entry[field] = values.get(field)
        entry.setdefault("版本号", "V1")
        entry["触点权重分布"] = values.get("触点权重分布") or values.get("触点权重配置")
        entry["贡献率下限"] = values.get("贡献率下限", 0.0)
        entry["贡献率上限"] = values.get("贡献率上限", 1.0)
        entry["status"] = STATUS_ORDER[0]
        entry["评审完成时间"] = ""
        entry["发布批次"] = ""
        entry["发布复算编号"] = ""
        entry["pending"] = True
        entry["abnormal"] = False
        entry["变更留痕"] = [self._trace("登记模型", "", STATUS_ORDER[0], "登记归因模型草稿")]
        rows.append(entry)
        return entry, []

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None, operator: str = "值班管理员"
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"归因模型 {entry_id} 不存在或已归档"
        values = values or {}
        if action == "恢复模型":
            return self._restore(entry, operator)
        if action == "切换指标引用":
            return self._switch_metric(entry, values, operator)
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于归因建模可执行范围"
        target = ACTION_RULES[action]
        current = str(entry.get("status") or "")
        allowed = ALLOWED_FROM.get(action)
        if allowed and current not in allowed:
            return None, f"模型当前状态为「{current}」，不能执行「{action}」"
        if action == "发布模型":
            return self._publish(entry, operator)
        entry["status"] = target
        entry["pending"] = target not in ("已下线", "已归档")
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        if action == "完成模型评审":
            entry["评审完成时间"] = _now()
            entry["评审结论"] = "通过"
        if action == "归档模型":
            entry["归档前状态"] = current
        entry["变更留痕"] = list(entry.get("变更留痕") or []) + [
            self._trace(action, current, target, f"归因模型{ACTION_LABELS.get(action, action)}", operator)
        ]
        store.append("attribution_audit", {
            "id": store.next_id("attribution_audit"),
            "时间": _now(),
            "对象": "归因模型",
            "对象编号": entry.get("模型编号"),
            "动作": action,
            "原状态": current,
            "新状态": target,
            "操作人": operator,
            "说明": f"归因模型{ACTION_LABELS.get(action, action)}",
        })
        return entry, f"归因模型{ACTION_LABELS.get(action, action)}"

    def _publish(self, entry: dict[str, Any], operator: str) -> tuple[dict[str, Any] | None, str]:
        missing = [field for field in PUBLISH_FIELDS if str(entry.get(field) or "").strip() == ""]
        if missing:
            return None, f"发布前缺少复算字段：{'、'.join(missing)}，模型与复算不能一起成功"
        weights, errors = parse_weight_config(entry.get("触点权重配置"))
        if errors:
            self._record_failed_publish(entry, "；".join(errors), operator)
            return None, f"发布前复算未通过，模型仍保持「{entry.get('status')}」：{'；'.join(errors)}"
        metric_name = str(entry.get("目标指标") or "")
        metric_entries = self._metric_entries(metric_name)
        metric_entry = metric_entries[0] if metric_entries else None
        result = recalc_service.prepare(entry, metric_entry)
        if not result.ok:
            self._record_failed_publish(entry, "；".join(result.errors), operator)
            return None, f"发布前复算未通过，模型仍保持「{entry.get('status')}」：{result.message}"
        batch = f"BATCH-{datetime.now().strftime('%Y%m%d')}-{len(store.rows('attributionrecalc')) + 1:02d}"
        header = recalc_service.persist(entry, result, batch, operator, metric_entry)
        current = str(entry.get("status") or "")
        entry["status"] = "已发布"
        entry["pending"] = False
        entry["abnormal"] = False
        entry["发布批次"] = batch
        entry["发布复算编号"] = header["复算编号"]
        entry["发布指标引用"] = "待切换"
        trace_note = f"模型发布与触点贡献复算 {header['复算编号']} 同时成功"
        if len(metric_entries) != 1:
            trace_note += (
                "；目标指标存在多套指标分析记录，当前引用保持不变，需在候选模型中显式切换"
                if metric_entries else "；目标指标没有对应的指标分析记录，发布后可再登记核对表"
            )
        else:
            metric_entry = metric_entries[0]
            bound_model = str(metric_entry.get("归因模型编号") or "")
            status = str(metric_entry.get("status") or "")
            if status in FROZEN_STATUSES:
                trace_note += "；目标指标已确认，历史归因按原模型只读查看，本次发布不覆盖结论"
            elif bound_model and bound_model != str(entry.get("模型编号")):
                trace_note += "；目标指标已有引用模型，本次发布不覆盖结论，需人工切换"
            elif not bound_model:
                self._bind_metric(entry, metric_entry, header, operator)
                entry["发布指标引用"] = "已绑定"
                trace_note += f"；已绑定目标指标 {metric_entry.get('目标指标')}"
            else:
                entry["发布指标引用"] = "已绑定"
        entry["变更留痕"] = list(entry.get("变更留痕") or []) + [
            self._trace(
                "发布模型", current, "已发布", trace_note, operator,
                recalc_number=str(header.get("复算编号")), batch=batch,
            )
        ]
        store.append("attribution_audit", {
            "id": store.next_id("attribution_audit"),
            "时间": _now(),
            "对象": "归因模型",
            "对象编号": entry.get("模型编号"),
            "动作": "发布模型",
            "原状态": current,
            "新状态": "已发布",
            "操作人": operator,
            "说明": trace_note,
            "复算编号": header.get("复算编号"),
        })
        return entry, f"模型已发布，触点贡献复算 {header['复算编号']} 已落定"

    def _record_failed_publish(self, entry: dict[str, Any], reason: str, operator: str) -> None:
        store.append("attribution_audit", {
            "id": store.next_id("attribution_audit"),
            "时间": _now(),
            "对象": "归因模型",
            "对象编号": entry.get("模型编号"),
            "动作": "发布模型",
            "原状态": entry.get("status"),
            "新状态": entry.get("status"),
            "操作人": operator,
            "说明": f"发布被复算阻断：{reason}",
        })

    def _bind_metric(
        self, entry: dict[str, Any], metric_entry: dict[str, Any], header: dict[str, Any], operator: str
    ) -> None:
        status = str(metric_entry.get("status") or "")
        if status in FROZEN_STATUSES:
            raise ValueError("已确认的历史归因必须按原模型查看，不能切换引用")
        check = recalc_service.check_for(int(header["id"]))
        if check is not None:
            recalc_service.register_check(header, metric_entry, operator)
        metric_entry["归因模型编号"] = entry.get("模型编号")
        metric_entry["模型版本"] = entry.get("版本号")
        metric_entry["归因建模状态"] = entry.get("status")
        metric_entry["复算核对编号"] = header.get("复算编号")
        metric_entry["贡献度排名"] = (check or {}).get("贡献度排名文本") or metric_entry.get("贡献度排名")
        metric_entry["归因快照"] = {
            "归因模型编号": entry.get("模型编号"),
            "模型版本": entry.get("版本号"),
            "贡献度排名": (check or {}).get("贡献度排名"),
            "核对编号": (check or {}).get("核对编号"),
            "复算编号": header.get("复算编号"),
        }

    def _switch_metric(
        self, entry: dict[str, Any], values: dict[str, Any], operator: str
    ) -> tuple[dict[str, Any] | None, str]:
        if entry.get("status") != "已发布":
            return None, "只有已发布的归因模型才能被切换引用"
        recalc_number = str(entry.get("发布复算编号") or "")
        if not recalc_number:
            return None, "当前模型没有发布前复算记录，不能切换指标引用"
        check = None
        for row in store.rows("attributionrecalc_check"):
            if row.get("复算编号") == recalc_number:
                check = row
                break
        if check is None or not check.get("已落定"):
            return None, "触点贡献复算核对表尚未落定，不能切换指标引用"
        metric_entry = self._pick_metric(values)
        if metric_entry is None:
            return None, "未找到可切换的目标指标分析记录"
        status = str(metric_entry.get("status") or "")
        if status in FROZEN_STATUSES:
            return None, "已确认或已归档的历史归因按原模型查看，不能切换引用"
        if str(metric_entry.get("目标指标") or "") != str(entry.get("目标指标") or ""):
            return None, "归因模型的目标指标与分析记录不一致，不能切换"
        previous = f"{metric_entry.get('归因模型编号') or '未绑定'} {metric_entry.get('模型版本') or ''}".strip()
        metric_entry["归因模型编号"] = entry.get("模型编号")
        metric_entry["模型版本"] = entry.get("版本号")
        metric_entry["归因建模状态"] = entry.get("status")
        metric_entry["复算核对编号"] = check.get("复算编号")
        metric_entry["贡献度排名"] = check.get("贡献度排名文本")
        metric_entry["根因假设"] = root_cause_text(check)
        metric_entry["归因快照"] = {
            "归因模型编号": entry.get("模型编号"),
            "模型版本": entry.get("版本号"),
            "贡献度排名": check.get("贡献度排名"),
            "核对编号": check.get("核对编号"),
            "复算编号": check.get("复算编号"),
        }
        header = None
        for row in store.rows("attributionrecalc"):
            if row.get("复算编号") == recalc_number:
                header = row
                break
        if header is not None:
            recalc_service.register_check(header, metric_entry, operator)
        entry["发布指标引用"] = "已绑定"
        entry["变更留痕"] = list(entry.get("变更留痕") or []) + [
            self._trace(
                "切换指标引用", str(entry.get("status")), str(entry.get("status")),
                f"指标 {metric_entry.get('目标指标')} 引用由 {previous} 切换为 "
                f"{entry.get('模型编号')} {entry.get('版本号')}",
                operator, recalc_number=recalc_number,
            )
        ]
        store.append("attribution_audit", {
            "id": store.next_id("attribution_audit"),
            "时间": _now(),
            "对象": "指标引用",
            "对象编号": metric_entry.get("分析编号"),
            "动作": "切换指标引用",
            "原状态": previous,
            "新状态": f"{entry.get('模型编号')} {entry.get('版本号')}",
            "操作人": operator,
            "说明": f"目标指标 {metric_entry.get('目标指标')} 只引用一个归因版本，切换已留痕",
            "复算编号": recalc_number,
        })
        return entry, f"指标 {metric_entry.get('目标指标')} 已切换引用 {entry.get('模型编号')} {entry.get('版本号')}"

    def _restore(self, entry: dict[str, Any], operator: str) -> tuple[dict[str, Any] | None, str]:
        if entry.get("status") != "已归档":
            return None, "只有已归档的归因模型才能恢复"
        history = list(entry.get("变更留痕") or [])
        previous = str(entry.get("归档前状态") or "已下线")
        if previous == "已归档":
            previous = "已下线"
        entry["status"] = previous
        entry["pending"] = False
        entry["abnormal"] = previous == "已下线"
        entry["变更留痕"] = history + [
            self._trace("恢复模型", "已归档", previous, f"从归档恢复为「{previous}」", operator)
        ]
        store.append("attribution_audit", {
            "id": store.next_id("attribution_audit"),
            "时间": _now(),
            "对象": "归因模型",
            "对象编号": entry.get("模型编号"),
            "动作": "恢复模型",
            "原状态": "已归档",
            "新状态": previous,
            "操作人": operator,
            "说明": "归因模型归档恢复留痕",
        })
        return entry, f"归因模型已恢复到「{previous}」"

    def weight_scope(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"归因模型 {entry_id} 不存在或已归档"
        return recalc_service.weight_scope(str(entry.get("模型编号") or ""))

    def trace(self, entry_id: int) -> list[dict[str, Any]] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return list(entry.get("变更留痕") or [])

    def candidates(self, metric: str | None = None) -> list[dict[str, Any]]:
        rows = [row for row in store.rows(MODULE) if row.get("status") == "已评审"]
        if metric:
            rows = [row for row in rows if row.get("目标指标") == metric]
        return sorted(rows, key=lambda row: str(row.get("评审完成时间") or ""), reverse=True)

    def _find_metric(self, metric_name: str) -> dict[str, Any] | None:
        for row in store.rows(METRIC_MODULE):
            if row.get("目标指标") == metric_name:
                return row
        return None

    def _metric_entries(self, metric_name: str) -> list[dict[str, Any]]:
        if not metric_name:
            return []
        return [row for row in store.rows(METRIC_MODULE) if row.get("目标指标") == metric_name]

    def _pick_metric(self, values: dict[str, Any]) -> dict[str, Any] | None:
        analysis_number = str(values.get("指标分析编号") or "").strip()
        if analysis_number:
            found = find_metric_by_number(analysis_number)
            if found is not None:
                return found
        entry_id = values.get("指标分析编号") or values.get("entry_id")
        if entry_id:
            try:
                found = store.find(METRIC_MODULE, int(entry_id))
            except (TypeError, ValueError):
                found = None
            if found is not None:
                return found
        metric_name = str(values.get("目标指标") or "").strip()
        if metric_name:
            from app.services.attributionrecalc import find_metric_entry

            return find_metric_entry(metric_name, str(values.get("归因模型编号") or ""))
        return None

    def _weight_preview(self, entry: dict[str, Any]) -> dict[str, Any]:
        items, errors = parse_weight_config(entry.get("触点权重配置"))
        return {
            "原始配置": entry.get("触点权重配置") or "",
            "触点": [{"触点": item.name, "权重": item.weight} for item in items],
            "权重合计": round(sum(item.weight for item in items), 4),
            "错误": errors,
        }

    def _references(self, model_number: str, version: str) -> list[dict[str, Any]]:
        return [
            {
                "分析编号": row.get("分析编号"),
                "目标指标": row.get("目标指标"),
                "状态": row.get("status"),
                "复算核对编号": row.get("复算核对编号") or current_recalc_number(str(row.get("目标指标") or "")),
            }
            for row in store.rows(METRIC_MODULE)
            if row.get("归因模型编号") == model_number and row.get("模型版本") in (version, None, "")
        ]


    def _trace(
        self,
        action: str,
        before: str,
        after: str,
        note: str,
        operator: str = "值班管理员",
        *,
        recalc_number: str = "",
        batch: str = "",
    ) -> dict[str, Any]:
        return {
            "时间": _now(),
            "动作": action,
            "原状态": before,
            "新状态": after,
            "操作人": operator,
            "说明": note,
            "复算编号": recalc_number,
            "批次编号": batch,
        }
