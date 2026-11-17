"""指标归因业务规则：状态流转、引用归因版本快照、待处理明细与历史只读口径。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store
from app.services.attributionrecalc import BOUNDARY_OK, root_cause_text

MODULE = "metricdrill"
PENDING_MODULE = "metricdrill_pending"
REQUIRED_FIELDS = ["分析编号", "目标指标", "波动方向"]
STATUS_ORDER = ["草稿", "分析中", "已确认", "已归档"]
ACTION_RULES = {"发起分析": "分析中", "确认结论": "已确认", "归档分析": "已归档"}
FROZEN_STATUSES = ["已确认", "已归档"]
FROZEN_FIELDS = ["贡献度排名", "根因假设", "归因模型编号", "模型版本", "归因快照", "验证结果"]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class MetricdrillService:
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
                if keyword in str(row.get("分析编号", "")) or keyword in str(row.get("目标指标", ""))
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
        enriched["根因版本"] = self._root_cause_versions(entry)
        enriched["历史查看口径"] = (
            "按确认时的归因快照只读查看" if entry.get("status") in FROZEN_STATUSES else "按当前引用模型查看"
        )
        return enriched

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in ["波动幅度", "维度下钻路径", "验证结果", "数据窗口开始", "数据窗口结束"]:
            if values.get(field) not in (None, ""):
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["贡献度排名"] = values.get("贡献度排名") or ""
        entry["根因假设"] = values.get("根因假设") or ""
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"归因分析 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于指标归因可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if action == "确认结论":
            return self._confirm(entry)
        if action == "发起分析" and entry.get("status") == "已归档":
            return None, "已归档的归因分析不能重新发起"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = False
        return entry, f"归因分析已{action}"

    def _confirm(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        current = str(entry.get("status") or "")
        if current == "已归档":
            return None, "已归档的归因分析不能确认结论"
        check = self._current_check(entry)
        if check is None:
            return None, "没有已落定的触点贡献复算核对表，当前指标结论不能确认"
        versions = self._root_cause_versions(entry)
        if len(versions) > 1 and not str(entry.get("归因模型编号") or "").strip():
            return None, (
                "同一目标指标收到多个已评审归因模型版本，当前指标只能引用一个归因版本，"
                "请先显式切换引用再确认结论"
            )
        model_number = str(entry.get("归因模型编号") or check.get("归因模型编号") or "")
        version = str(entry.get("模型版本") or check.get("模型版本") or "")
        entry["归因模型编号"] = model_number
        entry["模型版本"] = version
        entry["复算核对编号"] = check.get("复算编号")
        entry["贡献度排名"] = check.get("贡献度排名文本")
        entry["根因假设"] = root_cause_text(check)
        entry["根因版本清单"] = [f"{model_number} {version}".strip()]
        entry["归因快照"] = {
            "归因模型编号": model_number,
            "模型版本": version,
            "贡献度排名": check.get("贡献度排名"),
            "核对编号": check.get("核对编号"),
            "复算编号": check.get("复算编号"),
            "确认时间": _now(),
        }
        entry["归因建模状态"] = self._model_status(model_number)
        entry["status"] = "已确认"
        entry["pending"] = False
        entry["abnormal"] = False
        entry["确认时间"] = _now()
        return entry, f"指标结论已确认，引用归因模型 {model_number} {version}"

    def _model_status(self, model_number: str) -> str:
        for row in store.rows("attrmodel"):
            if row.get("模型编号") == model_number:
                return str(row.get("status") or "")
        return ""

    def _current_check(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        metric_name = str(entry.get("目标指标") or "")
        model_number = str(entry.get("归因模型编号") or "")
        version = str(entry.get("模型版本") or "")
        settled = [row for row in store.rows("attributionrecalc_check") if row.get("已落定")]
        for row in settled:
            if row.get("目标指标") != metric_name:
                continue
            if model_number and row.get("归因模型编号") != model_number:
                continue
            if version and row.get("模型版本") != version:
                continue
            return row
        return None

    def _root_cause_versions(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        """同一目标指标下所有已评审/已发布的候选模型版本，用于识别双版本冲突。

        优先按模型配置的目标指标匹配；配置缺失时退回按指标分析编号关联的复算批次匹配。
        """
        metric_name = str(entry.get("目标指标") or "")
        if not metric_name:
            return []
        analysis_number = str(entry.get("分析编号") or "")
        recalc_numbers = {
            str(row.get("归因模型编号"))
            for row in store.rows("attributionrecalc")
            if analysis_number and row.get("指标分析编号") == analysis_number
        }
        rows: list[dict[str, Any]] = []
        for model in store.rows("attrmodel"):
            if model.get("status") not in ("已评审", "已发布"):
                continue
            same_metric = model.get("目标指标") == metric_name
            same_analysis = str(model.get("模型编号")) in recalc_numbers
            if not same_metric and not same_analysis:
                continue
            rows.append({
                "归因模型编号": model.get("模型编号"),
                "模型版本": model.get("版本号"),
                "状态": model.get("status"),
                "评审完成时间": model.get("评审完成时间"),
                "数据窗口": f"{model.get('数据窗口开始') or '-'} ~ {model.get('数据窗口结束') or '-'}",
                "当前引用": (
                    model.get("模型编号") == entry.get("归因模型编号")
                    and str(model.get("版本号") or "") == str(entry.get("模型版本") or "")
                ),
            })
        return sorted(rows, key=lambda row: str(row.get("评审完成时间") or ""), reverse=True)


    def pending_details(self, metric: str | None = None) -> list[dict[str, Any]]:
        rows = list(store.rows(PENDING_MODULE))
        if metric:
            rows = [row for row in rows if metric in str(row.get("目标指标", ""))]
        return list(reversed(rows))

    def register_pending(self, values: dict[str, Any], operator: str = "值班管理员") -> tuple[dict[str, Any] | None, str]:
        recalc_number = str(values.get("复算编号") or "").strip()
        if not recalc_number:
            return None, "缺少复算编号"
        header = None
        for row in store.rows("attributionrecalc"):
            if row.get("复算编号") == recalc_number:
                header = row
                break
        if header is None:
            return None, f"触点贡献复算 {recalc_number} 不存在"
        if header.get("状态") not in ("已落定", "已归档"):
            return None, "触点贡献复算核对表尚未落定，不能登记到指标下钻待处理明细"
        if header.get("边界状态") != BOUNDARY_OK:
            return None, "贡献率越界的复算结果不能登记为当前指标结论"
        metric_name = str(header.get("目标指标") or "")
        entry = self._find_metric(metric_name)
        if entry is None:
            return None, f"目标指标 {metric_name} 没有对应的归因分析记录"
        from app.services.attributionrecalc import AttributionRecalcService

        row = AttributionRecalcService().register_check(header, entry, operator)
        return row, "已登记到指标下钻待处理明细"
    def _find_metric(self, metric_name: str) -> dict[str, Any] | None:
        from app.services.attributionrecalc import find_metric_entry

        return find_metric_entry(metric_name)
