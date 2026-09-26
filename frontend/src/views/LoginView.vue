<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NForm, NFormItem, NInput, NIcon, type FormInst } from 'naive-ui'
import { BookOutline } from '@vicons/ionicons5'
import { useAuthStore } from '../stores/auth'
import { useAction } from '../api/feedback'
const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const form = ref<FormInst | null>(null)
const values = reactive({ username: '', password: '' })
const { busy, run } = useAction()
async function submit() {
  try { await form.value?.validate() } catch { return }
  await run(async () => {
    await auth.login(values.username, values.password)
    values.password = ''
    const target = route.query.redirect
    await router.replace(typeof target === 'string' && target.startsWith('/') && !target.startsWith('//') ? target : '/dashboard')
  })
}
</script>

<template>
  <main class="login-page">
    <div class="login-form">
      <div class="brand login-brand"><NIcon :size="30" color="#18a058"><BookOutline /></NIcon><strong>KnowForge</strong></div>
      <h1>登录知识管理后台</h1>
      <p class="muted">管理文档、组织知识，验证每一次检索。</p>
      <NForm ref="form" :model="values" @submit.prevent="submit">
        <NFormItem label="用户名" path="username" :rule="{ required: true, message: '请输入用户名', trigger: 'blur' }">
          <NInput v-model:value="values.username" placeholder="请输入用户名" :input-props="{ 'aria-label': '用户名', autocomplete: 'username' }" />
        </NFormItem>
        <NFormItem label="密码" path="password" :rule="{ required: true, message: '请输入密码', trigger: 'blur' }">
          <NInput v-model:value="values.password" type="password" show-password-on="click" placeholder="请输入密码" :input-props="{ 'aria-label': '密码', autocomplete: 'current-password' }" />
        </NFormItem>
        <NButton type="primary" attr-type="submit" block size="large" :loading="busy">登录</NButton>
      </NForm>
    </div>
  </main>
</template>
