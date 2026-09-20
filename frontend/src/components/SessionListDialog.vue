<script setup lang="ts">
/**
 * 登录会话 —— 「我现在有哪几台设备登着、能不能把别处踢掉」。
 *
 * 这个弹层存在的理由是 §16.5 的另一半：管理员重置密码之后**旧 Token 全部失效、强制
 * 改密**，而那条链路的终点是一次登录。会话可撤销之前，「我在别的电脑上忘了退出」
 * 这个场景在界面上没有任何出路——唯一的办法是改密码，而改密码会把自己也一起踢下去。
 *
 * 三条口径：
 *
 *   1. **「这一台」由服务端算**（`is_current`）。前端手上没有 `jti`，猜错的后果是
 *      用户把自己踢下线，而他会以为自己点的是「踢掉别的设备」。
 *   2. **当前这一台不摆撤销按钮**（服务端其实也拒，回 422）。这里不摆不是省事，是
 *      因为它不是「不能删」而是「该用另一个动作」——撤销了就等于当场退出，那只按钮
 *      在右上角写着「退出」。**灰按钮要说得出为什么**，所以那一行明写这句话。
 *   3. **撤销别的设备要报数**。`revokeOtherSessions` 回的是**真的撤销了几条**，不是
 *      「成功」两个字：0 条与 3 条是两件不同的事，而「已退出其它设备」这句话在 0 条
 *      时是一句假话——用户会以为有人被踢下来了。
 */
import { computed, ref, watch } from 'vue'
import Modal from './Modal.vue'
import ErrorState from './ErrorState.vue'
import SkeletonBlock from './SkeletonBlock.vue'
import { showToast } from '../services/toast'
import { getMySessions, revokeOtherSessions, revokeSession, type SessionItem } from '../services/api'
import { authSessionStatusLabel, authSessionStatusTone } from '../services/labels'

const props = defineProps<{ open: boolean }>()

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void
}>()

const sessions = ref<SessionItem[]>([])
const loading = ref(false)
const error = ref('')
const working = ref(0)

async function load() {
  loading.value = true
  error.value = ''
  try {
    // 取数之前先清空（§14）：上一次打开留下的列表不该挂在下一次的错误下面。
    sessions.value = []
    sessions.value = await getMySessions()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '会话列表加载失败'
  } finally {
    loading.value = false
  }
}

// 每次打开都重取：这个列表的全部意义是「**此刻**有哪几台设备」，缓存一份几个小时前的
// 快照会让用户以为别处已经退出了。
watch(() => props.open, (open) => {
  if (open) load()
})

async function kick(session: SessionItem) {
  working.value = session.id
  try {
    await revokeSession(session.id)
    showToast('success', '该设备已退出登录')
    await load()
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '撤销失败')
    await load()
  } finally {
    working.value = 0
  }
}

async function kickOthers() {
  working.value = -1
  try {
    const revoked = await revokeOtherSessions()
    // 0 条时不说「已退出」——那一句会把「本来就没有别的设备」说成「我刚踢掉了几个」。
    showToast(
      revoked > 0 ? 'success' : 'info',
      revoked > 0 ? `已退出其它 ${revoked} 台设备` : '没有其它登录中的设备'
    )
    await load()
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '操作失败')
  } finally {
    working.value = 0
  }
}

/** `2026-09-19T14:30:00` → `09-19 14:30`。切字符串，不解析（后端发的是本地时间）。 */
function shortMoment(value: string | null): string {
  if (!value) return '—'
  return value.slice(5, 16).replace('T', ' ')
}

function deviceText(session: SessionItem): string {
  const ua = (session.user_agent || '').trim()
  if (!ua) return '未知设备'
  // 原始 UA 有 120 个字，一屏放不下也不给人任何判断依据。截断到能认出「同一台」为止。
  return ua.length > 48 ? `${ua.slice(0, 48)}…` : ua
}

/**
 * 「其它 N 台」这个数**现算**，不读服务端另发的计数：它必须与上面那张表同源
 * （§11）——表里列着 3 条、按钮写着「退出其它 1 台设备」时，人不知道该信哪个。
 * 只数 `ACTIVE` 的：已撤销与已过期的那几条没有可撤销的东西。
 */
const others = computed(
  () => sessions.value.filter((s) => !s.is_current && s.status === 'ACTIVE').length
)
</script>

<template>
  <Modal :model-value="open" title="登录设备" @update:model-value="emit('update:open', $event)">
    <div class="form-grid">
      <p class="muted tiny" style="margin:0">
        这里列出的是<strong>当前还在登录</strong>的设备。在机房或办公室的共用电脑上用完
        忘了退出时，从这里把那一台踢掉即可——对方的 Token 立刻失效，不需要等它过期。
      </p>

      <SkeletonBlock v-if="loading" variant="text" :rows="3" />
      <!-- 错误排在空态之前（§14）。 -->
      <ErrorState v-else-if="error" :message="error" :on-retry="load" />

      <div v-else class="session-list">
        <div v-for="session in sessions" :key="session.id" class="session-row">
          <div class="session-main">
            <div class="session-device">
              {{ deviceText(session) }}
              <span v-if="session.is_current" class="pill blue">这一台</span>
            </div>
            <div class="muted tiny">
              {{ session.ip || '未知地址' }} · 最近活跃 {{ shortMoment(session.last_seen_at) }}
              · 登录于 {{ shortMoment(session.issued_at) }}
            </div>
          </div>
          <div class="session-actions">
            <span class="pill" :class="authSessionStatusTone(session.status)">
              {{ authSessionStatusLabel(session.status) }}
            </span>
            <!-- 当前这一台：不是「不能撤销」，而是「撤销就是退出」——那个动作在右上角
                 写着「退出」。所以这里不给按钮，给指路。 -->
            <span v-if="session.is_current" class="muted tiny">要结束它请用右上角的「退出」</span>
            <button
              v-else-if="session.status === 'ACTIVE'"
              class="btn small danger"
              type="button"
              :disabled="working !== 0"
              @click="kick(session)"
            >
              {{ working === session.id ? '处理中…' : '退出这一台' }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <template #footer>
      <button class="btn" type="button" @click="emit('update:open', false)">关闭</button>
      <button
        class="btn danger"
        type="button"
        :disabled="working !== 0 || loading || others === 0"
        @click="kickOthers"
      >
        {{ working === -1 ? '处理中…' : `退出其它 ${others} 台设备` }}
      </button>
    </template>
  </Modal>
</template>

<style scoped>
.session-list {
  display: grid;
  gap: 10px;
}
.session-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 10px 12px;
}
.session-main {
  min-width: 0;
}
.session-device {
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 7px;
  overflow-wrap: anywhere;
}
.session-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
</style>
