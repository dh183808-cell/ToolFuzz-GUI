<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import { api } from '@/api/client'
const healthy = ref<boolean | null>(null)
const controller = new AbortController()
onMounted(async () => { try { healthy.value = (await api.health(controller.signal)).status === 'ok' } catch { healthy.value = false } })
onBeforeUnmount(() => controller.abort())
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <a class="brand" href="#/">TF<span>ToolFuzz<small>EXPERIMENT CONSOLE</small></span></a>
      <p class="nav-label">工作空间 / WORKSPACE</p>
      <nav aria-label="主导航">
        <RouterLink to="/"><span>01</span>测试工作台<small>Run experiment</small></RouterLink>
        <RouterLink to="/history"><span>02</span>历史记录<small>History</small></RouterLink>
      </nav>
      <div class="sidebar-note"><span class="dot" :class="{ online: healthy }"></span>{{ healthy === null ? '正在连接 API' : healthy ? 'API 已连接' : 'API 未连接' }}<p>本地实验环境 · 单任务执行</p></div>
    </aside>
    <main><header class="topbar"><span>TOOL RELIABILITY LAB</span><span>Vue 3 / FastAPI</span></header><RouterView /></main>
  </div>
</template>
