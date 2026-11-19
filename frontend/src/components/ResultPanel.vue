<script setup lang="ts">
import { computed } from 'vue'
import { api } from '@/api/client'
import { cases, metrics, modelSummary, records, text, trace } from '@/composables/display'
import type { Data, RunResult } from '@/types/api'
import StatusBadge from './StatusBadge.vue'
const props = defineProps<{ result: RunResult }>()
const kind = computed(() => props.result.test_type?.toLowerCase())
const rows = computed(() => cases(props.result))
const visibleRecord = computed(() => {
  const { cleanup_error: _cleanup, ...record } = props.result as RunResult & { cleanup_error?: unknown }
  const metadata = Object.fromEntries(Object.entries(record.metadata ?? {}).filter(([key]) => !key.startsWith('worker_')))
  return { ...record, metadata }
})
function output(row: Data, field: string, role: string) {
  if (row[field] !== undefined) return row[field]
  return trace(row.trace).filter(event => event.role === role || role === 'assistant' && event.role === 'ai').map(event => event.content)
}
const reports = computed(() => (['json', 'html'] as const).map(type => ({ type, url: api.reportUrl(props.result.artifacts?.[`${type}_path`]) })))
</script>
<template>
  <section class="panel results" aria-label="运行结果">
    <div class="section-heading"><div><p class="eyebrow">RESULT / 测试结果</p><h2>{{ result.test_type ?? '未记录类型' }} · {{ result.tool ?? '未记录工具' }}</h2></div><StatusBadge :status="result.status" /></div>
    <p class="hint">执行完成表示流程结束。工具问题以报告中的实际判定为准。</p>
    <dl class="identity"><div><dt>Task ID</dt><dd>{{ result.metadata?.task_id ?? '未记录' }}</dd></div><div><dt>Core Run ID</dt><dd>{{ result.metadata?.run_id ?? '未记录' }}</dd></div></dl>
    <p class="hint">{{ result.model ?? '未记录模型' }} · {{ modelSummary(result.config) }}</p>
    <p v-if="result.error" class="notice error">执行信息：{{ result.error }}</p>
    <p v-if="result.status === 'cancelled'" class="notice">任务已取消。当前记录可能不完整。</p>
    <div class="metrics"><div v-for="item in metrics(result)" :key="item.label"><span>{{ item.label }}</span><strong>{{ text(item.value) }}</strong></div></div>
    <div class="report-links"><template v-for="report in reports" :key="report.type"><a v-if="report.url" class="button secondary" :href="report.url" :download="report.type === 'json' ? (result.test_type ?? 'report') + '.json' : undefined" target="_blank" rel="noopener noreferrer">{{ report.type === 'html' ? '打开 HTML 报告 ↗' : '下载 JSON ↗' }}</a><span v-else class="hint">{{ report.type.toUpperCase() }} 报告不可用</span></template></div>
    <p v-if="result.artifacts?.save_error" class="notice error">报告保存信息：{{ result.artifacts.save_error }}</p>
    <div v-if="kind === 'correctness'" class="groups"><details v-for="(group, index) in records(result.results)" :key="index"><summary>Prompt Set {{ index + 1 }} · {{ text(group.template_question) }}</summary><p>预期回答</p><pre>{{ text(group.llm_output_expectation) }}</pre><p>参数不一致：{{ text(group.tool_arguments_inconsistency) }} · 输出不一致：{{ text(group.tool_output_inconsistency) }}</p></details></div>
    <h3>用例详情 / Cases <span class="hint">{{ rows.length }} 条已保存用例</span></h3>
    <p v-if="!rows.length" class="empty">没有可用用例，可能尚未生成结果或任务已中止。</p>
    <details v-for="(row, index) in rows" :key="index" class="case">
      <summary><span>用例 {{ String(index + 1).padStart(2, '0') }}</span><span>{{ kind === 'correctness' ? `工具失败：${text(row.tool_failure)}` : `成功触发：${text(row.successful_trigger)}` }}</span></summary>
      <h4>Prompt</h4><pre>{{ text(row.prompt) }}</pre>
      <div class="grid two"><div><h4>工具参数</h4><pre>{{ text(kind === 'correctness' ? row.tool_arguments : row.invocation_params) }}</pre><template v-if="kind === 'runtime'"><h4>Fuzzed 参数</h4><pre>{{ text(row.fuzzed_params) }}</pre><h4>预期异常</h4><pre>{{ text(row.expected_exception) }}</pre></template></div><div><h4>工具输出</h4><pre>{{ text(output(row, 'tool_output', 'tool')) }}</pre><h4>Agent 输出</h4><pre>{{ text(output(row, 'agent_output', 'assistant')) }}</pre><h4>{{ kind === 'runtime' ? '实际异常' : '评判说明' }}</h4><pre>{{ text(kind === 'runtime' ? row.exception : row.llm_agent_out_reason) }}</pre><p v-if="kind === 'correctness'">Agent 输出异常：{{ text(row.unexpected_agent_output) }}</p></div></div>
      <h4>Agent Trace</h4><p v-if="!trace(row.trace).length" class="hint">Trace 未记录或无法解析，可查看原始用例。</p>
      <ol class="trace"><li v-for="(event, eventIndex) in trace(row.trace)" :key="eventIndex"><strong>{{ text(event.role) }}</strong><pre>{{ text(event.content) }}</pre><pre v-if="event.tool_calls">工具调用：{{ text(event.tool_calls) }}</pre></li></ol>
      <details><summary>原始用例 JSON</summary><pre>{{ text(row) }}</pre></details>
    </details>
    <details class="inset"><summary>运行结果 JSON</summary><pre>{{ text(visibleRecord) }}</pre></details>
  </section>
</template>
