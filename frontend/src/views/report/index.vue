<template>
  <section class="page" data-module="report">
    <header class="page-head">
      <div>
        <h2>运行月报管理</h2>
        <p class="page-desc">发电量、等效利用小时、综合效率PR、设备可利用率均由各模块明细按统一口径自动汇总落库；口径调整后未归档月报自动重算，归档月报沿用历史口径。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">生成 / 更新月报</button>
        <button class="btn" type="button" @click="exportRows">导出月报清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <div class="tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab"
        :class="{ active: activeTab === tab.key }"
        type="button"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}
      </button>
    </div>

    <!-- ============ 月报列表 ============ -->
    <template v-if="activeTab === 'monthly'">
      <form class="filter-bar" @submit.prevent="reload">
        <label class="filter-item">
          <span>统计月份</span>
          <input v-model="filters.month" placeholder="YYYY-MM，如 2026-09" />
        </label>
        <label class="filter-item">
          <span>月报状态</span>
          <select v-model="filters.status">
            <option value="">全部</option>
            <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>
        <label class="filter-item">
          <span>编号 / 月份关键字</span>
          <input v-model="filters.keyword" placeholder="按编号或月份检索" />
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
        <button class="btn" type="button" @click="recalculateAll">全部未归档重算</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in columns" :key="column">{{ column }}</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="String(row.id)">
            <td v-for="column in columns" :key="column">{{ formatCell(row, column) }}</td>
            <td class="row-actions">
              <template v-if="row['月报状态'] === '草稿'">
                <button class="link" type="button" @click="recalc(row)">按当月口径重算</button>
                <button class="link" type="button" @click="openReview(row, false)">复核通过</button>
                <button class="link danger" type="button" @click="openReview(row, true)">复核退回</button>
                <button class="link" type="button" @click="act(row, 'archive')">归档</button>
              </template>
              <template v-else-if="row['月报状态'] === '已复核'">
                <button class="link danger" type="button" @click="openReview(row, true)">复核退回</button>
                <button class="link" type="button" @click="act(row, 'archive')">归档</button>
              </template>
              <template v-else>
                <button class="link" type="button" @click="openArchiveById(row.id)">查看归档详情</button>
              </template>
              <button class="link" type="button" @click="openDetail(row)">明细依据</button>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td :colspan="columns.length + 1" class="empty-state">暂无月报，可先生成指定统计月份的月报</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>共 {{ total }} 条月报记录（数字均为最后一次重算落库值）</span>
        <span v-if="message" :class="messageOk ? 'ok-text' : 'error-text'">{{ message }}</span>
      </footer>
    </template>

    <!-- ============ 归档台账 ============ -->
    <template v-else-if="activeTab === 'archives'">
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in archiveColumns" :key="column">{{ column }}</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in archiveRows" :key="String(row.id)">
            <td v-for="column in archiveColumns" :key="column">{{ formatCell(row, column) }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="openArchive(row)">归档详情</button>
            </td>
          </tr>
          <tr v-if="!archiveRows.length">
            <td :colspan="archiveColumns.length + 1" class="empty-state">暂无已归档月报</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>台账为归档时的指标与口径快照，口径再调整也不会变动</span>
        <span v-if="message" :class="messageOk ? 'ok-text' : 'error-text'">{{ message }}</span>
      </footer>
    </template>

    <!-- ============ 计算口径 ============ -->
    <template v-else>
      <form class="caliber-panel" @submit.prevent="publishCaliber">
        <h3>发布新口径（保存后所有未归档月报立即按新口径重算落库）</h3>
        <div class="form-grid">
          <label class="filter-item">
            <span>发电量取数来源</span>
            <select v-model="caliberForm.generation_source">
              <option value="meter">关口表计（本月示数－上月示数）</option>
              <option value="inverter">逆变器日均发电量汇总（×当月天数）</option>
            </select>
          </label>
          <label class="filter-item">
            <span>PR 理论发电量口径</span>
            <select v-model="caliberForm.pr_basis">
              <option value="peak_sun_hours">等效峰值日照小时数</option>
              <option value="irradiance">环境站实测辐照度折算</option>
            </select>
          </label>
          <label class="filter-item" v-if="caliberForm.pr_basis === 'peak_sun_hours'">
            <span>等效峰值日照小时数（h）</span>
            <input v-model="caliberForm.peak_sun_hours" type="number" min="0" step="0.1" />
          </label>
          <div class="filter-item">
            <span>故障停机时间统计范围</span>
            <label class="check"><input type="checkbox" value="alarm" v-model="caliberForm.downtime_sources" /> 未闭环告警</label>
            <label class="check"><input type="checkbox" value="defect" v-model="caliberForm.downtime_sources" /> 未闭环缺陷</label>
          </div>
          <label class="filter-item wide">
            <span>本版口径调整说明</span>
            <input v-model="caliberForm.remark" placeholder="如：10月起PR按130峰值小时，停机只计告警" />
          </label>
        </div>
        <div class="modal-actions">
          <button class="btn primary" type="submit">发布并自动重算未归档月报</button>
          <span v-if="message" :class="messageOk ? 'ok-text' : 'error-text'">{{ message }}</span>
        </div>
      </form>

      <h3>历史版本（已归档月报永久引用其归档时版本）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>版本</th><th>当前</th><th>发电量来源</th><th>PR口径</th><th>峰值小时</th>
            <th>停机范围</th><th>说明</th><th>创建时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in calibers" :key="item.version">
            <td>v{{ item.version }}</td>
            <td>{{ item.is_current ? '是' : '—' }}</td>
            <td>{{ sourceLabel(item.params.generation_source) }}</td>
            <td>{{ basisLabel(item.params.pr_basis) }}</td>
            <td>{{ item.params.peak_sun_hours }}</td>
            <td>{{ (item.params.downtime_sources || []).map(downtimeLabel).join('、') || '不计停机' }}</td>
            <td>{{ item.remark || '—' }}</td>
            <td>{{ item.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- ============ 弹窗：生成月报 ============ -->
    <div v-if="showCreate" class="modal-mask" @click.self="showCreate = false">
      <div class="modal">
        <h3>生成 / 更新月报</h3>
        <p class="page-desc">同一统计月份重复提交只保留最新一版；已归档月份不允许重新生成。</p>
        <label class="filter-item">
          <span>统计月份（YYYY-MM）</span>
          <input v-model="createForm.month" placeholder="2026-09" />
        </label>
        <label class="filter-item">
          <span>月报编号（可留空自动生成）</span>
          <input v-model="createForm.reportNo" placeholder="REPO-202609" />
        </label>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="submitCreate">提交并按当前口径重算</button>
          <button class="btn ghost" type="button" @click="showCreate = false">取消</button>
        </div>
      </div>
    </div>

    <!-- ============ 弹窗：复核 ============ -->
    <div v-if="reviewTarget" class="modal-mask" @click.self="reviewTarget = null">
      <div class="modal">
        <h3>{{ reviewReject ? '复核退回' : '复核通过' }} · {{ reviewTarget['统计月份'] }}</h3>
        <label class="filter-item">
          <span>复核结论（将随归档写入归档台账）</span>
          <textarea v-model="reviewConclusion" rows="3"></textarea>
        </label>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="submitReview">确认</button>
          <button class="btn ghost" type="button" @click="reviewTarget = null">取消</button>
        </div>
      </div>
    </div>

    <!-- ============ 弹窗：月报明细依据 ============ -->
    <div v-if="detailTarget" class="modal-mask" @click.self="detailTarget = null">
      <div class="modal wide-modal">
        <h3>月报明细依据 · {{ detailTarget['统计月份'] }}（口径 v{{ detailTarget['口径版本'] }}）</h3>
        <table class="data-table">
          <tbody>
            <tr v-for="kv in detailPairs(detailTarget)" :key="kv[0]">
              <th>{{ kv[0] }}</th><td>{{ kv[1] }}</td>
            </tr>
          </tbody>
        </table>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="detailTarget = null">关闭</button>
        </div>
      </div>
    </div>

    <!-- ============ 弹窗：归档详情 ============ -->
    <div v-if="archiveTarget" class="modal-mask" @click.self="archiveTarget = null">
      <div class="modal wide-modal">
        <h3>归档详情 · {{ archiveTarget['统计月份'] }}</h3>
        <p class="page-desc">
          归档于 {{ archiveTarget['归档时间'] }}，永久沿用口径 v{{ archiveTarget['口径版本'] }}；
          以下数字与月报列表读到的完全一致。
        </p>
        <table class="data-table">
          <tbody>
            <tr v-for="kv in archivePairs(archiveTarget)" :key="kv[0]">
              <th>{{ kv[0] }}</th><td>{{ kv[1] }}</td>
            </tr>
          </tbody>
        </table>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="archiveTarget = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/report'
const columns = ['月报编号', '统计月份', '发电量', '等效利用小时', '综合效率PR', '设备可利用率', '故障停机时间', '口径版本', '版本号', '月报状态']
const archiveColumns = ['月报编号', '统计月份', '发电量', '等效利用小时', '综合效率PR', '设备可利用率', '故障停机时间', '口径版本', '复核结论', '归档时间']
const statuses = ['草稿', '已复核', '已归档']
const tabs = [
  { key: 'monthly', label: '月报列表' },
  { key: 'archives', label: '归档台账' },
  { key: 'caliber', label: '计算口径' },
] as const

const activeTab = ref<(typeof tabs)[number]['key']>('monthly')
const rows = ref<Row[]>([])
const archiveRows = ref<Row[]>([])
const calibers = ref<any[]>([])
const total = ref(0)
const message = ref('')
const messageOk = ref(true)
const filters = ref<Record<string, string>>({ month: '', status: '', keyword: '' })

const showCreate = ref(false)
const createForm = reactive({ month: '', reportNo: '' })
const reviewTarget = ref<Row | null>(null)
const reviewReject = ref(false)
const reviewConclusion = ref('')
const detailTarget = ref<Row | null>(null)
const archiveTarget = ref<Row | null>(null)

const caliberForm = reactive({
  generation_source: 'meter',
  pr_basis: 'peak_sun_hours',
  peak_sun_hours: 140,
  downtime_sources: ['alarm', 'defect'] as string[],
  remark: '',
})

const stats = computed(() => {
  const latest = [...rows.value].sort((a, b) => String(b['统计月份']).localeCompare(String(a['统计月份'])))[0]
  return [
    { label: '最新统计月发电量(kWh)', value: latest ? latest['发电量'] : '—' },
    { label: '最新统计月PR(%)', value: latest ? latest['综合效率PR'] : '—' },
    { label: '未归档月报数', value: rows.value.filter((r) => r['月报状态'] !== '已归档').length },
    { label: '已归档月报数', value: archiveRows.value.length },
  ]
})

function notify(text: string, ok = true) {
  message.value = text
  messageOk.value = ok
}

function resetFilters() {
  filters.value = { month: '', status: '', keyword: '' }
  void reload()
}

function formatCell(row: Row, column: string) {
  const value = row[column]
  if (value === null || value === undefined || value === '') return '—'
  return value
}

async function reload() {
  message.value = ''
  const params = new URLSearchParams()
  if (filters.value.month) params.set('month', filters.value.month.trim())
  if (filters.value.status) params.set('status', filters.value.status)
  if (filters.value.keyword) params.set('keyword', filters.value.keyword.trim())
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) throw new Error('月报列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    notify(error instanceof Error ? error.message : '月报列表读取失败', false)
  }
}

async function reloadArchives() {
  const response = await request(`${ENDPOINT}/archives`)
  if (response.ok) {
    archiveRows.value = (await response.json()).items ?? []
  }
}

async function reloadCalibers() {
  const response = await request(`${ENDPOINT}/calibers`)
  if (response.ok) {
    const payload = await response.json()
    calibers.value = payload.items ?? []
    const params = payload.current?.params
    if (params) {
      caliberForm.generation_source = params.generation_source
      caliberForm.pr_basis = params.pr_basis
      caliberForm.peak_sun_hours = params.peak_sun_hours
      caliberForm.downtime_sources = [...(params.downtime_sources || [])]
    }
  }
}

async function switchTab(key: (typeof tabs)[number]['key']) {
  activeTab.value = key
  if (key === 'archives') await reloadArchives()
  if (key === 'caliber') await reloadCalibers()
}

async function postAction(path: string, body: Record<string, unknown>) {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  return response.json()
}

async function act(row: Row, action: string) {
  const result = await postAction(`${ENDPOINT}/${row.id}/actions`, { values: { action } })
  notify(result.message, !!result.ok)
  if (result.ok) await Promise.all([reload(), reloadArchives()])
}

async function recalc(row: Row) {
  await act(row, 'recalculate')
}

async function recalculateAll() {
  // 逐个未归档月报重算，保证列表里每个月都落最新口径
  const targets = rows.value.filter((r) => r['月报状态'] !== '已归档')
  for (const row of targets) {
    // eslint-disable-next-line no-await-in-loop
    const result = await postAction(`${ENDPOINT}/${row.id}/actions`, { values: { action: 'recalculate' } })
    if (!result.ok) {
      notify(result.message, false)
      return
    }
  }
  notify(targets.length ? `已重算 ${targets.length} 条未归档月报` : '没有需要重算的月报')
  await reload()
}

function openCreate() {
  createForm.month = ''
  createForm.reportNo = ''
  showCreate.value = true
}

async function submitCreate() {
  if (!/^\d{4}-\d{2}$/.test(createForm.month.trim())) {
    notify('统计月份格式应为 YYYY-MM', false)
    return
  }
  const values: Record<string, string> = { 统计月份: createForm.month.trim() }
  if (createForm.reportNo.trim()) values['月报编号'] = createForm.reportNo.trim()
  const result = await postAction(ENDPOINT, { values })
  notify(result.message, !!result.ok)
  if (result.ok) {
    showCreate.value = false
    await reload()
  }
}

function openReview(row: Row, reject: boolean) {
  reviewTarget.value = row
  reviewReject.value = reject
  reviewConclusion.value = reject ? '' : '复核通过：指标与各模块明细一致。'
}

async function submitReview() {
  if (!reviewTarget.value) return
  const result = await postAction(`${ENDPOINT}/${reviewTarget.value.id}/actions`, {
    values: { action: 'review', reject: reviewReject.value, conclusion: reviewConclusion.value },
  })
  notify(result.message, !!result.ok)
  if (result.ok) {
    reviewTarget.value = null
    await Promise.all([reload(), reloadArchives()])
  }
}

function openDetail(row: Row) {
  detailTarget.value = row
}

async function openArchiveById(reportId: number | string | null) {
  if (reportId === null || reportId === '') return
  const response = await request(`${ENDPOINT}/archives/${reportId}`)
  if (!response.ok) {
    notify('归档台账中不存在该月报', false)
    return
  }
  archiveTarget.value = await response.json()
}

async function openArchive(row: Row) {
  await openArchiveById(Number(row['月报ID']))
}

async function publishCaliber() {
  const result = await postAction(`${ENDPOINT}/calibers`, {
    values: {
      generation_source: caliberForm.generation_source,
      pr_basis: caliberForm.pr_basis,
      peak_sun_hours: caliberForm.peak_sun_hours,
      downtime_sources: caliberForm.downtime_sources,
    },
    remark: caliberForm.remark,
  })
  notify(result.message, !!result.ok)
  if (result.ok) {
    caliberForm.remark = ''
    await Promise.all([reloadCalibers(), reload()])
  }
}

function detailPairs(row: Row): [string, unknown][] {
  return [
    ['月报编号', row['月报编号']],
    ['月报状态', row['月报状态']],
    ['版本号', row['版本号']],
    ['口径版本', `v${row['口径版本']}`],
    ['发电量(kWh)', row['发电量']],
    ['装机容量(kW)', row['装机容量']],
    ['等效利用小时(h)', row['等效利用小时']],
    ['综合效率PR(%)', row['综合效率PR']],
    ['设备可利用率(%)', row['设备可利用率']],
    ['故障停机时间(h)', row['故障停机时间']],
    ['复核结论', row['复核结论']],
    ['最近重算时间', row['最近重算时间']],
    ['计算依据', JSON.stringify(row['计算依据'], null, 2)],
  ]
}

function archivePairs(row: Row): [string, unknown][] {
  return [
    ['月报编号', row['月报编号']],
    ['统计月份', row['统计月份']],
    ['月报状态', row['月报状态']],
    ['口径版本（已冻结）', `v${row['口径版本']}`],
    ['发电量(kWh)', row['发电量']],
    ['装机容量(kW)', row['装机容量']],
    ['等效利用小时(h)', row['等效利用小时']],
    ['综合效率PR(%)', row['综合效率PR']],
    ['设备可利用率(%)', row['设备可利用率']],
    ['故障停机时间(h)', row['故障停机时间']],
    ['复核结论', row['复核结论']],
    ['复核时间', row['复核时间']],
    ['归档时间', row['归档时间']],
    ['计算依据', JSON.stringify(row['计算依据'], null, 2)],
  ]
}

function sourceLabel(value: string) {
  return value === 'inverter' ? '逆变器日均汇总' : '关口表计'
}
function basisLabel(value: string) {
  return value === 'irradiance' ? '实测辐照度折算' : '峰值日照小时'
}
function downtimeLabel(value: string) {
  return value === 'alarm' ? '未闭环告警' : '未闭环缺陷'
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

onMounted(async () => {
  await Promise.all([reload(), reloadArchives()])
})
</script>
