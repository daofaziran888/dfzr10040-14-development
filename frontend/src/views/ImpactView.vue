<template>
  <section class="page impact-page" data-module="impact_view">
    <header class="page-head">
      <div>
        <h2>字段影响视图</h2>
        <p class="page-desc">
          从发生变化的原字段向下追两层，标注各目标的依据状态，列出关联指标与可重算范围，
          并沿指标聚合入口提交真实计算、从快照结果取数。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="refresh-impact" @click="loadAll">
          刷新视图
        </button>
        <a class="btn" :href="exportUrl" data-testid="export-snapshot">快照取数导出</a>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <!-- 变化原字段与重算范围概览 -->
    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">变化原字段</span>
        <strong class="stat-value roots">{{ view?.roots.join('、') ?? '—' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">可重算范围</span>
        <strong class="stat-value">{{ view?.scopes.recomputable.join('、') || '暂无' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待核阻断范围</span>
        <strong class="stat-value warn">{{ view?.scopes.blocked.join('、') || '无' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">范围外（不重建）</span>
        <strong class="stat-value muted">{{ view?.scopes.out_of_scope.join('、') || '无' }}</strong>
      </article>
    </div>

    <!-- 两层下游追踪 -->
    <h3 class="section-title">两层下游路径与依据标注</h3>
    <div v-for="layer in view?.layers ?? []" :key="layer.layer" class="trace-layer">
      <h4 class="layer-title">第 {{ layer.layer }} 层下游</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th>目标字段</th>
            <th>上游字段 / 血缘边</th>
            <th>转换</th>
            <th>依据状态</th>
            <th>关联证据版本</th>
            <th>阻断输入</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="node in layer.nodes" :key="node.field" :data-basis="node.basis">
            <td>{{ node.field }}</td>
            <td>{{ node.upstream }}<br /><span class="muted-text">{{ node.via_edge }}</span></td>
            <td>{{ node.transform }}</td>
            <td>
              <span class="badge" :class="node.basis === '仍有独立依据' ? 'badge-ok' : 'badge-warn'">
                {{ node.basis }}
              </span>
            </td>
            <td>
              <span v-for="ev in node.evidence" :key="ev.code" class="evidence-chip" :class="evidenceClass(ev.status)">
                {{ ev.version }}（{{ ev.status }}）
              </span>
            </td>
            <td>
              <span v-if="node.blocking_inputs.length">{{ node.blocking_inputs.join('、') }}</span>
              <span v-else class="muted-text">无</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 指标与重算范围 -->
    <h3 class="section-title">关联指标与重算范围</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th class="col-check">
            <input
              type="checkbox"
              data-testid="check-all"
              :checked="allRecomputableChecked"
              :disabled="recomputableCards.length === 0"
              @change="toggleAll($event)"
            />
          </th>
          <th>指标</th>
          <th>采用的字段路径</th>
          <th>已发布值 / 证据版本</th>
          <th>当前结果</th>
          <th>重算依据</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="card in view?.metrics ?? []" :key="card.code" :data-testid="`metric-${card.code}`">
          <td>
            <input
              v-if="card.recomputable"
              type="checkbox"
              :value="card.code"
              v-model="selectedMetrics"
              :data-testid="`check-${card.code}`"
            />
            <span v-else class="muted-text">—</span>
          </td>
          <td>
            {{ card.name }} <span class="muted-text">({{ card.code }})</span>
            <br />
            <span class="badge" :class="card.out_of_scope ? 'badge-muted' : card.recomputable ? 'badge-ok' : 'badge-warn'">
              {{ card.out_of_scope ? '不在重算范围' : card.recomputable ? '可重算' : '待核阻断' }}
            </span>
          </td>
          <td class="path-cell">
            <span v-for="path in card.field_paths" :key="path" class="field-path">{{ path }}</span>
          </td>
          <td>
            {{ card.published_value ?? '—' }}
            <br />
            <span class="muted-text">{{ card.published_evidence_versions.join('、') }}</span>
          </td>
          <td>
            <template v-if="card.current_result">
              <strong>{{ card.current_result.value ?? '未物化' }}</strong>
              <br />
              <span class="muted-text">{{ card.current_result.status }} · {{ card.current_result.result_id }}</span>
            </template>
            <span v-else class="muted-text">无</span>
          </td>
          <td>
            <span v-if="card.blocking_inputs.length" class="warn-text">{{ card.blocking_inputs.join('、') }}</span>
            <span v-else class="muted-text">输入证据均已确认</span>
          </td>
        </tr>
      </tbody>
    </table>

    <div class="action-bar">
      <button
        class="btn primary"
        type="button"
        data-testid="submit-recompute"
        :disabled="selectedMetrics.length === 0 || running"
        @click="submitRecompute"
      >
        {{ running ? '计算中…' : `对选中范围发起重算（${selectedMetrics.length}）` }}
      </button>
      <span class="muted-text">重算沿指标聚合入口选择实际数据字段与分组，确认的指标才写入快照取数结果。</span>
    </div>

    <!-- 最近一次重算运行 -->
    <template v-if="lastRun">
      <h3 class="section-title">最近重算运行 {{ lastRun.run_id }}</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>指标</th>
            <th>结果状态</th>
            <th>指标值</th>
            <th>实际采用证据版本</th>
            <th>待核输入 / 说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="res in lastRun.results" :key="res.result_id" :data-testid="`run-${res.metric_code}`">
            <td>{{ res.metric_code }}</td>
            <td>
              <span class="badge" :class="res.status === '已物化' ? 'badge-ok' : 'badge-warn'">
                {{ res.status }}
              </span>
            </td>
            <td>{{ res.value ?? '—' }}</td>
            <td>{{ res.evidence_versions.join('、') || '—' }}</td>
            <td>
              <span v-if="res.pending_inputs.length" class="warn-text">{{ res.pending_inputs.join('、') }}</span>
              <span v-else class="muted-text">{{ res.note }}</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p class="muted-text">
        本次阻断：{{ lastRun.blocked.join('、') || '无' }} ｜ 未触及：{{ lastRun.untouched.join('、') || '无' }}
      </p>
    </template>

    <!-- 证据台账：补齐待核来源 -->
    <h3 class="section-title">证据台账与来源确认</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>证据编号</th>
          <th>支撑字段</th>
          <th>来源角色</th>
          <th>证据版本</th>
          <th>状态</th>
          <th>说明</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="ev in evidences" :key="ev.code" :data-testid="`evidence-${ev.code}`">
          <td>{{ ev.code }}</td>
          <td>{{ ev.field_ref }}</td>
          <td>{{ ev.role }}</td>
          <td>{{ ev.version }}</td>
          <td>
            <span class="badge" :class="evidenceClass(ev.status)">{{ ev.status }}</span>
          </td>
          <td>{{ ev.detail }}</td>
          <td>
            <button
              v-if="ev.status !== '已确认'"
              class="link"
              type="button"
              :data-testid="`confirm-${ev.code}`"
              @click="confirm(ev.code)"
            >
              确认来源
            </button>
            <span v-else class="muted-text">已确认</span>
          </td>
        </tr>
      </tbody>
    </table>

    <!-- 快照取数核对 -->
    <h3 class="section-title">快照取数核对</h3>
    <div class="snapshot-bar">
      <label>
        选择指标
        <select v-model="snapshotMetric" data-testid="snapshot-metric">
          <option v-for="card in view?.metrics ?? []" :key="card.code" :value="card.code">
            {{ card.name }}（{{ card.code }}）
          </option>
        </select>
      </label>
      <button class="btn primary" type="button" data-testid="read-snapshot" @click="readSnapshot">
        快照取数
      </button>
    </div>
    <table v-if="snapshot" class="data-table">
      <thead>
        <tr>
          <th>结果编号</th>
          <th>取数来源</th>
          <th>指标值</th>
          <th>字段路径</th>
          <th>证据版本</th>
          <th>状态</th>
          <th>运行</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in snapshot.history" :key="item.result_id" :data-testid="`snap-row-${item.result_id}`">
          <td>{{ item.result_id }}</td>
          <td>
            <span v-if="item.result_id === snapshot.result.result_id" class="badge badge-ok">本次取到</span>
            <span v-else class="muted-text">历史版本</span>
          </td>
          <td>{{ item.value ?? '—' }}</td>
          <td class="path-cell">{{ item.field_path }}</td>
          <td>{{ item.evidence_versions.join('、') || '—' }}</td>
          <td>{{ item.status }}</td>
          <td>{{ item.run_id || '—' }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  confirmEvidence,
  fetchImpactView,
  fetchSnapshot,
  recomputeMetrics,
  snapshotExportUrl,
} from '@/api/impact-service'
import type { Evidence, ImpactView, RecalcRun, SnapshotRead } from '@/api/impact-types'

const view = ref<ImpactView | null>(null)
const evidences = ref<Evidence[]>([])
const selectedMetrics = ref<string[]>([])
const lastRun = ref<RecalcRun | null>(null)
const snapshot = ref<SnapshotRead | null>(null)
const snapshotMetric = ref('METR-TAX')
const running = ref(false)
const errorMessage = ref('')
const exportUrl = snapshotExportUrl()

const recomputableCards = computed(
  () => view.value?.metrics.filter((card) => card.recomputable) ?? [],
)
const allRecomputableChecked = computed(
  () =>
    recomputableCards.value.length > 0 &&
    recomputableCards.value.every((card) => selectedMetrics.value.includes(card.code)),
)

function evidenceClass(status: string): string {
  if (status === '已确认') return 'badge-ok'
  if (status === '待核') return 'badge-warn'
  return 'badge-muted'
}

function toggleAll(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  selectedMetrics.value = checked ? recomputableCards.value.map((card) => card.code) : []
}

async function loadAll() {
  errorMessage.value = ''
  try {
    view.value = await fetchImpactView()
    // 证据从两层节点去重汇总，保持台账顺序稳定。
    const seen = new Set<string>()
    const merged: Evidence[] = []
    for (const layer of view.value.layers) {
      for (const node of layer.nodes) {
        for (const ev of node.evidence) {
          if (!seen.has(ev.code)) {
            seen.add(ev.code)
            merged.push(ev)
          }
        }
      }
    }
    evidences.value = merged
    // 清理已不在可重算范围的勾选项。
    const allowed = new Set(view.value.scopes.recomputable)
    selectedMetrics.value = selectedMetrics.value.filter((code) => allowed.has(code))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '影响视图加载失败'
  }
}

async function submitRecompute() {
  running.value = true
  errorMessage.value = ''
  try {
    lastRun.value = await recomputeMetrics([...selectedMetrics.value])
    await loadAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重算失败'
  } finally {
    running.value = false
  }
}

async function confirm(code: string) {
  errorMessage.value = ''
  try {
    await confirmEvidence(code)
    await loadAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '证据确认失败'
  }
}

async function readSnapshot() {
  errorMessage.value = ''
  try {
    snapshot.value = await fetchSnapshot(snapshotMetric.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '快照取数失败'
  }
}

onMounted(loadAll)
</script>

<style scoped>
.impact-page { display: flex; flex-direction: column; gap: 8px; }
.roots { font-size: 15px; }
.warn { color: #b42318; }
.muted,
.muted-text { color: var(--muted); font-weight: normal; }
.warn-text { color: #b42318; }
.section-title { margin: 16px 0 6px; font-size: 15px; }
.layer-title { margin: 8px 0; font-size: 13px; color: var(--muted); }
.trace-layer { margin-bottom: 10px; }
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
  white-space: nowrap;
}
.badge-ok { background: #dcfae6; color: #0f7b3c; }
.badge-warn { background: #fef3c7; color: #b45309; }
.badge-muted { background: #e2e8f0; color: #475569; }
.evidence-chip {
  display: inline-block;
  margin: 1px 4px 1px 0;
  padding: 1px 6px;
  border-radius: 8px;
  font-size: 12px;
  background: #f1f5f9;
}
.path-cell { max-width: 320px; }
.field-path {
  display: block;
  font-family: ui-monospace, monospace;
  font-size: 12px;
  color: #334155;
}
.col-check { width: 42px; }
.action-bar,
.snapshot-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 10px 0;
}
.snapshot-bar label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.snapshot-bar select { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; min-width: 220px; }
a.btn { text-decoration: none; display: inline-flex; align-items: center; }
</style>
