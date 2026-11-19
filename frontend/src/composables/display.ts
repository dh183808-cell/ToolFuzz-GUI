import type { Data, RunResult } from '@/types/api'
export const mapping = (value: unknown): Data => value && typeof value === 'object' && !Array.isArray(value) ? value as Data : {}
export const records = (value: unknown): Data[] => Array.isArray(value) ? value.filter(item => item && typeof item === 'object' && !Array.isArray(item)) : []
export const text = (value: unknown): string => value === undefined || value === null || value === '' ? '未记录' : typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)
export function trace(value: unknown): Data[] {
  if (typeof value === 'string') { try { value = JSON.parse(value) } catch { return [] } }
  return Array.isArray(value) ? records(value) : mapping(value).role ? [mapping(value)] : []
}
export function cases(run: RunResult) {
  return run.test_type?.toLowerCase() === 'correctness' ? records(run.results).flatMap(group => records(group.individual_run_test_results)) : records(run.results)
}
export function metrics(run: RunResult): { label: string; value: unknown }[] {
  const summary = mapping(run.summary)
  return run.test_type?.toLowerCase() === 'correctness'
    ? [{ label: 'Prompt Set 数', value: summary.prompt_set_count }, { label: '实际 Prompt 数', value: summary.prompt_count }, { label: '工具失败数', value: summary.tool_failure_count }]
    : [{ label: '实际用例数', value: summary.test_count }, { label: '成功触发数', value: summary.successful_triggers }]
}
export function modelSummary(config: unknown): string {
  return Object.entries(mapping(mapping(config).model_roles)).map(([role, val]) => {
    const item = mapping(val)
    return `${role}: ${text(item.provider)} / ${text(item.model_id)}`
  }).join(' · ') || '未记录角色配置'
}
