<template>
  <div class="notice privacy">
    <strong>心理测评解释与隐私提示</strong>
    <div><slot>统计结果仅用于心理健康教育的筛查参考，不构成医学诊断；小样本和组合筛选必须由正式后端进行隐私校验。筛查结果不等同于心理诊断。</slot></div>
  </div>
</template>

<style scoped>
.privacy { margin: 16px 0; line-height: 1.8 }
</style>
