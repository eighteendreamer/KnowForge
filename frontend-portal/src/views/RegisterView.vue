<script setup lang="ts">
import { reactive, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { NButton, NForm, NFormItem, NInput, type FormInst } from 'naive-ui'
import { useAuthStore } from '../stores/auth'
import { useAction } from '../api/feedback'
import GateHeader from '../components/GateHeader.vue'

const auth = useAuthStore()
const router = useRouter()
const form = ref<FormInst | null>(null)
const values = reactive({ username: '', password: '', confirm: '' })
const { busy, run } = useAction()

async function submit() {
  try {
    await form.value?.validate()
  } catch {
    return
  }
  await run(async () => {
    await auth.register(values.username, values.password)
    await router.replace('/console/overview')
  })
}
</script>

<template>
  <GateHeader />
  <main class="gate-page">
    <div class="gate-form">
      <h1>注册 KnowForge 账号</h1>
      <p class="muted">注册即可创建 API Key 并调用检索接口。</p>
      <NForm ref="form" :model="values" @submit.prevent="submit">
        <NFormItem
          label="用户名"
          path="username"
          :rule="[
            { required: true, message: '请输入用户名', trigger: 'blur' },
            { pattern: /^[\w.@-]{2,100}$/, message: '2-100 位，仅字母、数字、. @ - _', trigger: 'blur' },
          ]"
        >
          <NInput
            v-model:value="values.username"
            placeholder="例如 shop-assistant"
            :input-props="{ 'aria-label': '用户名', autocomplete: 'username' }"
          />
        </NFormItem>
        <NFormItem
          label="密码"
          path="password"
          :rule="[
            { required: true, message: '请输入密码', trigger: 'blur' },
            { min: 12, message: '密码至少 12 位', trigger: 'blur' },
          ]"
        >
          <NInput
            v-model:value="values.password"
            type="password"
            show-password-on="click"
            placeholder="至少 12 位"
            :input-props="{ 'aria-label': '密码', autocomplete: 'new-password' }"
          />
        </NFormItem>
        <NFormItem
          label="确认密码"
          path="confirm"
          :rule="{
            required: true,
            validator: () => (values.confirm === values.password ? true : new Error('两次输入的密码不一致')),
            trigger: ['blur', 'input'],
          }"
        >
          <NInput
            v-model:value="values.confirm"
            type="password"
            show-password-on="click"
            placeholder="再输入一次"
            :input-props="{ 'aria-label': '确认密码', autocomplete: 'new-password' }"
          />
        </NFormItem>
        <NButton type="primary" attr-type="submit" block size="large" :loading="busy">注册并进入控制台</NButton>
      </NForm>
      <p class="field-hint">
        注册不赠送调用额度；余额与配额由平台在后台开通。已有账号请<RouterLink to="/login">登录</RouterLink>。
      </p>
    </div>
  </main>
</template>
