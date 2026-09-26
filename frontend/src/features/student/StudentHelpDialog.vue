<script setup lang="ts">
/**
 * 「我想找人聊聊」——学生端三个页面共用的同一个弹层。
 *
 * 此前它是**三份复制**（首页、完成记录、答题页），三份的正文各写各的：
 * 首页与记录页给的是学校配置的那三行，答题页多一句紧急提示。于是同一句话
 * 在学生手上出现了两个版本，而「求助入口三处一致」这件事没有任何东西保证
 * ——加到第四页时，谁也不会知道该抄哪一份。
 *
 * 三条约定：
 *
 * 1. **不虚构联系方式。** 三行只在**学校真的配过**（非空）时才出现。出厂值
 *    里那三格是空的（见 `settings_service.DEFAULTS` 那段注释），所以一所没配过
 *    的学校，学生看到的只有通用指引——可信的成年人、家长、学校心理老师、
 *    当地紧急服务。**绝不 `?? '请咨询心理老师'` 之类地补一个默认值**：一个
 *    编出来的辅导室会让学生白跑一趟，比什么都不写更糟。
 * 2. **紧急提示是行动语，不是诊断语。** 「如果你现在感到不安全，或有过伤害
 *    自己的念头，请立刻告诉身边可信任的成年人」——说的是**做什么**：找谁、
 *    去哪里。产品边界是筛查与关怀，不是诊断，所以这里不出现任何症状名、
 *    不做任何判断、也不承诺后果。
 * 3. **不写电话号码。** 除学校自己填的那一格之外，一个号码都不写。当地紧急
 *    号码随地区不同，写死一个在这里，写错的代价正好落在最需要它的那一刻。
 */
import { computed } from 'vue'
import Modal from '../../components/Modal.vue'
import { useSettings } from '../../composables/useSettings'

defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'update:open', value: boolean): void }>()

const { settings } = useSettings()

/**
 * 学校配过的那些行。**空值整行不出现**（而不是渲染成一行空白的「开放时间」）：
 * 一格空的 `—` 会让人以为「学校有辅导室，只是我不知道时间」，而事实是这一项
 * 学校没有提供。判据是 `trim()`——一个只有空格的配置和没填是一样的。
 */
const contactRows = computed(() =>
  [
    { label: '心理辅导室', value: settings.value.org.counselling_room },
    { label: '开放时间', value: settings.value.org.counselling_hours },
    { label: '校内联系', value: settings.value.org.counselling_contact }
  ].filter((row) => (row.value || '').trim() !== '')
)
</script>

<template>
  <Modal
    :model-value="open"
    title="我想找人聊聊"
    size="md"
    @update:model-value="emit('update:open', $event)"
  >
    <div class="form-grid">
      <div class="notice">
        填写这些题目时如果感到不舒服，可以随时停下来。你可以找学校心理老师、家长，
        或者任何一位你信得过的成年人聊一聊——这不是一件需要独自扛着的事。
      </div>

      <template v-if="contactRows.length">
        <div class="detail-grid" style="margin-top: 14px">
          <div v-for="row in contactRows" :key="row.label" class="detail-row">
            <span>{{ row.label }}</span><b>{{ row.value }}</b>
          </div>
        </div>
        <p class="muted tiny">
          上面的联系方式由学校填写。如果和你知道的情况不一样，以学校现在的情况为准。
        </p>
      </template>

      <!-- 学校没配过任何一项时的通用指引。这不是「兜底文案」，它是这一档
           唯一诚实的输出：我们确实不知道这所学校的辅导室在哪、谁在值班。 -->
      <p v-else class="muted tiny">
        学校还没有在这里填写心理辅导室的位置和联系方式。你可以直接问班主任或心理老师，
        他们知道该找谁、在哪儿谈。
      </p>

      <div class="notice warn" style="margin-top: 14px">
        如果你现在感到不安全，或者有过伤害自己的想法，请立刻告诉身边可信任的成年人
        （家长、老师、学校心理老师），或者联系当地的紧急服务。<b>现在就可以去说，不用等到测评结束。</b>
      </div>
    </div>
  </Modal>
</template>
