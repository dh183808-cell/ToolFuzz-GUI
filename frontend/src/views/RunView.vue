<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { api, errorMessage } from '@/api/client'
import ResultPanel from '@/components/ResultPanel.vue'
import type { RunResult } from '@/types/api'
const route = useRoute(), result = ref<RunResult | null>(null), error = ref(''), busy = ref(false)
let generation = 0, controller: AbortController | undefined
watch(() => route.params.runId, async (id) => {
  const ticket = ++generation; controller?.abort(); controller = new AbortController()
  result.value = null; error.value = ''; busy.value = true
  try { const record = await api.run(String(id), controller.signal); if (ticket === generation) result.value = record }
  catch (err) { if (ticket === generation) error.value = errorMessage(err) }
  finally { if (ticket === generation) busy.value = false }
}, { immediate: true })
onBeforeUnmount(() => { generation++; controller?.abort() })
</script>
<template><div class="page"><div class="page-heading"><div><p class="eyebrow">RUN DETAIL / 运行详情</p><h1>实验记录</h1></div><RouterLink to="/history">← 返回 History</RouterLink></div><p v-if="busy" class="empty">正在读取运行记录…</p><p v-if="error" role="alert" class="notice error">{{ error }}</p><ResultPanel v-if="result" :result="result" /></div></template>
