/** 字段影响分析接口的前端类型，与后端 app/impact 返回结构保持一致。 */

export type BasisStatus = '仍有独立依据' | '暂时无完整依据'
export type EvidenceStatus = '已确认' | '待核' | '已撤销'

export type Evidence = {
  code: string
  field_ref: string
  role: string
  version: string
  status: EvidenceStatus
  detail: string
  value_hint: number | null
}

export type ImpactNode = {
  field: string
  layer: number
  basis: BasisStatus
  via_edge: string
  upstream: string
  transform: string
  evidence: Evidence[]
  blocking_inputs: string[]
}

export type MetricCard = {
  code: string
  name: string
  group_code: string
  in_scope: boolean
  out_of_scope: boolean
  recomputable: boolean
  blocking_inputs: string[]
  field_paths: string[]
  agg_function: string
  data_field: string
  group_dimensions: string[]
  time_partition: string
  published_value: number | null
  published_evidence_versions: string[]
  current_result: {
    result_id: string
    status: string
    value: number | null
    field_path: string
    evidence_versions: string[]
    pending_inputs: string[]
    run_id: string
    note: string
  } | null
}

export type ImpactView = {
  roots: string[]
  max_layers: number
  layers: { layer: number; nodes: ImpactNode[] }[]
  metrics: MetricCard[]
  scopes: {
    recomputable: string[]
    blocked: string[]
    out_of_scope: string[]
  }
}

export type RecalcResult = {
  result_id: string
  metric_code: string
  group_code: string
  time_partition: string
  status: string
  value: number | null
  field_path: string
  evidence_versions: string[]
  pending_inputs: string[]
  run_id: string
  storage_table: string
  superseded_result_id: string | null
  note: string
}

export type RecalcRun = {
  run_id: string
  group_code: string
  time_partition: string
  requested_metric_codes: string[]
  results: RecalcResult[]
  blocked: string[]
  untouched: string[]
  created_at: string
}

export type SnapshotRead = {
  metric_code: string
  metric_name: string
  time_partition: string
  storage_table: string
  source: string
  result: RecalcResult
  history: RecalcResult[]
}
