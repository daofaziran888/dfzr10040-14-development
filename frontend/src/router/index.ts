import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import ImpactView from '@/views/ImpactView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 固定路由需排在动态模块路由之前，避免 impact_view 被工作台抢先匹配。
    { path: '/impact_view', name: 'impact-view', component: ImpactView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
