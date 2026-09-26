<script setup lang="ts">
import { RouterLink, useRoute } from 'vue-router'
import { NButton, NIcon } from 'naive-ui'
import { BookOutline, MenuOutline } from '@vicons/ionicons5'
import AccountMenu from './AccountMenu.vue'
import { useNarrow } from '../composables/media'

// 欢迎页与控制台共用这一个顶栏：样式只有一份，不会再出现两边高度、留白不一致。
const props = defineProps<{ inConsole?: boolean }>()
const emit = defineEmits<{ (event: 'open-nav'): void }>()

const route = useRoute()
const narrow = useNarrow()
</script>

<template>
  <header class="gate-header">
    <div class="row gate-leading">
      <NButton
        v-if="props.inConsole && narrow"
        quaternary
        circle
        aria-label="打开导航"
        class="gate-burger"
        @click="emit('open-nav')"
      >
        <template #icon><NIcon><MenuOutline /></NIcon></template>
      </NButton>
      <RouterLink to="/" class="brand">
        <NIcon :size="26" color="#18a058"><BookOutline /></NIcon>
        <strong>KnowForge</strong>
        <span class="brand-tag muted nowrap">技术知识检索开放平台</span>
      </RouterLink>
    </div>
    <nav class="gate-nav" aria-label="主导航">
      <!-- 控制台窄屏下品牌标已经指向 "/"，再排一个"系统介绍"就会把账号区挤到换行。 -->
      <RouterLink v-if="!props.inConsole || !narrow" to="/#intro" :class="{ active: route.path === '/' }">
        系统介绍
      </RouterLink>
      <AccountMenu :hide-console="props.inConsole" />
    </nav>
  </header>
</template>

<style scoped>
.gate-leading {
  min-width: 0;
}
.gate-nav :deep(.account-name) {
  max-width: 120px;
}
@media (max-width: 700px) {
  .brand-tag {
    display: none;
  }
  .gate-nav {
    gap: 14px;
  }
  .gate-nav :deep(.account-name) {
    max-width: 84px;
  }
}
</style>
