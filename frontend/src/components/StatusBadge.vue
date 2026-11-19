<script setup lang="ts">
defineProps<{ status?: string }>()
const labels: Record<string, string> = { running: '正在执行', cancelling: '正在取消…', cancelled: '任务已取消', completed: '执行完成', failed: '执行失败' }
</script>
<template><span class="badge" :class="status">{{ labels[status ?? ''] ?? status ?? '未记录' }}</span></template>
