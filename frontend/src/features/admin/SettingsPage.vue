<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import ErrorState from '../../components/ErrorState.vue'
import ConfirmDialog from '../../components/ConfirmDialog.vue'
import { showToast } from '../../services/toast'
import { applySettings, snapshotSettings, useSettings } from '../../composables/useSettings'
import {
  getMe,
  getSystemSettings,
  updateSystemSettings,
  resetSystemSettings,
  type SettingsNamespace,
  type SystemSettings
} from '../../services/api'

const router = useRouter()
const { settings, defaults } = useSettings()

const loading = ref(true)
const error = ref('')
const saving = ref<SettingsNamespace | null>(null)
const activeTab = ref<SettingsNamespace>('org')

/** Editable copy; only written back on save so an accidental edit is discardable. */
const draft = ref<SystemSettings>(snapshotSettings())

const showReset = ref(false)
const resetTarget = ref<SettingsNamespace>('org')

const TABS: Array<{ key: SettingsNamespace; label: string; hint: string }> = [
  { key: 'org', label: '机构标识', hint: '学校名称、品牌与心理辅导室联系方式' },
  { key: 'care', label: '关怀词表', hint: '跟进、回访与关闭档案时的可选内容' },
  { key: 'export', label: '导出与敏感查看', hint: '受控导出用途、重点题查看原因' },
  { key: 'cadence', label: '跟进节奏', hint: '新建记录时默认的下次日期' },
  { key: 'ui', label: '界面阈值', hint: '告警与高亮的判定阈值' }
]

const currentTab = computed(() => TABS.find(t => t.key === activeTab.value)!)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const me = await getMe()
    if (me.role_code !== 'admin') {
      await router.push('/login')
      return
    }
    const response = await getSystemSettings()
    applySettings(response.values)
    draft.value = snapshotSettings()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

/** True when the draft differs from the shipped default for this key. */
function isCustomised(namespace: SettingsNamespace, key: string) {
  const current = (draft.value[namespace] as Record<string, unknown>)[key]
  const shipped = (defaults.value[namespace] as Record<string, unknown>)[key]
  return JSON.stringify(current) !== JSON.stringify(shipped)
}

/** Unsaved edits — gates the Save button. */
function dirtyFor(namespace: SettingsNamespace) {
  return JSON.stringify(draft.value[namespace]) !== JSON.stringify(settings.value[namespace])
}

/**
 * Stored values that differ from the shipped defaults — gates "restore".
 *
 * Distinct from `dirtyFor`: after saving a change the draft is clean, but the
 * stored value still deviates from the default and must remain restorable.
 */
function hasOverrides(namespace: SettingsNamespace) {
  return JSON.stringify(settings.value[namespace]) !== JSON.stringify(defaults.value[namespace])
}

async function save(namespace: SettingsNamespace) {
  saving.value = namespace
  try {
    const result = await updateSystemSettings(namespace, draft.value[namespace])
    const merged = { ...settings.value, [namespace]: result.values } as SystemSettings
    applySettings(merged)
    draft.value = snapshotSettings()
    showToast('success', `${TABS.find(t => t.key === namespace)!.label} 已保存`)
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = null
  }
}

function askReset(namespace: SettingsNamespace) {
  resetTarget.value = namespace
  showReset.value = true
}

async function confirmReset() {
  const namespace = resetTarget.value
  try {
    const result = await resetSystemSettings(namespace)
    const merged = { ...settings.value, [namespace]: result.values } as SystemSettings
    applySettings(merged)
    draft.value = snapshotSettings()
    showToast('success', '已恢复默认值')
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '重置失败')
  }
}

/** List fields are edited as one-per-line text. */
function listToText(list: string[]) {
  return list.join('\n')
}

function textToList(text: string) {
  return text.split('\n').map(line => line.trim()).filter(Boolean)
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">系统治理</div>
        <h1>系统配置</h1>
        <p class="page-desc">修改后立即对所有用户生效，并写入审计日志。留空的项使用系统默认值。</p>
      </div>
    </div>

    <SkeletonBlock v-if="loading" variant="cards" :rows="2" />
    <ErrorState v-if="error && !loading" :message="error" :on-retry="load" />

    <template v-if="!loading && !error">
      <div class="tabs">
        <button
          v-for="tab in TABS"
          :key="tab.key"
          :class="['tab', { active: activeTab === tab.key }]"
          @click="activeTab = tab.key"
        >
          {{ tab.label }}
          <span v-if="dirtyFor(tab.key)" class="tab-dot" title="有未保存的修改">•</span>
        </button>
      </div>

      <p class="muted tiny" style="margin-bottom:14px">{{ currentTab.hint }}</p>

      <div class="card pad">
        <!-- 机构标识 -->
        <div v-if="activeTab === 'org'" class="settings-grid">
          <label v-for="field in [
            { key: 'school_name', label: '学校名称' },
            { key: 'brand_name', label: '平台名称' },
            { key: 'brand_subtitle', label: '平台副标题' },
            { key: 'counselling_room', label: '心理辅导室位置' },
            { key: 'counselling_hours', label: '开放时间' },
            { key: 'counselling_contact', label: '校内联系方式' }
          ]" :key="field.key" class="field">
            <span>
              {{ field.label }}
              <span v-if="isCustomised('org', field.key)" class="customised-mark" title="已自定义">已改</span>
            </span>
            <input v-model="(draft.org as any)[field.key]" type="text" />
          </label>
        </div>

        <!-- 关怀词表 -->
        <div v-else-if="activeTab === 'care'" class="settings-grid">
          <label v-for="field in [
            { key: 'follow_up_types', label: '跟进类型' },
            { key: 'contact_channels', label: '家庭回访方式' },
            { key: 'contact_results', label: '联系结果' },
            { key: 'support_statuses', label: '家庭支持情况' },
            { key: 'close_reasons', label: '关闭档案原因' },
            { key: 'retest_reasons', label: '复测原因' },
            { key: 'review_results', label: '人工复核结果' },
            { key: 'review_actions', label: '人工复核的下一步安排' }
          ]" :key="field.key" class="field settings-list-field">
            <span>
              {{ field.label }}
              <span v-if="isCustomised('care', field.key)" class="customised-mark">已改</span>
              <span class="muted tiny">每行一项</span>
            </span>
            <textarea
              :value="listToText((draft.care as any)[field.key])"
              rows="5"
              @input="(draft.care as any)[field.key] = textToList(($event.target as HTMLTextAreaElement).value)"
            />
          </label>
        </div>

        <!-- 导出与敏感查看 -->
        <div v-else-if="activeTab === 'export'" class="settings-grid">
          <label v-for="field in [
            { key: 'purposes', label: '受控导出用途' },
            { key: 'key_question_reasons', label: '重点题二次查看原因' }
          ]" :key="field.key" class="field settings-list-field">
            <span>
              {{ field.label }}
              <span v-if="isCustomised('export', field.key)" class="customised-mark">已改</span>
              <span class="muted tiny">每行一项</span>
            </span>
            <textarea
              :value="listToText((draft.export as any)[field.key])"
              rows="5"
              @input="(draft.export as any)[field.key] = textToList(($event.target as HTMLTextAreaElement).value)"
            />
          </label>
          <div class="notice warn">
            这两项会直接写入审计日志的「用途」字段。修改后历史记录的原文不会改变。
          </div>
        </div>

        <!-- 跟进节奏 -->
        <div v-else-if="activeTab === 'cadence'" class="settings-grid">
          <label v-for="field in [
            { key: 'follow_up_days', label: '下次跟进默认间隔（天）' },
            { key: 'family_contact_days', label: '下次家庭联系默认间隔（天）' },
            { key: 'retest_days', label: '复测计划默认间隔（天）' },
            { key: 'task_duration_days', label: '新建测评任务默认时长（天）' }
          ]" :key="field.key" class="field">
            <span>
              {{ field.label }}
              <span v-if="isCustomised('cadence', field.key)" class="customised-mark">已改</span>
            </span>
            <input v-model.number="(draft.cadence as any)[field.key]" type="number" min="1" max="365" />
          </label>
          <div class="notice">
            这些值只影响<strong>新建记录时的默认日期</strong>，不会改动已有记录。
          </div>
        </div>

        <!-- 界面阈值 -->
        <div v-else class="settings-grid">
          <label v-for="field in [
            { key: 'low_completion_threshold', label: '完成率告警阈值（%）' },
            { key: 'dimension_high_threshold', label: '维度高亮阈值（高分占比 %）' },
            { key: 'seconds_per_question', label: '每题预计耗时（秒）' }
          ]" :key="field.key" class="field">
            <span>
              {{ field.label }}
              <span v-if="isCustomised('ui', field.key)" class="customised-mark">已改</span>
            </span>
            <input v-model.number="(draft.ui as any)[field.key]" type="number" min="1" max="100" />
          </label>
          <div class="notice">
            前一版界面里的完成率阈值 80 与维度高亮 30 曾分别写在两个文件里，其中一个还是裸字面量。
            现在两者都由这里统一控制。
          </div>
        </div>

        <div class="settings-actions">
          <button class="btn" :disabled="!hasOverrides(activeTab)" @click="askReset(activeTab)">
            恢复默认
          </button>
          <button
            class="btn primary"
            :disabled="!dirtyFor(activeTab) || saving === activeTab"
            @click="save(activeTab)"
          >
            {{ saving === activeTab ? '正在保存…' : '保存' }}
          </button>
        </div>
      </div>
    </template>

    <ConfirmDialog
      :open="showReset"
      title="恢复默认值"
      :message="`将把「${TABS.find(t => t.key === resetTarget)?.label}」的全部选项恢复为系统默认，未保存的修改也会丢失。确认继续？`"
      danger
      confirm-text="恢复默认"
      @confirm="confirmReset"
      @update:open="showReset = $event"
    />

  </div>
</template>
