"""字段影响引擎：影响视图、指标重算与快照取数。

关键约束（对应业务规则）：
- 从发生变化的原字段最多向下追两层，逐个目标标注依据状态；
- 重算沿指标聚合入口，按入口声明的实际数据字段与分组做真实聚合；
- 存在待核输入的指标不得物化，未核金额不能先作为重算成功；
- 确认的指标才写入可供快照取数的结果存储，旧结果保留但标记为被取代；
- 不在重算范围内的指标（同表无关库存）不会被重建。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app.impact.models import (
    BASIS_SUPPORTED,
    BASIS_UNVERIFIED,
    EVIDENCE_CONFIRMED,
    EVIDENCE_PENDING,
    EVIDENCE_REVOKED,
    EVIDENCE_STATUSES,
    RESULT_BLOCKED,
    RESULT_MATERIALIZED,
    RESULT_OUT_OF_SCOPE,
    RESULT_PUBLISHED,
    RESULT_SUPERSEDED,
    Evidence,
    FactRow,
    FieldLineageEdge,
    MetricAggregationEntry,
    MetricResult,
    RecalcRun,
)
from app.impact.scenario import GROUP_TODAY, PARTITION, build_scenario


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ImpactEngine:
    """持有字段血缘、证据台账、聚合入口、事实数据与快照结果存储。"""

    def __init__(self) -> None:
        scenario = build_scenario()
        self.evidences: dict[str, Evidence] = {
            item.code: item for item in scenario["evidences"]  # type: ignore[index]
        }
        self.edges: list[FieldLineageEdge] = list(scenario["edges"])  # type: ignore[arg-type]
        self.metrics: dict[str, MetricAggregationEntry] = {
            item.code: item for item in scenario["metrics"]  # type: ignore[index]
        }
        self.facts: list[FactRow] = list(scenario["facts"])  # type: ignore[arg-type]
        self.default_roots: list[str] = list(scenario["roots"])  # type: ignore[arg-type]

        # 快照取数结果存储：(metric_code, partition) -> 按时间排列的结果
        self._result_store: dict[tuple[str, str], list[MetricResult]] = defaultdict(list)
        self._runs: list[RecalcRun] = []
        self._run_seq = 0
        self._result_seq = 0
        self.audit_log: list[dict[str, str]] = []
        self._seed_published_results()

    # ------------------------------------------------------------------
    # 初始化：已发布的金额 20、税额 5（一组本日指标）与库存 100
    # ------------------------------------------------------------------
    def _seed_published_results(self) -> None:
        for entry in self.metrics.values():
            result = MetricResult(
                result_id=f"RES-{entry.code}-PUB",
                metric_code=entry.code,
                group_code=entry.group_code,
                time_partition=entry.time_partition,
                status=RESULT_PUBLISHED,
                value=entry.published_value,
                field_path=entry.source_field_paths[0],
                evidence_versions=list(entry.published_evidence_versions),
                pending_inputs=[],
                run_id="",
                storage_table=entry.storage_table,
                note="场景初始已发布结果",
            )
            self._result_store[(entry.code, entry.time_partition)].append(result)

    # ------------------------------------------------------------------
    # 影响视图：从变化原字段向下追两层
    # ------------------------------------------------------------------
    def impact_view(self, roots: list[str] | None = None,
                    max_layers: int = 2) -> dict[str, Any]:
        roots = roots or self.default_roots
        outgoing: dict[str, list[FieldLineageEdge]] = defaultdict(list)
        for edge in self.edges:
            outgoing[edge.upstream].append(edge)

        layers: list[dict[str, Any]] = []
        reached_fields: set[str] = set(roots)
        frontier = list(roots)
        for layer in range(1, max_layers + 1):
            nodes: list[dict[str, Any]] = []
            next_frontier: list[str] = []
            seen_in_layer: set[str] = set()
            for upstream in frontier:
                for edge in outgoing.get(upstream, []):
                    if edge.layer != layer or edge.downstream in seen_in_layer:
                        continue
                    seen_in_layer.add(edge.downstream)
                    target_evidences = [
                        self.evidences[code]
                        for code in edge.evidence_codes
                        if code in self.evidences
                    ]
                    confirmed = [e for e in target_evidences if e.status == EVIDENCE_CONFIRMED]
                    pending = [e for e in target_evidences if e.status == EVIDENCE_PENDING]
                    revoked = [e for e in target_evidences if e.status == EVIDENCE_REVOKED]
                    # 仍有独立依据：至少一条已确认证据；否则暂时无完整依据。
                    basis = BASIS_SUPPORTED if confirmed else BASIS_UNVERIFIED
                    # 待核证据始终阻断；已撤销证据仅在没有确认替代时才阻断。
                    blocking = [f"{e.code}（{e.status}）" for e in pending]
                    if not confirmed:
                        blocking.extend(f"{e.code}（{e.status}）" for e in revoked)
                    nodes.append({
                        "field": edge.downstream,
                        "layer": layer,
                        "basis": basis,
                        "via_edge": edge.code,
                        "upstream": edge.upstream,
                        "transform": edge.transform,
                        "evidence": [e.to_dict() for e in target_evidences],
                        "blocking_inputs": blocking,
                    })
                    next_frontier.append(edge.downstream)
                    reached_fields.add(edge.downstream)
            layers.append({"layer": layer, "nodes": nodes})
            frontier = next_frontier

        metric_cards = self._metric_cards(reached_fields)
        return {
            "roots": roots,
            "max_layers": max_layers,
            "layers": layers,
            "metrics": metric_cards,
            "scopes": {
                "recomputable": [m["code"] for m in metric_cards if m["recomputable"]],
                "blocked": [m["code"] for m in metric_cards if m["blocking_inputs"]],
                "out_of_scope": [m["code"] for m in metric_cards if m["out_of_scope"]],
            },
        }

    def _metric_cards(self, reached_fields: set[str]) -> list[dict[str, Any]]:
        cards: list[dict[str, Any]] = []
        for entry in self.metrics.values():
            in_scope = any(
                any(node_name in path for node_name in reached_fields)
                for path in entry.source_field_paths
            )
            evidences = self._evidences_for_metric(entry.code)
            pending = [e for e in evidences if e.status == EVIDENCE_PENDING]
            revoked = [e for e in evidences if e.status == EVIDENCE_REVOKED]
            confirmed = [e for e in evidences if e.status == EVIDENCE_CONFIRMED]
            # 待核证据始终阻断；已撤销证据仅在没有确认替代时阻断。
            blocking = [f"{e.code}（{e.status}）" for e in pending]
            if not confirmed:
                blocking.extend(f"{e.code}（{e.status}）" for e in revoked)
            latest = self.latest_result(entry.code, entry.time_partition)
            cards.append({
                "code": entry.code,
                "name": entry.name,
                "group_code": entry.group_code,
                "in_scope": in_scope,
                "out_of_scope": not in_scope,
                "recomputable": in_scope and not blocking and bool(confirmed),
                "blocking_inputs": blocking if in_scope else [],
                "field_paths": entry.source_field_paths,
                "agg_function": entry.agg_function,
                "data_field": entry.data_field,
                "group_dimensions": entry.group_dimensions,
                "time_partition": entry.time_partition,
                "published_value": entry.published_value,
                "published_evidence_versions": entry.published_evidence_versions,
                "current_result": latest.to_dict() if latest else None,
            })
        return cards

    def _evidences_for_metric(self, metric_code: str) -> list[Evidence]:
        codes = {row.evidence_code for row in self.facts if row.metric_code == metric_code}
        return [self.evidences[code] for code in sorted(codes) if code in self.evidences]

    # ------------------------------------------------------------------
    # 从视图发起重算：沿指标聚合入口做真实计算
    # ------------------------------------------------------------------
    def recompute(self, metric_codes: list[str], *, group_code: str | None = None,
                  time_partition: str = PARTITION) -> RecalcRun:
        self._run_seq += 1
        run_id = f"RUN-{self._run_seq:04d}"
        results: list[MetricResult] = []
        blocked: list[str] = []
        untouched: list[str] = []

        for code in metric_codes:
            entry = self.metrics.get(code)
            if entry is None:
                continue
            evidences = self._evidences_for_metric(code)
            pending = [e for e in evidences if e.status == EVIDENCE_PENDING]
            revoked = [e for e in evidences if e.status == EVIDENCE_REVOKED]
            confirmed = [e for e in evidences if e.status == EVIDENCE_CONFIRMED]
            # 待核证据始终阻断；已撤销证据仅在没有确认替代时阻断。
            blocking_inputs = [f"{e.code}（{e.status}）" for e in pending]
            if not confirmed:
                blocking_inputs.extend(f"{e.code}（{e.status}）" for e in revoked)

            if blocking_inputs or not confirmed:
                # 待核输入未闭环：不物化，未核指标不能先作为重算成功。
                blocked.append(code)
                results.append(MetricResult(
                    result_id=self._next_result_id(code),
                    metric_code=code,
                    group_code=entry.group_code,
                    time_partition=entry.time_partition,
                    status=RESULT_BLOCKED,
                    value=None,
                    field_path=entry.source_field_paths[0],
                    evidence_versions=[e.version for e in confirmed],
                    pending_inputs=blocking_inputs,
                    run_id=run_id,
                    storage_table=entry.storage_table,
                    note="存在待核/已撤销输入，本次未物化；快照仍读取已发布结果",
                ))
                continue

            value, group_breakdown = self._aggregate(entry, confirmed)
            previous = self.latest_result(code, entry.time_partition)
            result = MetricResult(
                result_id=self._next_result_id(code),
                metric_code=code,
                group_code=entry.group_code,
                time_partition=entry.time_partition,
                status=RESULT_MATERIALIZED,
                value=value,
                field_path=entry.source_field_paths[0],
                evidence_versions=sorted(e.version for e in confirmed),
                pending_inputs=[],
                run_id=run_id,
                storage_table=entry.storage_table,
                superseded_result_id=previous.result_id if previous else None,
                note="分组聚合：" + "；".join(
                    f"{name}={val:g}" for name, val in group_breakdown.items()
                ),
            )
            if previous is not None and previous.status == RESULT_PUBLISHED:
                previous.status = RESULT_SUPERSEDED
                previous.note = f"已被 {result.result_id} 取代，旧结果仍可查"
            self._result_store[(code, entry.time_partition)].append(result)
            results.append(result)

        if group_code:
            group_members = [c for c, e in self.metrics.items() if e.group_code == group_code]
            untouched = [c for c in group_members if c not in metric_codes]

        run = RecalcRun(
            run_id=run_id,
            group_code=group_code or "",
            time_partition=time_partition,
            requested_metric_codes=list(metric_codes),
            results=results,
            blocked=blocked,
            untouched=untouched,
            created_at=_now_iso(),
        )
        self._runs.append(run)
        return run

    def _next_result_id(self, metric_code: str) -> str:
        self._result_seq += 1
        return f"RES-{metric_code}-{self._result_seq:04d}"

    def _aggregate(self, entry: MetricAggregationEntry,
                   confirmed_evidences: list[Evidence]) -> tuple[float, dict[str, float]]:
        """按聚合入口声明的数据字段与分组，对已确认证据的事实行做真实聚合。"""
        usable_codes = {e.code for e in confirmed_evidences}
        rows = [
            row for row in self.facts
            if row.metric_code == entry.code
            and row.field_name == entry.data_field
            and row.evidence_code in usable_codes
        ]
        grouped: dict[str, float] = defaultdict(float)
        for row in rows:
            grouped[row.group] += row.value
        if entry.agg_function == "SUM":
            total = sum(grouped.values())
        elif entry.agg_function == "COUNT":
            total = float(len(rows))
        else:
            raise ValueError(f"不支持的聚合函数：{entry.agg_function}")
        return total, dict(sorted(grouped.items()))

    # ------------------------------------------------------------------
    # 证据确认（补齐金额新来源）
    # ------------------------------------------------------------------
    def confirm_evidence(self, evidence_code: str) -> Evidence:
        evidence = self.evidences.get(evidence_code)
        if evidence is None:
            raise KeyError(f"证据 {evidence_code} 不存在")
        old_status = evidence.status
        if old_status not in EVIDENCE_STATUSES:
            raise ValueError(f"证据状态非法：{old_status}")
        evidence.status = EVIDENCE_CONFIRMED
        self.audit_log.append({
            "time": _now_iso(),
            "evidence_code": evidence.code,
            "evidence_version": evidence.version,
            "from_status": old_status,
            "to_status": EVIDENCE_CONFIRMED,
            "operator": "值班同事",
        })
        return evidence

    # ------------------------------------------------------------------
    # 快照取数与导出
    # ------------------------------------------------------------------
    def latest_result(self, metric_code: str,
                      time_partition: str = PARTITION) -> MetricResult | None:
        history = self._result_store.get((metric_code, time_partition), [])
        return history[-1] if history else None

    def snapshot_read(self, metric_code: str,
                      time_partition: str = PARTITION) -> dict[str, Any]:
        """快照取数：优先读取已物化的重算结果，否则回退已发布结果。"""
        entry = self.metrics.get(metric_code)
        if entry is None:
            raise KeyError(f"指标 {metric_code} 不存在")
        history = self._result_store.get((metric_code, time_partition), [])
        materialized = [r for r in history if r.status == RESULT_MATERIALIZED]
        published = [r for r in history if r.status in (RESULT_PUBLISHED, RESULT_SUPERSEDED)]
        if materialized:
            chosen = materialized[-1]
            source = "重算物化结果"
        elif published:
            chosen = published[0]
            source = "已发布结果（重算尚未物化）"
        else:
            raise LookupError(f"指标 {metric_code} 在分区 {time_partition} 无可用结果")
        return {
            "metric_code": metric_code,
            "metric_name": entry.name,
            "time_partition": time_partition,
            "storage_table": chosen.storage_table,
            "source": source,
            "result": chosen.to_dict(),
            "history": [r.to_dict() for r in history],
        }

    def export_snapshot(self, time_partition: str = PARTITION,
                        metric_codes: list[str] | None = None) -> dict[str, Any]:
        """用快照取数导出指定分区指标，返回表头与行（供路由层输出 CSV）。"""
        codes = metric_codes or list(self.metrics.keys())
        header = ["分区", "指标编号", "指标名称", "取数来源", "结果编号", "指标值",
                  "字段路径", "证据版本", "结果状态", "运行编号"]
        rows: list[list[Any]] = []
        for code in codes:
            payload = self.snapshot_read(code, time_partition)
            result = payload["result"]
            rows.append([
                time_partition, code, payload["metric_name"], payload["source"],
                result["result_id"],
                "" if result["value"] is None else result["value"],
                result["field_path"], "|".join(result["evidence_versions"]),
                result["status"], result["run_id"],
            ])
        return {"header": header, "rows": rows}

    # ------------------------------------------------------------------
    # 查询辅助
    # ------------------------------------------------------------------
    def list_evidences(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.evidences.values()]

    def list_metrics(self) -> list[dict[str, Any]]:
        view = self.impact_view()
        return view["metrics"]

    def list_runs(self) -> list[dict[str, Any]]:
        return [run.to_dict() for run in self._runs]

    def group_members(self, group_code: str = GROUP_TODAY) -> list[str]:
        return [code for code, entry in self.metrics.items() if entry.group_code == group_code]


# 全局单例：路由层共享同一份场景状态。
impact_engine = ImpactEngine()
