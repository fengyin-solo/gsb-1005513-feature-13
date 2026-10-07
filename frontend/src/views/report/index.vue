<template>
  <section class="page" data-module="report">
    <header class="page-head">
      <div>
        <h2>运行月报管理</h2>
        <p class="page-desc">
          发电量、等效利用小时、综合效率PR、设备可利用率由系统按统一口径从各模块明细自动汇总；
          状态沿草稿、已复核、已归档推进，归档后冻结口径快照。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="showCreate = !showCreate">生成运行月报</button>
        <button class="btn" type="button" @click="exportRows">导出运行月报清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form v-if="showCreate" class="filter-bar create-panel" @submit.prevent="submitCreate">
      <label class="filter-item">
        <span>统计月份</span>
        <input v-model="createForm.统计月份" type="month" required />
      </label>
      <label class="filter-item">
        <span>月报编号（留空自动编号）</span>
        <input v-model="createForm.月报编号" placeholder="如 REPO-2026-09" />
      </label>
      <button class="btn primary" type="submit">按当月口径生成</button>
      <button class="btn ghost" type="button" @click="showCreate = false">取消</button>
    </form>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>月报编号</span>
        <input v-model="filters.keyword" placeholder="按月报编号检索" />
      </label>
      <label class="filter-item">
        <span>月报状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <div v-if="pendingAction" class="conclusion-panel">
      <span>{{ pendingAction.action }} · {{ pendingAction.row.月报编号 }}（{{ pendingAction.row.统计月份 }}）</span>
      <input v-model="conclusion" placeholder="复核结论（留空默认：复核通过）" />
      <button class="btn primary" type="button" @click="confirmAction">确认{{ pendingAction.action }}</button>
      <button class="btn ghost" type="button" @click="pendingAction = null">取消</button>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="runAction('重新计算', row)">重新计算</button>
            <button class="link" type="button" @click="askConclusion('提交复核', row)">提交复核</button>
            <button class="link" type="button" @click="askConclusion('归档月报', row)">归档月报</button>
            <button
              v-if="row.status === '已归档'"
              class="link"
              type="button"
              @click="openArchiveDetail(row)"
            >
              归档详情
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无运行月报数据，可先生成运行月报</td>
        </tr>
      </tbody>
    </table>

    <h3 class="section-title">归档台账</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in archiveColumns" :key="column">{{ column }}</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="record in archiveRows" :key="String(record.id)">
          <td v-for="column in archiveColumns" :key="column">{{ record[column] ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openArchiveDetail(record)">归档详情</button>
          </td>
        </tr>
        <tr v-if="!archiveRows.length">
          <td :colspan="archiveColumns.length + 1" class="empty-state">暂无归档记录</td>
        </tr>
      </tbody>
    </table>

    <div v-if="archiveDetail" class="detail-panel">
      <header class="detail-head">
        <strong>归档详情 · {{ archiveDetail.月报编号 }}（{{ archiveDetail.统计月份 }}）</strong>
        <button class="link" type="button" @click="archiveDetail = null">收起</button>
      </header>
      <dl class="detail-grid">
        <template v-for="field in archiveDetailFields" :key="field">
          <dt>{{ field }}</dt>
          <dd>{{ archiveDetail[field] ?? '—' }}</dd>
        </template>
      </dl>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条运行月报记录</span>
      <span v-if="notice" class="notice-text">{{ notice }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/report'
const columns = ["月报编号", "统计月份", "发电量", "等效利用小时", "综合效率PR", "设备可利用率", "故障停机时间", "口径版本", "月报状态"]
const statuses = ["草稿", "已复核", "已归档"]
const archiveColumns = ["台账编号", "月报编号", "统计月份", "复核结论", "口径版本", "归档时间"]
const archiveDetailFields = ["台账编号", "月报编号", "统计月份", "发电量", "等效利用小时", "综合效率PR", "设备可利用率", "故障停机时间", "口径版本", "复核结论", "归档时间"]

const rows = ref<Row[]>([])
const archiveRows = ref<Row[]>([])
const total = ref(0)
const notice = ref('')
const errorMessage = ref('')
const filters = ref<Record<string, string>>({ keyword: '', status: '' })
const showCreate = ref(false)
const createForm = ref<Record<string, string>>({ 统计月份: '', 月报编号: '' })
const pendingAction = ref<{ action: string; row: Row } | null>(null)
const conclusion = ref('')
const archiveDetail = ref<Row | null>(null)

const stats = computed(() => {
  const latest = rows.value[0]
  return [
    { label: '最新月报发电量(MWh)', value: latest?.发电量 ?? '—' },
    { label: '最新月报PR值(%)', value: latest?.综合效率PR ?? '—' },
    { label: '待复核月报数', value: rows.value.filter((row) => row.status === '草稿').length },
    { label: '已归档月报数', value: rows.value.filter((row) => row.status === '已归档').length },
  ]
})

function resetFilters() {
  filters.value = { keyword: '', status: '' }
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function readPayload(response: Response): Promise<{ ok: boolean; message?: string }> {
  const payload = (await response.json()) as { ok?: boolean; message?: string; detail?: string }
  return { ok: response.ok && payload.ok !== false, message: payload.message ?? payload.detail }
}

async function submitCreate() {
  notice.value = ''
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: { ...createForm.value } }),
    })
    const result = await readPayload(response)
    if (!result.ok) {
      errorMessage.value = result.message ?? '运行月报生成失败'
      return
    }
    notice.value = result.message ?? '运行月报已生成'
    showCreate.value = false
    createForm.value = { 统计月份: '', 月报编号: '' }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '运行月报生成失败'
  }
}

function askConclusion(action: string, row: Row) {
  conclusion.value = String(row.复核结论 ?? '')
  pendingAction.value = { action, row }
}

async function confirmAction() {
  if (!pendingAction.value) return
  const { action, row } = pendingAction.value
  pendingAction.value = null
  await runAction(action, row, conclusion.value)
  conclusion.value = ''
}

async function runAction(action: string, row: Row, 复核结论 = '') {
  notice.value = ''
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action, 复核结论 } }),
    })
    const result = await readPayload(response)
    if (!result.ok) {
      errorMessage.value = result.message ?? '运行月报动作未生效'
      return
    }
    notice.value = result.message ?? '操作完成'
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '运行月报操作失败'
  }
}

async function openArchiveDetail(record: Row) {
  notice.value = ''
  errorMessage.value = ''
  const entryId = record.月报ID ?? record.id
  try {
    const response = await request(`${ENDPOINT}/${entryId}/archive`)
    if (!response.ok) {
      const payload = (await response.json()) as { detail?: string }
      errorMessage.value = payload.detail ?? '归档详情读取失败'
      return
    }
    archiveDetail.value = (await response.json()) as Row
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '归档详情读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (filters.value.keyword) query.set('keyword', filters.value.keyword)
  if (filters.value.status) query.set('status', filters.value.status)
  query.set('size', '200')
  try {
    const [listResponse, archiveResponse] = await Promise.all([
      request(`${ENDPOINT}?${query}`),
      request(`${ENDPOINT}/archive`),
    ])
    if (!listResponse.ok) {
      throw new Error('运行月报列表读取失败')
    }
    const payload = await listResponse.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    if (archiveResponse.ok) {
      const archivePayload = await archiveResponse.json()
      archiveRows.value = archivePayload.items ?? []
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '运行月报列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.create-panel,
.conclusion-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
}
.conclusion-panel {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 12px;
  font-size: 13px;
}
.conclusion-panel input {
  flex: 1;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.section-title {
  margin: 18px 0 8px;
  font-size: 14px;
}
.detail-panel {
  margin-top: 12px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
}
.detail-head {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
}
.detail-grid {
  display: grid;
  grid-template-columns: 140px 1fr 140px 1fr;
  gap: 6px 12px;
  margin: 0;
  font-size: 13px;
}
.detail-grid dt {
  color: var(--muted);
}
.detail-grid dd {
  margin: 0;
}
.notice-text {
  color: #067647;
}
.filter-item select {
  padding: 5px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
}
</style>
