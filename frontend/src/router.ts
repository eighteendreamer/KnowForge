import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from './stores/auth'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: () => import('./views/LoginView.vue'), meta: { title: '登录', public: true } },
    {
      path: '/', component: () => import('./components/AdminLayout.vue'),
      children: [
        { path: '', redirect: '/dashboard' },
        { path: 'dashboard', component: () => import('./views/DashboardView.vue'), meta: { title: '仪表盘' } },
        { path: 'documents', component: () => import('./views/DocumentsView.vue'), meta: { title: '文档管理' } },
        { path: 'documents/:id', component: () => import('./views/DocumentDetailView.vue'), meta: { title: '文档详情' } },
        { path: 'tasks', component: () => import('./views/TasksView.vue'), meta: { title: '处理任务' } },
        { path: 'tags', component: () => import('./views/TagsView.vue'), meta: { title: '标签管理' } },
        { path: 'categories', component: () => import('./views/CategoriesView.vue'), meta: { title: '分类管理' } },
        { path: 'search', component: () => import('./views/SearchView.vue'), meta: { title: '检索测试' } },
        { path: 'evaluations', component: () => import('./views/EvaluationsView.vue'), meta: { title: '相关性标注' } },
        { path: 'api-docs', component: () => import('./views/ApiDocsView.vue'), meta: { title: '接口文档' } },
        { path: 'api-keys', component: () => import('./views/ApiKeysView.vue'), meta: { title: 'API 密钥', superAdmin: true } },
        { path: 'users', component: () => import('./views/UsersView.vue'), meta: { title: '用户管理', superAdmin: true } },
        { path: 'recharge', component: () => import('./views/RechargeView.vue'), meta: { title: '充值系统', superAdmin: true } },
        { path: 'audit-logs', component: () => import('./views/AuditView.vue'), meta: { title: '审计日志', superAdmin: true } },
        { path: 'settings', component: () => import('./views/SettingsView.vue'), meta: { title: '系统设置', superAdmin: true } },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (to.meta.public) return true
  if (!auth.token) return { path: '/login', query: { redirect: to.fullPath } }
  if (!auth.user) {
    try { await auth.restore() } catch { return { path: '/login', query: { redirect: to.fullPath } } }
  }
  if (to.meta.superAdmin && !auth.isSuperAdmin) return '/dashboard'
})

router.afterEach((to) => { document.title = `${to.meta.title ?? '知识管理'} · KnowForge` })
