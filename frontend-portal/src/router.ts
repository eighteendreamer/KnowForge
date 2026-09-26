import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from './stores/auth'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: () => import('./views/WelcomeView.vue'), meta: { title: '产品与能力', public: true } },
    { path: '/login', component: () => import('./views/LoginView.vue'), meta: { title: '登录', public: true } },
    { path: '/register', component: () => import('./views/RegisterView.vue'), meta: { title: '注册', public: true } },
    {
      path: '/console',
      component: () => import('./components/ConsoleLayout.vue'),
      children: [
        { path: '', redirect: '/console/overview' },
        { path: 'overview', component: () => import('./views/OverviewView.vue'), meta: { title: '数据概览' } },
        { path: 'keys', component: () => import('./views/KeysView.vue'), meta: { title: 'API Key' } },
        { path: 'usage', component: () => import('./views/UsageView.vue'), meta: { title: '调用记录' } },
        { path: 'playground', component: () => import('./views/PlaygroundView.vue'), meta: { title: '检索试用' } },
        { path: 'docs', component: () => import('./views/DocsView.vue'), meta: { title: '接口文档' } },
        { path: 'recharge', component: () => import('./views/RechargeView.vue'), meta: { title: '充值' } },
        { path: 'account', component: () => import('./views/AccountView.vue'), meta: { title: '账号设置' } },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach(async (to) => {
  if (to.meta.public) return true
  const auth = useAuthStore()
  if (!auth.token) return { path: '/login', query: { redirect: to.fullPath } }
  if (!auth.user) {
    try {
      await auth.restore()
    } catch {
      return { path: '/login', query: { redirect: to.fullPath } }
    }
  }
  return true
})

router.afterEach((to) => {
  document.title = `${to.meta.title ?? '开放平台'} · KnowForge`
})
