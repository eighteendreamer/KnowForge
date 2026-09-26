<script setup lang="ts">
import { computed, h, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { NButton, NIcon, NLayout, NLayoutContent, NLayoutHeader, NLayoutSider, NMenu } from 'naive-ui'
import { BookOutline, DocumentsOutline, GridOutline, PricetagsOutline, FolderOpenOutline, SearchOutline, ListOutline, KeyOutline, PeopleOutline, CashOutline, ShieldCheckmarkOutline, CheckmarkDoneOutline, DocumentTextOutline, SettingsOutline, MenuOutline, LogOutOutline } from '@vicons/ionicons5'
import type { Component } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useTasksStore } from '../stores/tasks'
const auth = useAuthStore()
const tasks = useTasksStore()
onMounted(tasks.start)
onUnmounted(tasks.stop)
const route = useRoute()
const router = useRouter()
const collapsed = ref(false)
const entries: [string, string, Component, boolean?][] = [
  ['/dashboard', '仪表盘', GridOutline], ['/documents', '文档管理', DocumentsOutline],
  ['/tasks', '处理任务', ListOutline], ['/tags', '标签管理', PricetagsOutline],
  ['/categories', '分类管理', FolderOpenOutline], ['/search', '检索测试', SearchOutline],
  ['/evaluations', '相关性标注', CheckmarkDoneOutline], ['/api-docs', '接口文档', DocumentTextOutline],
  ['/api-keys', 'API 密钥', KeyOutline, true], ['/users', '用户管理', PeopleOutline, true], ['/recharge', '充值系统', CashOutline, true], ['/audit-logs', '审计日志', ShieldCheckmarkOutline, true],
  ['/settings', '系统设置', SettingsOutline, true],
]
const options = computed(() => entries.filter((entry) => !entry[3] || auth.isSuperAdmin).map(([key, label, icon]) => ({
  key, label: () => h(RouterLink, { to: key }, () => label), icon: () => h(NIcon, null, () => h(icon)),
})))
const active = computed(() => '/' + route.path.split('/')[1])
function logout() { auth.logout(); void router.replace('/login') }
</script>

<template>
  <NLayout has-sider class="admin-shell">
    <NLayoutSider bordered :width="216" :collapsed-width="64" :collapsed="collapsed" collapse-mode="width">
      <RouterLink to="/dashboard" class="brand">
        <NIcon :size="28" color="#18a058"><BookOutline /></NIcon>
        <strong v-if="!collapsed">KnowForge</strong>
      </RouterLink>
      <NMenu :value="active" :options="options" :collapsed="collapsed" :collapsed-width="64" />
    </NLayoutSider>
    <NLayout>
      <NLayoutHeader bordered class="shell-header">
        <div class="row">
          <NButton quaternary circle aria-label="折叠导航" @click="collapsed = !collapsed">
            <template #icon><NIcon><MenuOutline /></NIcon></template>
          </NButton>
          <span>知识管理 / {{ route.meta.title }}</span>
        </div>
        <div class="row">
          <span>{{ auth.user?.username }} <span class="muted">· {{ auth.isSuperAdmin ? '超级管理员' : '内容管理员' }}</span></span>
          <NButton quaternary aria-label="退出登录" @click="logout">
            <template #icon><NIcon><LogOutOutline /></NIcon></template>
            退出
          </NButton>
        </div>
      </NLayoutHeader>
      <NLayoutContent class="page-content" content-style="min-height: 100%">
        <RouterView />
      </NLayoutContent>
    </NLayout>
  </NLayout>
</template>
