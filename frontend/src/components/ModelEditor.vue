<script setup lang="ts">
import { computed } from 'vue'
import type { ModelDraft, Provider } from '@/types/api'
const props = defineProps<{ modelValue: ModelDraft; providers: Provider[]; title: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: ModelDraft] }>()
const provider = computed(() => props.providers.find(item => item.id === props.modelValue.provider))
function update(key: keyof ModelDraft, value: string | number) { emit('update:modelValue', { ...props.modelValue, [key]: value } as ModelDraft) }
function changeProvider(value: string) {
  emit('update:modelValue', { ...props.modelValue, provider: value, model_id: '', base_url: '', api_key_env: '', temporary_key: '' })
}
</script>
<template>
  <fieldset class="model-card">
    <legend>{{ title }}</legend>
    <div class="grid two">
      <label>Provider<select :value="modelValue.provider" @change="changeProvider(($event.target as HTMLSelectElement).value)"><option v-for="item in providers" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
      <label>Model ID<input :value="modelValue.model_id" autocomplete="off" placeholder="填写账户可用的模型 ID" @input="update('model_id', ($event.target as HTMLInputElement).value)" /></label>
    </div>
    <label class="inset">凭证来源<select :value="modelValue.credential_source" @change="emit('update:modelValue', { ...modelValue, credential_source: ($event.target as HTMLSelectElement).value as 'direct' | 'environment', temporary_key: '' })"><option value="direct">直接输入 API Key</option><option value="environment">使用服务器环境变量（高级）</option></select></label>
    <div v-if="modelValue.credential_source === 'direct'" class="inset">
      <label>API Key<input type="password" autocomplete="new-password" spellcheck="false" :value="modelValue.temporary_key" placeholder="仅用于本次任务，刷新后需重新输入" @input="update('temporary_key', ($event.target as HTMLInputElement).value)" /></label>
      <button type="button" class="inset" :disabled="!modelValue.temporary_key" @click="update('temporary_key', '')">清空密钥</button>
      <p class="hint">密钥仅传给本机后端，不写入历史记录。任务提交后清空输入。</p>
    </div>
    <p v-else class="hint">{{ provider?.credentials_configured ? '后端默认环境变量已配置；尚未检查模型连通性。' : '默认凭证未确认；请在后端配置环境变量。' }}</p>
    <details :open="provider?.requires_base_url"><summary>地址与凭证变量 / Endpoint</summary>
      <div class="grid two inset">
        <label>Base URL<input :value="modelValue.base_url ?? ''" :placeholder="provider?.default_base_url ?? 'https://your-service.example/v1'" @input="update('base_url', ($event.target as HTMLInputElement).value)" /></label>
        <label v-if="modelValue.credential_source === 'environment'">API Key 环境变量名<input :value="modelValue.api_key_env ?? ''" autocomplete="off" placeholder="例如 CUSTOM_API_KEY，非密钥值" @input="update('api_key_env', ($event.target as HTMLInputElement).value)" /></label>
      </div>
    </details>
    <div class="grid three inset">
      <label>Temperature<input type="number" min="0" max="2" step="0.1" :value="modelValue.temperature" @input="update('temperature', Number(($event.target as HTMLInputElement).value))" /></label>
      <label>Timeout · 秒<input type="number" min="0.1" step="1" :value="modelValue.timeout" @input="update('timeout', Number(($event.target as HTMLInputElement).value))" /></label>
      <label>Max Retries<input type="number" min="0" step="1" :value="modelValue.max_retries" @input="update('max_retries', Number(($event.target as HTMLInputElement).value))" /></label>
    </div>
  </fieldset>
</template>
