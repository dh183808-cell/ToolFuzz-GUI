export type TestType = 'Runtime' | 'Correctness'
export type TaskStatus = 'running' | 'cancelling' | 'cancelled' | 'completed' | 'failed'
export type Role = 'prompt' | 'agent' | 'judge'
export type Data = Record<string, unknown>
export interface Provider { id: string; name: string; default_base_url: string | null; requires_base_url: boolean; credentials_configured: boolean }
export interface Tool { id: string; name: string }
export interface ModelConfig { provider: string; model_id: string; base_url?: string; api_key_env?: string; temperature: number; timeout: number; max_retries: number }
export interface ModelDraft extends ModelConfig { credential_source: 'direct' | 'environment'; temporary_key: string }
export interface TaskInput { test_type: TestType; tool_id: string; iterations: number; model_roles: { prompt: ModelConfig; agent: ModelConfig; judge?: ModelConfig }; temporary_credentials?: Partial<Record<Role, string>> }
export interface Persistence { status?: string; error?: string | null }
export interface Task { task_id: string; run_id?: string | null; status: TaskStatus; run_type?: string; started_at?: string; duration_seconds?: number; error?: string | null; summary?: Data; config?: Data; persistence?: Persistence }
export interface RunSummary { task_id?: string; run_id: string; status?: string; run_type?: string; tool?: string; model?: string; started_at?: string; summary?: Data; persistence?: Persistence }
export interface RunResult { status?: string; test_type?: string; tool?: string; model?: string; config?: Data; task_config?: Data; summary?: Data; results?: unknown; error?: string; metadata?: { task_id?: string; run_id?: string; started_at?: string; duration_seconds?: number }; artifacts?: { json_path?: string | null; html_path?: string | null; save_error?: string }; reports?: Record<string, string>; persistence?: Persistence }
export interface RunPage { items: RunSummary[]; total: number; limit: number; offset: number }
