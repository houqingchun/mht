import { readonly, ref } from 'vue'
import {
  getSystemSettings,
  type SettingsNamespace,
  type SystemSettings
} from '../services/api'

/**
 * Operator-editable configuration, loaded once per session and shared.
 *
 * Deliberately non-blocking: every view reads `settings.value.<ns>.<key>` and
 * falls back to a built-in literal while the request is in flight or if it
 * fails. A settings outage must not blank the UI — the same fail-safe stance
 * the backend takes. The literals here mirror `DEFAULTS` in
 * `backend/app/services/settings_service.py`; if they ever diverge the backend
 * still wins on the next successful load.
 */
const FALLBACK: SystemSettings = {
  org: {
    school_name: '青禾实验学校',
    brand_name: '心晴',
    brand_subtitle: '心理测评与关怀平台',
    counselling_room: '综合楼3层305室',
    counselling_hours: '周一至周五 12:30—17:30',
    counselling_contact: '陈老师 · 分机8305'
  },
  care: {
    follow_up_types: ['心理老师访谈', '支持性辅导', '一般观察', '复测沟通', '其他'],
    contact_channels: ['电话', '到校会面', '线上沟通'],
    contact_results: ['已联系', '未接通', '已约时间'],
    support_statuses: ['愿意配合', '需要继续沟通', '信息不足'],
    close_reasons: [
      '完成阶段跟进并进入一般观察',
      '复测结果达到关闭条件',
      '学生已转出',
      '其他经审核原因'
    ],
    retest_reasons: ['重点维度趋势复测', '跟进后效果复测', '效度异常复测'],
    review_results: ['建立持续关注档案', '建议复测', '当前进入一般观察', '信息不足，继续了解'],
    review_actions: ['安排下次跟进', '建议复测', '转介校内资源', '持续观察'],
    case_owner_fallback: '未分配'
  },
  export: {
    purposes: ['校内心理工作跟进', '经批准的工作汇报', '复测任务准备'],
    key_question_reasons: ['执行人工复核', '处置紧急工作事项', '核验历史记录']
  },
  cadence: {
    follow_up_days: 7,
    family_contact_days: 14,
    retest_days: 30,
    task_duration_days: 14,
    reminder_horizon_days: 30
  },
  ui: {
    low_completion_threshold: 80,
    dimension_high_threshold: 30,
    seconds_per_question: 12,
    page_size_default: 20
  }
}

const settings = ref<SystemSettings>(structuredClone(FALLBACK))
const defaults = ref<SystemSettings>(structuredClone(FALLBACK))
const loaded = ref(false)
let inFlight: Promise<void> | null = null

/** Idempotent: concurrent callers share one request. */
export function loadSettings(force = false): Promise<void> {
  if (!force && (loaded.value || inFlight)) return inFlight ?? Promise.resolve()
  inFlight = getSystemSettings()
    .then(response => {
      settings.value = response.values
      defaults.value = response.defaults
      loaded.value = true
    })
    .catch(() => {
      // Keep the fallback values; a settings outage must not break the app.
    })
    .finally(() => {
      inFlight = null
    })
  return inFlight
}

/** Called after a successful save so every consumer sees the new values. */
export function applySettings(values: SystemSettings) {
  settings.value = values
  loaded.value = true
}

/**
 * A mutable deep copy for editing.
 *
 * `settings` is exposed readonly so views can't accidentally mutate shared
 * state — which is exactly what an editor needs to do to a *draft*. Take a
 * snapshot, edit it, then commit through `applySettings`.
 */
export function snapshotSettings(namespace?: SettingsNamespace): SystemSettings {
  const copy = structuredClone(toRawSnapshot())
  return copy
}

function toRawSnapshot(): SystemSettings {
  return JSON.parse(JSON.stringify(settings.value)) as SystemSettings
}

export function useSettings() {
  return {
    settings: readonly(settings),
    defaults: readonly(defaults),
    settingsLoaded: readonly(loaded),
    loadSettings,
    snapshotSettings
  }
}

/** Convenience for a single namespace, e.g. `const org = useNamespace('org')`. */
export function useNamespace<K extends SettingsNamespace>(namespace: K) {
  return {
    get value(): SystemSettings[K] {
      return settings.value[namespace]
    }
  }
}
