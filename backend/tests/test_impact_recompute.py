"""字段影响视图与指标重算的端到端测试。

覆盖业务叙述：
1. 从发生变化的原字段追两层下游，区分仍有独立依据与暂时无完整依据的目标；
2. 已发布金额 20、税额 5 是一组本日指标，旧税额 5 仍是可用结果；
3. 金额旧来源撤销、新来源待核；税额有独立更正证据 7；
4. 整组重算：税额成功物化（5+7=12），金额因待核输入不得物化；
5. 快照取数导出本日税额，核对实际取到的是哪次结果、旧组合是否仍在使用；
6. 补齐金额新来源后重算金额 30，同表无关库存不被重建。
"""
from __future__ import annotations

import pytest

from app.impact.engine import ImpactEngine
from app.impact.models import (
    BASIS_SUPPORTED,
    BASIS_UNVERIFIED,
    EVIDENCE_CONFIRMED,
    EVIDENCE_PENDING,
    EVIDENCE_REVOKED,
    RESULT_BLOCKED,
    RESULT_MATERIALIZED,
    RESULT_PUBLISHED,
    RESULT_SUPERSEDED,
)


@pytest.fixture
def engine() -> ImpactEngine:
    return ImpactEngine()


def _node_by_field(layer: dict, field: str) -> dict:
    return next(node for node in layer["nodes"] if node["field"] == field)


class TestImpactView:
    """影响视图：两层血缘与依据标注。"""

    def test_two_layers_from_changed_fields(self, engine: ImpactEngine) -> None:
        view = engine.impact_view()
        assert view["roots"] == ["ods.amount_raw", "ods.tax_raw"]
        assert [layer["layer"] for layer in view["layers"]] == [1, 2]
        layer1_fields = {node["field"] for node in view["layers"][0]["nodes"]}
        layer2_fields = {node["field"] for node in view["layers"][1]["nodes"]}
        assert layer1_fields == {"dwd_order.amount_fld", "dwd_order.tax_fld"}
        assert layer2_fields == {"ads_daily.amount_metric", "ads_daily.tax_metric"}

    def test_amount_targets_lack_complete_basis(self, engine: ImpactEngine) -> None:
        view = engine.impact_view()
        amount_l1 = _node_by_field(view["layers"][0], "dwd_order.amount_fld")
        amount_l2 = _node_by_field(view["layers"][1], "ads_daily.amount_metric")
        # 金额旧证据已撤销、新证据待核 -> 两层目标都暂时无完整依据
        assert amount_l1["basis"] == BASIS_UNVERIFIED
        assert amount_l2["basis"] == BASIS_UNVERIFIED
        blocking = " ".join(amount_l1["blocking_inputs"])
        assert "EV-AMT-OLD" in blocking and "已撤销" in blocking
        assert "EV-AMT-NEW" in blocking and "待核" in blocking

    def test_tax_targets_keep_independent_basis(self, engine: ImpactEngine) -> None:
        view = engine.impact_view()
        tax_l1 = _node_by_field(view["layers"][0], "dwd_order.tax_fld")
        tax_l2 = _node_by_field(view["layers"][1], "ads_daily.tax_metric")
        # 旧税额 5 仍可用 + 独立新证据 7 已确认 -> 仍有独立依据
        assert tax_l1["basis"] == BASIS_SUPPORTED
        assert tax_l2["basis"] == BASIS_SUPPORTED
        versions = {item["version"] for item in tax_l2["evidence"]}
        assert {"EV-TAX-OLD-V1", "EV-TAX-NEW-V1"} <= versions

    def test_scopes_separate_recomputable_blocked_and_unrelated(self, engine: ImpactEngine) -> None:
        view = engine.impact_view()
        assert view["scopes"]["recomputable"] == ["METR-TAX"]
        assert view["scopes"]["blocked"] == ["METR-AMT"]
        # 库存不在金额/税额变化的影响范围内
        assert view["scopes"]["out_of_scope"] == ["METR-INV"]

    def test_metric_cards_record_field_path_and_evidence_versions(self, engine: ImpactEngine) -> None:
        view = engine.impact_view()
        tax = next(card for card in view["metrics"] if card["code"] == "METR-TAX")
        assert tax["field_paths"] == [
            "ods.tax_raw -> dwd_order.tax_fld -> ads_daily.tax_metric",
        ]
        assert tax["published_value"] == 5.0
        assert tax["published_evidence_versions"] == ["EV-TAX-OLD-V1"]
        assert tax["current_result"]["status"] == RESULT_PUBLISHED


class TestGroupRecompute:
    """同事从视图发起整组重算。"""

    def test_published_metrics_are_the_today_group(self, engine: ImpactEngine) -> None:
        amount = engine.latest_result("METR-AMT")
        tax = engine.latest_result("METR-TAX")
        assert amount.status == RESULT_PUBLISHED and amount.value == 20.0
        assert tax.status == RESULT_PUBLISHED and tax.value == 5.0
        # 旧税额 5 是可用结果（证据仍为已确认）
        assert engine.evidences["EV-TAX-OLD"].status == EVIDENCE_CONFIRMED

    def test_group_recompute_materializes_tax_but_blocks_amount(self, engine: ImpactEngine) -> None:
        run = engine.recompute(["METR-AMT", "METR-TAX"], group_code="本日财税指标组")
        assert run.blocked == ["METR-AMT"]
        statuses = {r.metric_code: r for r in run.results}

        # 税额沿聚合入口真实计算：旧 5 + 独立新证据 7 = 12
        tax_result = statuses["METR-TAX"]
        assert tax_result.status == RESULT_MATERIALIZED
        assert tax_result.value == 12.0
        assert tax_result.evidence_versions == ["EV-TAX-NEW-V1", "EV-TAX-OLD-V1"]
        assert "华东=7" in tax_result.note and "华北=5" in tax_result.note

        # 未核金额不得先作为重算成功
        amount_result = statuses["METR-AMT"]
        assert amount_result.status == RESULT_BLOCKED
        assert amount_result.value is None
        assert amount_result.pending_inputs == [
            "EV-AMT-NEW（待核）", "EV-AMT-OLD（已撤销）",
        ]

    def test_old_tax_result_kept_but_superseded(self, engine: ImpactEngine) -> None:
        engine.recompute(["METR-TAX"], group_code="本日财税指标组")
        history = engine.snapshot_read("METR-TAX")["history"]
        statuses = {item["status"] for item in history}
        assert RESULT_SUPERSEDED in statuses
        old = next(item for item in history if item["status"] == RESULT_SUPERSEDED)
        assert old["value"] == 5.0  # 旧组合仍可查
        new = next(item for item in history if item["status"] == RESULT_MATERIALIZED)
        assert new["value"] == 12.0

    def test_snapshot_reads_materialized_tax_result(self, engine: ImpactEngine) -> None:
        engine.recompute(["METR-TAX"], group_code="本日财税指标组")
        payload = engine.snapshot_read("METR-TAX")
        assert payload["source"] == "重算物化结果"
        assert payload["result"]["value"] == 12.0
        assert payload["result"]["run_id"]

    def test_snapshot_falls_back_to_published_when_amount_blocked(self, engine: ImpactEngine) -> None:
        engine.recompute(["METR-AMT", "METR-TAX"], group_code="本日财税指标组")
        payload = engine.snapshot_read("METR-AMT")
        assert payload["source"] == "已发布结果（重算尚未物化）"
        assert payload["result"]["value"] == 20.0  # 金额仍是旧发布值，未被冒进改写

    def test_export_uses_snapshot_reads(self, engine: ImpactEngine) -> None:
        engine.recompute(["METR-AMT", "METR-TAX"], group_code="本日财税指标组")
        exported = engine.export_snapshot(metric_codes=["METR-TAX", "METR-AMT"])
        rows = {row[1]: row for row in exported["rows"]}
        assert rows["METR-TAX"][5] == 12.0
        assert rows["METR-TAX"][3] == "重算物化结果"
        assert rows["METR-AMT"][5] == 20.0
        assert rows["METR-AMT"][3] == "已发布结果（重算尚未物化）"
        # 导出注明字段路径与证据版本
        assert "ods.tax_raw" in rows["METR-TAX"][6]
        assert "EV-TAX-NEW-V1" in rows["METR-TAX"][7]


class TestConfirmAndRecomputeAmount:
    """补齐金额新来源后再算金额，并验证库存不被重建。"""

    def test_confirm_new_amount_evidence_is_audited(self, engine: ImpactEngine) -> None:
        assert engine.evidences["EV-AMT-NEW"].status == EVIDENCE_PENDING
        evidence = engine.confirm_evidence("EV-AMT-NEW")
        assert evidence.status == EVIDENCE_CONFIRMED
        log = engine.audit_log[-1]
        assert log["evidence_code"] == "EV-AMT-NEW"
        assert log["from_status"] == EVIDENCE_PENDING
        assert log["to_status"] == EVIDENCE_CONFIRMED

    def test_amount_recomputes_to_30_after_confirmation(self, engine: ImpactEngine) -> None:
        engine.confirm_evidence("EV-AMT-NEW")
        view = engine.impact_view()
        # 新来源确认后金额目标转为仍有独立依据
        amount_l2 = _node_by_field(view["layers"][1], "ads_daily.amount_metric")
        assert amount_l2["basis"] == BASIS_SUPPORTED
        assert "METR-AMT" in view["scopes"]["recomputable"]

        run = engine.recompute(["METR-AMT"], group_code="本日财税指标组")
        assert run.blocked == []
        result = run.results[0]
        assert result.status == RESULT_MATERIALIZED
        assert result.value == 30.0  # 华东 18 + 华北 12
        assert result.evidence_versions == ["EV-AMT-NEW-V1"]

        payload = engine.snapshot_read("METR-AMT")
        assert payload["result"]["value"] == 30.0
        assert payload["source"] == "重算物化结果"

    def test_inventory_never_rebuilt(self, engine: ImpactEngine) -> None:
        before = engine.latest_result("METR-INV")
        # 整组（金额+税额）重算
        engine.recompute(["METR-AMT", "METR-TAX"], group_code="本日财税指标组")
        # 补齐金额后再算金额
        engine.confirm_evidence("EV-AMT-NEW")
        engine.recompute(["METR-AMT"], group_code="本日财税指标组")

        after = engine.latest_result("METR-INV")
        assert after.result_id == before.result_id
        assert after.status == RESULT_PUBLISHED
        assert after.value == 100.0
        # 重算运行记录里库存始终是未触及项
        for run in engine.list_runs():
            assert "METR-INV" not in run["requested_metric_codes"]

    def test_recompute_only_confirmed_evidence_rows(self, engine: ImpactEngine) -> None:
        """确认新金额来源后，已撤销旧来源的行不参与聚合。"""
        engine.confirm_evidence("EV-AMT-NEW")
        entry = engine.metrics["METR-AMT"]
        confirmed = [e for e in engine._evidences_for_metric("METR-AMT") if e.is_usable()]
        total, breakdown = engine._aggregate(entry, confirmed)
        assert total == 30.0
        assert breakdown == {"华东": 18.0, "华北": 12.0}
