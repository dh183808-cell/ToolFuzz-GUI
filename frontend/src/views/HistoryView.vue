<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { api, errorMessage } from '@/api/client'
import StatusBadge from '@/components/StatusBadge.vue'
import type { RunSummary } from '@/types/api'
const items = ref<RunSummary[]>([]), total = ref(0), offset = ref(0), filter = ref(''), busy = ref(false), error = ref('')
const limit = 10
let request: AbortController | undefined, generation = 0
async function load() {
  const ticket = ++generation; request?.abort(); request = new AbortController()
  busy.value = true; error.value = ''
  try {
    const page = await api.runs({ limit, offset: offset.value, test_type: filter.value }, request.signal)
    if (ticket !== generation) return
    if (page.total > 0 && offset.value >= page.total) { offset.value = Math.floor((page.total - 1) / limit) * limit; return }
    items.value = page.items; total.value = page.total
  } catch (err) { if (ticket === generation) { items.value = []; error.value = errorMessage(err) } }
  finally { if (ticket === generation) busy.value = false }
}
watch(filter, () => { if (offset.value) offset.value = 0; else void load() })
watch(offset, load)
void load()
onBeforeUnmount(() => { generation++; request?.abort() })
</script>
<template>
  <div class="page"><div class="page-heading"><div><p class="eyebrow">ARCHIVE / 历史运行</p><h1>历史记录</h1><p>重新打开已保存的实验，查看结果与执行轨迹。</p></div><button :disabled="busy" @click="load">刷新记录 ↻</button></div>
    <section class="panel"><div class="section-heading"><h2>{{ total }} 次已保存运行</h2><label class="inline-label">测试类型<select v-model="filter" aria-label="历史测试类型"><option value="">全部类型</option><option value="runtime">Runtime</option><option value="correctness">Correctness</option></select></label></div>
      <p v-if="error" role="alert" class="notice error">{{ error }}</p><p v-if="busy" class="empty">正在加载历史记录…</p>
      <div v-else-if="items.length" class="table-scroll"><table><thead><tr><th>时间 / 类型</th><th>工具 / 模型</th><th>执行状态</th><th>持久化</th><th>运行标识</th><th></th></tr></thead><tbody><tr v-for="item in items" :key="`${item.task_id}:${item.run_id}`"><td>{{ item.started_at ? new Date(item.started_at).toLocaleString('zh-CN') : '未记录时间' }}<small>{{ item.run_type ?? '未记录类型' }}</small></td><td>{{ item.tool ?? '未记录工具' }}<small>{{ item.model ?? '未记录模型' }}</small></td><td><StatusBadge :status="item.status" /></td><td>{{ item.persistence?.status ?? '未记录' }}</td><td class="ids">Task: {{ item.task_id ?? '未记录' }}<small>Run: {{ item.run_id }}</small></td><td><RouterLink :to="`/runs/${encodeURIComponent(item.run_id)}`">查看详情 →</RouterLink></td></tr></tbody></table></div>
      <p v-else-if="!error" class="empty">暂无匹配的历史记录。完成的运行会显示在这里。</p>
      <div class="pagination"><span class="hint">按测试时间从新到旧 · 第 {{ Math.floor(offset / limit) + 1 }} / {{ Math.max(1, Math.ceil(total / limit)) }} 页</span><div class="actions"><button :disabled="busy || offset === 0" @click="offset -= limit">上一页</button><button :disabled="busy || offset + limit >= total" @click="offset += limit">下一页</button></div></div>
    </section>
  </div>
</template>
