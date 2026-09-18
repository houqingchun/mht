<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import ConfirmDialog from '../../components/ConfirmDialog.vue'
import { showToast } from '../../services/toast'
import { ruleStatusLabel, ruleStatusTone } from '../../services/labels'
import { createLatestRequest } from '../../services/latest-request'
import {
  getScaleRule,
  updateScaleRule,
  type ScaleRuleConfig,
  type ScaleRuleSnapshot,
  type ScoreBand
} from '../../services/api'

const props = defineProps<{ scaleId: number | null; scaleVersion: string }>()

const snapshot = ref<ScaleRuleSnapshot | null>(null)
const loading = ref(false)
const saving = ref(false)
const error = ref('')

/** Editable copy; only committed on save. */
const draft = ref<Pick<ScaleRuleConfig, 'validity_retest_threshold' | 'total_levels' | 'dimension_levels'> | null>(null)

const showConfirm = ref(false)

/** 版本下拉框连着切两次（`watch` 每次都会出发一次请求）就会撞上这个竞态。 */
const latest = createLatestRequest()

const isDirty = computed(() => {
  if (!draft.value || !snapshot.value) return false
  const c = snapshot.value.config
  return JSON.stringify({
    t: draft.value.validity_retest_threshold,
    a: draft.value.total_levels,
    b: draft.value.dimension_levels
  }) !== JSON.stringify({ t: c.validity_retest_threshold, a: c.total_levels, b: c.dimension_levels })
})

async function load() {
  if (!props.scaleId) {
    snapshot.value = null
    draft.value = null
    return
  }
  const token = latest.begin()
  loading.value = true
  error.value = ''
  // 先清空再取：换一个版本之后，面板上任何一处都**不许**再留着上一个版本的东西
  // （2026-09-17 补）。此前失败时 `snapshot` 原地不动，于是标题栏写着
  // `MHT-RULE-1.1.0 · 生效中`、正文写着「读取评分规则失败」——读者会以为
  // 这次失败说的是 1.1.0，而它说的是他刚选的那个版本。
  snapshot.value = null
  draft.value = null
  try {
    const data = await getScaleRule(props.scaleId)
    if (!latest.isCurrent(token)) return
    snapshot.value = data
    draft.value = structuredClone({
      validity_retest_threshold: data.config.validity_retest_threshold,
      total_levels: data.config.total_levels,
      dimension_levels: data.config.dimension_levels
    })
  } catch (err) {
    if (!latest.isCurrent(token)) return
    error.value = err instanceof Error ? err.message : '读取评分规则失败'
  } finally {
    if (latest.isCurrent(token)) loading.value = false
  }
}

watch(() => props.scaleId, load, { immediate: true })

function bandKeys(bands: ScoreBand[] | undefined) {
  return bands ?? []
}

function save() {
  if (!draft.value) return
  // Saving a published rule spins a new rule version, which the operator
  // should know before it happens.
  if (snapshot.value?.rule_status !== 'DRAFT') {
    showConfirm.value = true
    return
  }
  void commit()
}

async function commit() {
  if (!props.scaleId || !draft.value) return
  saving.value = true
  try {
    const result = await updateScaleRule(props.scaleId, {
      validity_retest_threshold: draft.value.validity_retest_threshold,
      total_levels: draft.value.total_levels,
      dimension_levels: draft.value.dimension_levels
    })
    showToast(
      'success',
      result.created_new_version
        ? `已创建新的规则版本 ${result.rule_version}，历史结果不受影响`
        : `已更新 ${result.rule_version}`
    )
    await load()
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

/** Every band must be contiguous and cover the whole range with no gap. */
const bandWarning = computed(() => {
  if (!draft.value) return ''
  const check = (label: string, bands: ScoreBand[]) => {
    if (!bands.length) return `${label}：至少需要一个分段`
    const ordered = [...bands].sort((a, b) => a.min - b.min)
    if (ordered[0].min !== 0) return `${label}：必须从 0 分开始`
    for (let i = 1; i < ordered.length; i += 1) {
      if (ordered[i].min !== ordered[i - 1].max + 1) {
        return `${label}：${ordered[i - 1].code} 与 ${ordered[i].code} 之间存在断档或重叠`
      }
    }
    return ''
  }
  return check('总分', draft.value.total_levels) || check('维度', draft.value.dimension_levels)
})
</script>

<template>
  <section class="card pad">
    <div class="card-head" style="padding: 0 0 14px; border: none">
      <h2>评分规则</h2>
      <span v-if="snapshot" class="muted tiny">
        {{ snapshot.rule_version }} ·
        <span :class="['pill', ruleStatusTone(snapshot.rule_status)]">{{
          ruleStatusLabel(snapshot.rule_status)
        }}</span>
      </span>
    </div>

    <p v-if="loading" class="muted tiny">正在读取评分规则…</p>
    <p v-else-if="error" class="form-error">{{ error }}</p>

    <template v-else-if="draft">
      <div class="notice" style="margin-bottom: 16px">
        这些阈值决定哪些学生被标记为需要关注。修改后会<strong>生成新的规则版本</strong>，
        已有测评结果仍记录在原规则版本下，不会被追溯改写。
      </div>

      <div class="field" style="max-width: 320px">
        <span>效度重测阈值<span class="muted tiny">　效度分 ≥ 此值判定为建议重测</span></span>
        <input v-model.number="draft.validity_retest_threshold" type="number" min="1" max="20" />
      </div>

      <div class="grid two" style="margin-top: 18px">
        <div>
          <h3 class="rule-heading">总分分段</h3>
          <div class="band-row band-header">
            <span>分类代码</span><span>起始分</span><span>结束分</span>
          </div>
          <div v-for="(band, index) in bandKeys(draft.total_levels)" :key="index" class="band-row">
            <input v-model="band.code" type="text" />
            <input v-model.number="band.min" type="number" min="0" />
            <input v-model.number="band.max" type="number" min="0" />
          </div>
        </div>
        <div>
          <h3 class="rule-heading">维度分段</h3>
          <div class="band-row band-header">
            <span>分类代码</span><span>起始分</span><span>结束分</span>
          </div>
          <div v-for="(band, index) in bandKeys(draft.dimension_levels)" :key="index" class="band-row">
            <input v-model="band.code" type="text" />
            <input v-model.number="band.min" type="number" min="0" />
            <input v-model.number="band.max" type="number" min="0" />
          </div>
        </div>
      </div>

      <p v-if="bandWarning" class="form-error" style="margin-top: 14px">{{ bandWarning }}</p>

      <div class="settings-actions">
        <button class="btn" :disabled="!isDirty" @click="load">放弃修改</button>
        <button class="btn primary" :disabled="!isDirty || saving || !!bandWarning" @click="save">
          {{ saving ? '正在保存…' : '保存规则' }}
        </button>
      </div>
    </template>

    <ConfirmDialog
      :open="showConfirm"
      title="保存评分规则"
      :message="`当前规则 ${snapshot?.rule_version} 已发布，保存将创建新的规则版本，并立即用于之后提交的测评。已有结果不受影响。确认继续？`"
      confirm-text="创建新版本并保存"
      @confirm="commit"
      @update:open="showConfirm = $event"
    />
  </section>
</template>
