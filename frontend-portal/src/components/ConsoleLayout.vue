<script setup lang="ts">
import { computed, h, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import {
  NButton,
  NDrawer,
  NDrawerContent,
  NIcon,
  NLayout,
  NLayoutContent,
  NLayoutSider,
  NMenu,
} from 'naive-ui'
import {
  CashOutline,
  CloseOutline,
  DocumentTextOutline,
  GridOutline,
  KeyOutline,
  ListOutline,
  MenuOutline,
  SearchOutline,
  SettingsOutline,
} from '@vicons/ionicons5'
import type { Component } from 'vue'
import { useNarrow } from '../composables/media'
import GateHeader from './GateHeader.vue'

const route = useRoute()
const narrow = useNarrow()
const collapsed = ref(false)
const drawerOpen = ref(false)

const entries: { key: string; label: string; icon: Component }[] = [
  { key: '/console/overview', label: '数据概览', icon: GridOutline },
  { key: '/console/keys', label: 'API Key', icon: KeyOutline },
  { key: '/console/usage', label: '调用记录', icon: ListOutline },
  { key: '/console/playground', label: '检索试用', icon: SearchOutline },
  { key: '/console/docs', label: '接口文档', icon: DocumentTextOutline },
  { key: '/console/recharge', label: '充值', icon: CashOutline },
  { key: '/console/account', label: '账号设置', icon: SettingsOutline },
]

const options = computed(() =>
  entries.map((entry) => ({
    key: entry.key,
    label: () => h(RouterLink, { to: entry.key }, () => entry.label),
    icon: () => h(NIcon, null, () => h(entry.icon)),
  }))
)
const active = computed(() => route.path)

// 窄屏点完菜单项要自动收起抽屉，否则遮罩挡住刚打开的页面。
watch(
  () => route.fullPath,
  () => {
    drawerOpen.value = false
  }
)
</script>

<template>
  <div class="console-frame">
    <GateHeader in-console @open-nav="drawerOpen = true" />
    <NLayout has-sider class="console-shell">
      <NLayoutSider
        v-if="!narrow"
        bordered
        :width="216"
        :collapsed-width="64"
        :collapsed="collapsed"
        collapse-mode="width"
        class="console-sider"
      >
        <NMenu
          :value="active"
          :options="options"
          :collapsed="collapsed"
          :collapsed-width="64"
          :collapsed-icon-size="18"
          class="sider-menu"
        />
        <button
          type="button"
          class="sider-toggle"
          :aria-label="collapsed ? '展开导航' : '折叠导航'"
          @click="collapsed = !collapsed"
        >
          <NIcon :size="16"><MenuOutline /></NIcon>
          <span v-if="!collapsed">收起侧栏</span>
        </button>
      </NLayoutSider>

      <NLayout>
        <NLayoutContent class="console-content" content-style="min-height: 100%">
          <p class="console-crumb">开放平台 / {{ route.meta.title }}</p>
          <RouterView />
        </NLayoutContent>
      </NLayout>
    </NLayout>

    <NDrawer v-model:show="drawerOpen" :width="252" placement="left">
      <NDrawerContent :closable="false" body-content-style="padding: 0">
        <template #header>
          <div class="drawer-head">
            <span class="drawer-title">控制台导航</span>
            <NButton quaternary circle aria-label="关闭导航" @click="drawerOpen = false">
              <template #icon><NIcon><CloseOutline /></NIcon></template>
            </NButton>
          </div>
        </template>
        <NMenu :value="active" :options="options" class="drawer-menu" />
      </NDrawerContent>
    </NDrawer>
  </div>
</template>

<style scoped>
.console-sider {
  display: flex;
  flex-direction: column;
}
.sider-menu {
  flex: 1;
  padding-top: 10px;
}
.sider-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 12px 18px;
  border: 0;
  border-top: 1px solid #efeff5;
  background: transparent;
  color: #767c82;
  font: inherit;
  font-size: 12.5px;
  cursor: pointer;
}
.sider-toggle:hover {
  color: #18a058;
}
.console-crumb {
  font-size: 12.5px;
  color: #9aa0a6;
  margin-bottom: 14px;
}
.drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
}
.drawer-title {
  font-size: 14px;
  color: #5c6662;
}
.drawer-menu {
  padding: 8px 10px;
}
</style>
