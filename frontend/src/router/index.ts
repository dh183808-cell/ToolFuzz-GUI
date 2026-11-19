import { createRouter, createWebHashHistory } from 'vue-router'
export default createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: () => import('@/views/WorkspaceView.vue') },
    { path: '/history', component: () => import('@/views/HistoryView.vue') },
    { path: '/runs/:runId', component: () => import('@/views/RunView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
