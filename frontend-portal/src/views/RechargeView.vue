<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NAlert, NButton, NEmpty, NDataTable, type DataTableColumns } from 'naive-ui'
import { api, formatCent } from '../api/client'
import { formatDate, useAction } from '../api/feedback'
import type { TransactionRow, WalletData } from '../api/types'
import PageHeader from '../components/PageHeader.vue'

const { busy, run } = useAction()
const wallet = ref<WalletData | null>(null)

async function load() {
  wallet.value = await api<WalletData>('/wallet')
}

const columns: DataTableColumns<TransactionRow> = [
  { title: '时间', key: 'created_at', width: 190, render: (row) => formatDate(row.created_at) },
  { title: '入账金额', key: 'amount_cent', width: 140, render: (row) => formatCent(row.amount_cent) },
  { title: '渠道', key: 'channel', width: 120 },
  { title: '备注', key: 'note', minWidth: 200, render: (row) => row.note ?? '—' },
]

onMounted(() => void run(load))
</script>

<template>
  <PageHeader title="充值" description="余额由平台按账本入账，这里展示的是真实流水。">
    <NButton size="small" :loading="busy" @click="run(load)">刷新</NButton>
  </PageHeader>
  <template v-if="wallet">
    <div class="metric-strip" style="grid-template-columns: repeat(3, 1fr)">
      <div class="metric">
        <strong>{{ formatCent(wallet.balance_cent) }}</strong>
        <span>当前余额</span>
      </div>
      <div class="metric">
        <strong>{{ wallet.transactions.length }}</strong>
        <span>入账笔数（最近 100 笔）</span>
      </div>
      <div class="metric">
        <strong>{{ wallet.channels.length }}</strong>
        <span>已开通支付渠道</span>
      </div>
    </div>

    <section class="section">
      <h2>在线充值</h2>
      <NAlert v-if="!wallet.packages.length" type="info" style="margin-bottom: 6px">
        支付通道尚未开通，暂时无法在线下单。需要额度请联系平台在后台为你的账号入账。
      </NAlert>
      <template v-else>
        <p class="muted">以下档位已配置，在线支付接入后可直接下单：</p>
        <table class="table-plain" style="margin-top: 14px">
          <thead>
            <tr><th>档位</th><th>金额</th><th>赠送</th><th>到账</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in wallet.packages" :key="row.label">
              <td>{{ row.label }}</td>
              <td>{{ formatCent(row.amount_cent) }}</td>
              <td>{{ formatCent(row.bonus_cent) }}</td>
              <td>{{ formatCent(row.amount_cent + row.bonus_cent) }}</td>
            </tr>
          </tbody>
        </table>
      </template>
      <div v-if="wallet.channels.length" class="row" style="margin-top: 16px">
        <span class="muted">已开通渠道：</span>
        <span v-for="row in wallet.channels" :key="row.code">{{ row.display_name }}</span>
      </div>
    </section>

    <section class="section">
      <h2>入账流水</h2>
      <NEmpty v-if="!wallet.transactions.length" description="尚无入账记录" />
      <NDataTable
        v-else
        :columns="columns"
        :data="wallet.transactions"
        :row-key="(row) => row.id"
        :bordered="false"
        :pagination="{ pageSize: 20 }"
      />
    </section>
  </template>
</template>
