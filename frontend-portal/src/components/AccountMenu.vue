<script setup lang="ts">
import { computed, h, onMounted } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { NAvatar, NDropdown, NIcon } from 'naive-ui'
import { CaretDownOutline, GridOutline, LogOutOutline } from '@vicons/ionicons5'
import { useAuthStore } from '../stores/auth'

const props = defineProps<{ hideConsole?: boolean }>()

const auth = useAuthStore()
const router = useRouter()

const options = computed(() => {
  const items: { key: string; label: string; icon: () => ReturnType<typeof h> }[] = []
  if (!props.hideConsole) {
    items.push({ key: 'console', label: '控制台', icon: () => h(NIcon, null, () => h(GridOutline)) })
  }
  if (auth.user) {
    items.push({ key: 'logout', label: '退出登录', icon: () => h(NIcon, null, () => h(LogOutOutline)) })
  }
  return items
})

const initial = computed(() => (auth.user?.username ?? '').slice(0, 1).toUpperCase())

// 公开路由上守卫不会替我们取账号信息：有令牌就自己补一次，补不上就当没登录。
onMounted(() => {
  if (auth.token && !auth.user) auth.restore().catch(() => auth.logout())
})

async function onSelect(key: string) {
  if (key === 'console') {
    await router.push('/console/overview')
    return
  }
  auth.logout()
  await router.replace('/login')
}
</script>

<template>
  <NDropdown v-if="auth.token" trigger="click" placement="bottom-end" :options="options" @select="onSelect">
    <button type="button" class="account" aria-label="账号菜单">
      <NAvatar round :size="28" color="#18a058">{{ initial || '·' }}</NAvatar>
      <span class="account-name">{{ auth.user?.username ?? '账号' }}</span>
      <NIcon :size="12" class="account-caret"><CaretDownOutline /></NIcon>
    </button>
  </NDropdown>
  <div v-else class="account-guest">
    <RouterLink to="/login">登录</RouterLink>
    <RouterLink to="/register" class="button-register">免费注册</RouterLink>
  </div>
</template>

<style scoped>
.account {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px 4px 4px;
  border: 1px solid #efeff5;
  border-radius: 999px;
  background: #fff;
  cursor: pointer;
  font: inherit;
  color: #333639;
}
.account:hover {
  border-color: #cfe6d8;
  background: #f7faf8;
}
.account-name {
  font-size: 14px;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.account-caret {
  color: #767c82;
}
.account-guest {
  display: flex;
  align-items: center;
  gap: 22px;
}
.button-register {
  display: inline-flex;
  align-items: center;
  height: 30px;
  padding: 0 14px;
  border-radius: 3px;
  background: #18a058;
  color: #fff;
  font-size: 14px;
}
.button-register:hover {
  background: #36ad6a;
  color: #fff;
}
</style>
