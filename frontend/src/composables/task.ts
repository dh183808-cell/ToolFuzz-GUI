import { computed, ref, shallowRef } from 'vue'
import { api, ApiError, errorMessage, type Api } from '@/api/client'
import type { RunResult, Task, TaskInput } from '@/types/api'

export const activeStatus = (value?: string) => value === 'running' || value === 'cancelling'
const marker = 'toolfuzz.currentTaskId'
export function createTaskController(client: Api = api, storage?: Pick<Storage, 'setItem' | 'getItem'>, interval = 1200) {
  const task = shallowRef<Task | null>(null), result = shallowRef<RunResult | null>(null)
  const input = shallowRef<TaskInput | null>(null), error = ref(''), busy = ref(false), cancelling = ref(false), recovering = ref(false)
  const blocked = computed(() => busy.value || recovering.value || cancelling.value || activeStatus(task.value?.status))
  let timer: ReturnType<typeof setTimeout> | undefined, generation = 0, request: AbortController | undefined
  function stop() { generation++; clearTimeout(timer); request?.abort(); request = undefined; cancelling.value = false }
  function remember(id: string) { try { storage?.setItem(marker, id) } catch { /* ID recovery via URL remains available. */ } }
  function savedId() { try { return storage?.getItem(marker) ?? '' } catch { return '' } }
  async function poll(id: string, ticket: number) {
    const controller = new AbortController(); request = controller
    let missing = false
    try {
      const latest = await client.task(id, controller.signal)
      if (ticket !== generation || latest.task_id !== id) return
      task.value = latest; error.value = ''
      if (!activeStatus(latest.status) && latest.run_id) {
        try {
          const record = await client.run(latest.run_id, controller.signal)
          if (ticket === generation) result.value = record
        } catch (err) { if (ticket === generation) error.value = errorMessage(err) }
      }
    } catch (err) {
      if (ticket !== generation) return
      error.value = errorMessage(err)
      if (err instanceof ApiError && err.status === 404) missing = true
    } finally {
      if (ticket === generation && !missing && activeStatus(task.value?.status)) timer = setTimeout(() => void poll(id, ticket), interval)
    }
  }
  async function resume(id: string) {
    if (!id || busy.value) return
    stop(); result.value = null; error.value = ''
    if (task.value?.task_id !== id) { input.value = null; task.value = null }
    remember(id)
    recovering.value = true
    try { await poll(id, generation) } finally { recovering.value = false }
  }
  async function start(value: TaskInput) {
    if (blocked.value) { delete value.temporary_credentials; return null }
    stop(); busy.value = true; error.value = ''; result.value = null
    const ticket = generation
    request = new AbortController()
    try {
      const created = await client.start(value, request.signal)
      if (ticket !== generation) return null
      task.value = { task_id: created.task_id, status: created.status, run_type: value.test_type.toLowerCase() }
      // Keep only non-sensitive configuration for the task summary.
      const { temporary_credentials: _credentials, ...safeInput } = value
      input.value = safeInput; remember(created.task_id)
      void poll(created.task_id, ticket)
      return created.task_id
    } catch (err) {
      if (ticket === generation) error.value = errorMessage(err) + ' 创建请求不会自动重发。'
      return null
    } finally { delete value.temporary_credentials; busy.value = false }
  }
  async function cancel() {
    if (task.value?.status !== 'running' || cancelling.value) return
    const id = task.value.task_id
    stop(); const ticket = generation; cancelling.value = true
    request = new AbortController()
    try {
      const accepted = await client.cancel(id, request.signal)
      if (ticket !== generation || accepted.task_id !== id) return
      // Only this backend response confirms cancelled, never the button click.
      task.value = { ...task.value!, status: accepted.status }; result.value = null
    } catch (err) { if (ticket === generation) error.value = errorMessage(err) }
    finally {
      if (ticket === generation) { cancelling.value = false; await poll(id, ticket) }
    }
  }
  return { task, result, input, error, busy, cancelling, recovering, blocked, start, cancel, resume, stop, savedId }
}
let instance: ReturnType<typeof createTaskController> | undefined
export function useTask() {
  return instance ??= createTaskController(api, typeof sessionStorage === 'undefined' ? undefined : sessionStorage)
}
