<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { api, errorMessage } from '@/api/client'
import ModelEditor from '@/components/ModelEditor.vue'
import ResultPanel from '@/components/ResultPanel.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { buildInput, clearKeys, defaultForm, roleNames } from '@/composables/config'
import { modelSummary, text } from '@/composables/display'
import { useTask } from '@/composables/task'
import type { Provider, Tool } from '@/types/api'
const form = reactive(defaultForm()), providers = ref<Provider[]>([]), tools = ref<Tool[]>([]), loading = ref(true), message = ref('')
const state = useTask(), { task, result, blocked, busy, cancelling, recovering, error, input } = state
const route = useRoute(), router = useRouter(), controller = new AbortController()
const roles = computed(() => form.test_type === 'Correctness' ? ['prompt', 'agent', 'judge'] as const : ['prompt', 'agent'] as const)
const missing = computed(() => !providers.value.length || !tools.value.length)
onMounted(async () => {
  const saved = typeof route.query.task === 'string' ? route.query.task : state.savedId()
  if (saved) void state.resume(saved)
  try {
    const [p, t] = await Promise.all([api.providers(controller.signal), api.tools(controller.signal)])
    providers.value = p.items; tools.value = t.items; form.tool_id = t.items[0]?.id ?? ''
  } catch (err) { if (!controller.signal.aborted) message.value = errorMessage(err) }
  finally { loading.value = false }
})
onBeforeUnmount(() => { controller.abort(); state.stop(); clearKeys(form) })
onBeforeRouteLeave(() => !busy.value && !cancelling.value)
watch(() => route.query.task, id => {
  if (typeof id === 'string' && id !== task.value?.task_id && !busy.value && !cancelling.value) void state.resume(id)
})
async function start() {
  message.value = ''
  try {
    const value = buildInput(form, providers.value, tools.value)
    clearKeys(form)
    const id = await state.start(value)
    if (id) await router.replace({ path: '/', query: { task: id } })
  } catch (err) { message.value = err instanceof Error ? err.message : '请检查测试参数。' }
}
</script>
<template>
  <div class="page">
    <div class="page-heading"><div><p class="eyebrow">EXPERIMENT / 新建测试</p><h1>测试工作台</h1><p>配置模型与工具，观察工具行为，保留每次实验。</p></div><span class="tag">单任务 · 本地运行</span></div>
    <p v-if="message" role="alert" class="notice error">{{ message }}</p>
    <div class="workspace-grid">
      <form class="panel config-panel" @submit.prevent="start" novalidate>
        <div class="section-heading"><h2>01 · 测试配置</h2><span class="hint">Configuration</span></div>
        <fieldset :disabled="blocked || loading || missing" class="plain-fieldset">
          <label>测试类型<div class="segmented"><button type="button" :class="{ selected: form.test_type === 'Runtime' }" @click="form.test_type = 'Runtime'">Runtime</button><button type="button" :class="{ selected: form.test_type === 'Correctness' }" @click="form.test_type = 'Correctness'">Correctness</button></div></label>
          <div class="grid two inset"><label>测试工具<select v-model="form.tool_id" aria-label="测试工具"><option value="" disabled>请选择工具</option><option v-for="tool in tools" :key="tool.id" :value="tool.id">{{ tool.name }}</option></select></label><label>Iterations<input v-model.number="form.iterations" aria-label="Iterations" type="number" min="1" step="1" /></label></div>
          <p class="hint">{{ form.test_type === 'Runtime' ? 'Runtime：Fuzzer 输入探索迭代' : 'Correctness：Prompt Set 迭代' }}。最终用例数量以实际结果为准。</p>
          <div class="section-heading model-heading"><h2>02 · 模型配置</h2><select v-model="form.mode" aria-label="模型配置模式"><option value="simple">简单模式 · 同一模型</option><option value="advanced">高级模式 · 独立角色</option></select></div>
          <p class="hint">支持本次任务临时密钥，或使用服务器环境变量。仅限可信本机使用。启动真实测试会产生模型费用。</p>
          <ModelEditor v-if="form.mode === 'simple'" v-model="form.shared" :providers="providers" title="所有必需角色使用同一配置" />
          <template v-else><ModelEditor v-for="role in roles" :key="role" v-model="form[role]" :providers="providers" :title="roleNames[role]" /></template>
        </fieldset>
        <button class="primary start-button" type="submit" :disabled="blocked || loading || missing">{{ busy ? '正在提交…' : recovering ? '正在恢复任务状态…' : blocked ? '当前任务尚未结束' : loading ? '加载配置…' : '开始测试 / Start run →' }}</button>
      </form>
      <section class="panel task-panel" aria-label="当前任务">
        <div class="section-heading"><h2>当前任务</h2><StatusBadge v-if="task" :status="task.status" /></div>
        <div v-if="!task" class="empty"><div class="empty-mark">↗</div><h3>准备开始一次实验</h3><p>配置左侧参数后启动测试。运行状态与结果将显示在这里。</p></div>
        <template v-else>
          <dl class="identity vertical"><div><dt>Task ID</dt><dd>{{ task.task_id }}</dd></div><div><dt>Core Run ID</dt><dd>{{ task.run_id ?? '尚未记录' }}</dd></div><div><dt>测试类型 / 工具</dt><dd>{{ task.run_type ?? '未记录' }} · {{ input?.tool_id ?? result?.tool ?? '未记录' }}</dd></div><div><dt>模型配置</dt><dd>{{ modelSummary(input ?? task.config) }}</dd></div><div><dt>运行时间</dt><dd>{{ text(task.duration_seconds) }} 秒</dd></div><div><dt>持久化状态</dt><dd>{{ task.persistence?.status ?? '尚未记录' }}</dd></div></dl>
          <p v-if="task.status === 'running'" class="notice">正在执行，等待后端返回结果。</p>
          <p v-if="task.status === 'cancelling'" class="notice">正在取消任务…</p>
          <p v-if="task.status === 'cancelled'" class="notice">任务已取消。</p>
          <p v-if="task.error" class="notice error">{{ task.error }}</p>
          <p v-if="task.persistence?.error" class="notice error">结果保存失败：{{ task.persistence.error }}</p>
          <details v-if="task.summary && Object.keys(task.summary).length"><summary>可用执行摘要</summary><pre>{{ text(task.summary) }}</pre></details>
          <div class="actions"><button type="button" class="danger" :disabled="task.status !== 'running' || cancelling" @click="state.cancel">{{ cancelling ? '正在提交取消…' : '取消当前任务 / Cancel Task' }}</button><button type="button" :disabled="busy || cancelling" @click="state.resume(task.task_id)">刷新状态</button></div>
        </template>
        <p v-if="error" role="alert" class="notice error">{{ error }}</p>
      </section>
    </div>
    <ResultPanel v-if="result" :result="result" />
  </div>
</template>
