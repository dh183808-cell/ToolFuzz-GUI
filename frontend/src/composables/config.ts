import type { ModelConfig, ModelDraft, Provider, Role, TaskInput, TestType } from '@/types/api'

export const defaultModel = (): ModelDraft => ({ provider: 'deepseek', model_id: 'deepseek-v4-flash', temperature: 0, timeout: 60, max_retries: 0, credential_source: 'direct', temporary_key: '' })
export interface FormConfig { test_type: TestType; tool_id: string; iterations: number; mode: 'simple' | 'advanced'; shared: ModelDraft; prompt: ModelDraft; agent: ModelDraft; judge: ModelDraft }
export const defaultForm = (): FormConfig => ({ test_type: 'Runtime', tool_id: '', iterations: 1, mode: 'simple', shared: defaultModel(), prompt: defaultModel(), agent: defaultModel(), judge: defaultModel() })
export const roleNames: Record<Role, string> = { prompt: 'Prompt · 输入生成', agent: 'Agent · 工具使用', judge: 'Judge · 正确性评判' }

export function buildInput(form: FormConfig, providers: Provider[], tools: { id: string }[]): TaskInput {
  if (!tools.some(tool => tool.id === form.tool_id)) throw new Error('请选择测试工具。')
  if (!Number.isSafeInteger(form.iterations) || form.iterations < 1) throw new Error('Iterations 必须为正整数。')
  function model(raw: ModelDraft): ModelConfig {
    const provider = providers.find(item => item.id === raw.provider)
    if (!provider || !raw.model_id.trim() || /[\u0000-\u001f]/.test(raw.model_id)) throw new Error('请选择 Provider 并填写 Model ID。')
    if (!Number.isFinite(raw.temperature) || raw.temperature < 0 || raw.temperature > 2) throw new Error('Temperature 必须在 0–2 之间。')
    if (!Number.isFinite(raw.timeout) || raw.timeout <= 0) throw new Error('Timeout 必须大于 0。')
    if (!Number.isSafeInteger(raw.max_retries) || raw.max_retries < 0) throw new Error('Max Retries 必须为非负整数。')
    const url = raw.base_url?.trim()
    if (provider.requires_base_url && !url) throw new Error('Custom Provider 需要 Base URL。')
    if (url) {
      let parsed: URL
      try { parsed = new URL(url) } catch { throw new Error('Base URL 必须是有效的 HTTP(S) 地址。') }
      if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || /[?#\\\s]/.test(url) || /%/.test(parsed.host)) throw new Error('Base URL 不允许包含凭证、查询参数或片段。')
    }
    if (raw.credential_source === 'direct' && !raw.temporary_key.trim()) throw new Error('请粘贴 API Key，或切换为使用服务器环境变量。')
    const env = raw.credential_source === 'direct' && provider.requires_base_url ? 'TOOLFUZZ_TEMPORARY_API_KEY' : raw.api_key_env?.trim()
    if ((provider.requires_base_url && !env) || (env && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(env))) throw new Error('请填写 API Key 的环境变量名称，例如 CUSTOM_API_KEY；不要填写密钥值。')
    // Whitelist fields: no raw key can be submitted via form state extensions.
    return { provider: raw.provider, model_id: raw.model_id.trim(), temperature: raw.temperature, timeout: raw.timeout, max_retries: raw.max_retries, ...(url ? { base_url: url } : {}), ...(env ? { api_key_env: env } : {}) }
  }
  const roles = form.test_type === 'Correctness' ? ['prompt', 'agent', 'judge'] as const : ['prompt', 'agent'] as const
  const entries = roles.map(role => [role, model(form.mode === 'simple' ? form.shared : form[role])])
  const credentials = Object.fromEntries(roles.flatMap(role => {
    const draft = form.mode === 'simple' ? form.shared : form[role]
    return draft.credential_source === 'direct' ? [[role, draft.temporary_key.trim()]] : []
  }))
  return { test_type: form.test_type, tool_id: form.tool_id, iterations: form.iterations, model_roles: Object.fromEntries(entries) as TaskInput['model_roles'], ...(Object.keys(credentials).length ? { temporary_credentials: credentials } : {}) }
}

export function clearKeys(form: FormConfig) {
  for (const role of ['shared', 'prompt', 'agent', 'judge'] as const) form[role].temporary_key = ''
}
