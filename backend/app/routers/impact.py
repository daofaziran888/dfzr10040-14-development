"""字段影响分析路由：影响视图、证据确认、指标重算与快照取数。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.impact.engine import impact_engine
from app.impact.models import EVIDENCE_CONFIRMED

router = APIRouter(prefix="/api/impact", tags=["字段影响分析"])


class RecomputePayload(BaseModel):
    metric_codes: list[str] = Field(default_factory=list)
    group_code: str | None = None
    time_partition: str = "20261123"


@router.get("/view")
def impact_view(
    root: list[str] = Query(default=None),
    max_layers: int = Query(default=2, ge=1, le=4),
) -> dict[str, Any]:
    """字段影响视图：两层下游路径、依据标注、指标与可重算范围。"""
    return impact_engine.impact_view(root, max_layers=max_layers)


@router.get("/evidences")
def list_evidences() -> dict[str, Any]:
    return {"items": impact_engine.list_evidences()}


@router.post("/evidences/{evidence_code}/confirm")
def confirm_evidence(evidence_code: str) -> dict[str, Any]:
    """确认待核来源；只有确认动作能把证据置为「已确认」。"""
    try:
        evidence = impact_engine.confirm_evidence(evidence_code)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if evidence.status != EVIDENCE_CONFIRMED:
        raise HTTPException(status_code=409, detail=f"证据 {evidence_code} 确认失败")
    return {"ok": True, "message": f"证据 {evidence_code} 已确认", "entry": evidence.to_dict()}


@router.get("/metrics")
def list_metrics() -> dict[str, Any]:
    return {"items": impact_engine.list_metrics()}


@router.post("/recompute")
def recompute(payload: RecomputePayload) -> dict[str, Any]:
    """对选中的可重算范围提交真实计算。"""
    unknown = [code for code in payload.metric_codes if code not in impact_engine.metrics]
    if unknown:
        raise HTTPException(status_code=404, detail=f"未知指标：{'、'.join(unknown)}")
    run = impact_engine.recompute(
        payload.metric_codes,
        group_code=payload.group_code,
        time_partition=payload.time_partition,
    )
    return {"ok": True, "message": f"重算 {run.run_id} 完成", "run": run.to_dict()}


@router.get("/runs")
def list_runs() -> dict[str, Any]:
    return {"items": impact_engine.list_runs()}


@router.get("/snapshot/{metric_code}")
def snapshot_read(
    metric_code: str,
    time_partition: str = Query(default="20261123"),
) -> dict[str, Any]:
    """从快照取数结果读取指标实际取到的结果。"""
    try:
        return impact_engine.snapshot_read(metric_code, time_partition)
    except (KeyError, LookupError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/export")
def export_snapshot(
    time_partition: str = Query(default="20261123"),
    metric: list[str] = Query(default=None),
) -> StreamingResponse:
    """用快照取数导出本日指标（CSV）。"""
    payload = impact_engine.export_snapshot(time_partition, metric)
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(payload["header"])
    writer.writerows(payload["rows"])
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="impact_snapshot_{time_partition}.csv"',
        },
    )
