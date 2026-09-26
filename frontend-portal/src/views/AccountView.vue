<script setup lang="ts">
import { reactive, ref } from 'vue'
import { NAlert, NButton, NForm, NFormItem, NInput, type FormInst } from 'naive-ui'
import { api } from '../api/client'
import { useAction } from '../api/feedback'
import { useAuthStore } from '../stores/auth'
import PageHeader from '../components/PageHeader.vue'

const auth = useAuthStore()
const form = ref<FormInst | null>(null)
const values = reactive({ current_password: '', new_password: '', confirm: '' })
const { busy, run } = useAction()

async function submit() {
  try {
    await form.value?.validate()
  } catch {
    return
  }
  await run(async () => {
    await api('/auth/password', {
      method: 'PUT',
      data: { current_password: values.current_password, new_password: values.new_password },
    })
    values.current_password = ''
    values.new_password = ''
    values.confirm = ''
  }, '密码已更新，下次登录生效')
}
</script>

<template>
  <PageHeader title="账号设置" description="门户账号只用于建密钥和看用量，不涉及知识库内容管理。" />
  <div class="split-view">
    <div>
      <NForm ref="form" :model="values" @submit.prevent="submit">
        <NFormItem label="当前密码" path="current_password" :rule="{ required: true, message: '请输入当前密码', trigger: 'blur' }">
          <NInput
            v-model:value="values.current_password"
            type="password"
            show-password-on="click"
            :input-props="{ 'aria-label': '当前密码', autocomplete: 'current-password' }"
          />
        </NFormItem>
        <NFormItem
          label="新密码"
          path="new_password"
          :rule="[
            { required: true, message: '请输入新密码', trigger: 'blur' },
            { min: 12, message: '新密码至少 12 位', trigger: 'blur' },
          ]"
        >
          <NInput
            v-model:value="values.new_password"
            type="password"
            show-password-on="click"
            placeholder="至少 12 位"
            :input-props="{ 'aria-label': '新密码', autocomplete: 'new-password' }"
          />
        </NFormItem>
        <NFormItem
          label="确认新密码"
          path="confirm"
          :rule="{
            required: true,
            validator: () => (values.confirm === values.new_password ? true : new Error('两次输入的密码不一致')),
            trigger: ['blur', 'input'],
          }"
        >
          <NInput
            v-model:value="values.confirm"
            type="password"
            show-password-on="click"
            :input-props="{ 'aria-label': '确认新密码', autocomplete: 'new-password' }"
          />
        </NFormItem>
        <div class="form-actions">
          <NButton type="primary" attr-type="submit" :loading="busy">更新密码</NButton>
        </div>
      </NForm>
    </div>
    <div>
      <table class="table-plain">
        <tbody>
          <tr><th>用户名</th><td>{{ auth.user?.username }}</td></tr>
          <tr><th>账号类型</th><td>门户账号</td></tr>
          <tr>
            <th>状态</th>
            <td>{{ auth.user?.status === 'active' ? '正常' : auth.user?.status }}</td>
          </tr>
        </tbody>
      </table>
      <NAlert type="info" style="margin-top: 18px">
        调用配额与账户余额都由平台在后台维护；改密码不会影响已签发的 API Key。
      </NAlert>
    </div>
  </div>
</template>
