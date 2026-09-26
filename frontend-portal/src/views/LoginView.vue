<script setup lang="ts">
import { reactive, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { NButton, NForm, NFormItem, NInput, type FormInst } from 'naive-ui'
import { useAuthStore } from '../stores/auth'
import { useAction } from '../api/feedback'
import GateHeader from '../components/GateHeader.vue'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const form = ref<FormInst | null>(null)
const values = reactive({ username: '', password: '' })
const { busy, run } = useAction()

async function submit() {
  try {
    await form.value?.validate()
  } catch {
    return
  }
  await run(async () => {
    await auth.login(values.username, values.password)
    values.password = ''
    const target = route.query.redirect
    await router.replace(
      typeof target === 'string' && target.startsWith('/') && !target.startsWith('//')
        ? target
        : '/console/overview'
    )
  })
}
</script>

<template>
  <GateHeader />
  <main class="gate-page">
    <div class="gate-form">
      <h1>登录控制台</h1>
      <p class="muted">管理 API Key、查看调用记录与用量。</p>
      <NForm ref="form" :model="values" @submit.prevent="submit">
        <NFormItem label="用户名" path="username" :rule="{ required: true, message: '请输入用户名', trigger: 'blur' }">
          <NInput
            v-model:value="values.username"
            placeholder="注册时使用的用户名"
            :input-props="{ 'aria-label': '用户名', autocomplete: 'username' }"
          />
        </NFormItem>
        <NFormItem label="密码" path="password" :rule="{ required: true, message: '请输入密码', trigger: 'blur' }">
          <NInput
            v-model:value="values.password"
            type="password"
            show-password-on="click"
            placeholder="请输入密码"
            :input-props="{ 'aria-label': '密码', autocomplete: 'current-password' }"
          />
        </NFormItem>
        <NButton type="primary" attr-type="submit" block size="large" :loading="busy">登录</NButton>
      </NForm>
      <p class="field-hint">
        还没有账号？<RouterLink to="/register">免费注册</RouterLink>。控制台账号与后台管理账号相互独立。
      </p>
    </div>
  </main>
</template>
