import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { router } from './router'
import { useAuthStore } from './stores/auth'
import './style.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)
window.addEventListener('knowforge:unauthorized', () => {
  useAuthStore().logout()
  if (router.currentRoute.value.path !== '/login') {
    void router.replace({ path: '/login', query: { redirect: router.currentRoute.value.fullPath } })
  }
})
app.mount('#app')
