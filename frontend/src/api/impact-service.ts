import { request } from './client'
import type { ImpactView, RecalcRun, SnapshotRead } from './impact-types'

/** 字段影响视图：两层下游路径、依据标注、指标与重算范围。 */
export async function fetchImpactView(): Promise<ImpactView> {
  const response = await request('/api/impact/view')
  if (!response.ok) throw new Error('字段影响视图读取失败')
  return await response.json()
}

/** 确认证据（如补齐金额新来源），确认后该字段下游重新具备完整依据。 */
export async function confirmEvidence(code: string): Promise<void> {
  const response = await request(`/api/impact/evidences/${code}/confirm`, {
    method: 'POST',
  })
  if (!response.ok) throw new Error('证据确认失败')
}

/** 对选中的可重算范围发起真实计算。 */
export async function recomputeMetrics(metricCodes: string[]): Promise<RecalcRun> {
  const response = await request('/api/impact/recompute', {
    method: 'POST',
    body: JSON.stringify({
      metric_codes: metricCodes,
      group_code: '本日财税指标组',
      time_partition: '20261123',
    }),
  })
  if (!response.ok) throw new Error('指标重算提交失败')
  const payload = await response.json()
  return payload.run
}

/** 快照取数：读取指标实际取到的结果及其版本历史。 */
export async function fetchSnapshot(metricCode: string): Promise<SnapshotRead> {
  const response = await request(`/api/impact/snapshot/${metricCode}`)
  if (!response.ok) throw new Error('快照取数失败')
  return await response.json()
}

/** 用快照取数导出本日指标 CSV。 */
export function snapshotExportUrl(): string {
  return `${import.meta.env.VITE_API_BASE ?? ''}/api/impact/export?time_partition=20261123`
}
