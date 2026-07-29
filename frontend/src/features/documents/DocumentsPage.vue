<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { ApiError } from '@/api/client'

import DocumentRow from './DocumentRow.vue'
import UploadZone from './UploadZone.vue'
import {
  useDeleteDocument,
  useDocuments,
  useRetryDocument,
  useUploadDocument,
} from './useDocuments'

const { data, isLoading } = useDocuments()
const upload = useUploadDocument()
const retry = useRetryDocument()
const remove = useDeleteDocument()

const notice = ref<string | null>(null)
const errorMessage = ref<string | null>(null)

const documents = computed(() => data.value ?? [])
const busy = computed(() => retry.isPending.value || remove.isPending.value)

function report(err: unknown): void {
  errorMessage.value = err instanceof ApiError ? err.message : '操作失敗,請稍後再試'
}

async function onSelect(files: File[]): Promise<void> {
  notice.value = null
  errorMessage.value = null
  const deduplicated: string[] = []
  for (const file of files) {
    try {
      const doc = await upload.mutateAsync(file)
      if (doc.deduplicated) deduplicated.push(doc.filename) // D8:重複上傳不是錯誤
    } catch (err) {
      report(err)
    }
  }
  if (deduplicated.length > 0) {
    notice.value = `${deduplicated.join('、')} 已存在,沿用既有文件`
  }
}

async function onRetry(id: string): Promise<void> {
  errorMessage.value = null
  try {
    await retry.mutateAsync(id)
  } catch (err) {
    report(err)
  }
}

async function onRemove(id: string): Promise<void> {
  errorMessage.value = null
  try {
    await remove.mutateAsync(id)
  } catch (err) {
    report(err)
  }
}
</script>

<template>
  <div class="min-h-screen bg-slate-50 text-slate-800">
    <div class="mx-auto flex max-w-3xl flex-col gap-4 p-6">
      <header class="flex items-center justify-between">
        <h1 class="text-lg font-semibold">我的文件</h1>
        <RouterLink to="/chat" class="text-sm text-slate-500 hover:text-slate-800">
          ← 回到聊天
        </RouterLink>
      </header>

      <UploadZone :uploading="upload.isPending.value" @select="onSelect" />

      <p v-if="notice !== null" class="text-sm text-slate-500">{{ notice }}</p>
      <p v-if="errorMessage !== null" class="text-sm text-red-600" role="alert">
        {{ errorMessage }}
      </p>

      <p v-if="isLoading" class="text-sm text-slate-400">載入中…</p>
      <p v-else-if="documents.length === 0" class="text-sm text-slate-400">
        尚未上傳任何文件。上傳後狀態變為「可檢索」即可在聊天中使用知識庫提問。
      </p>
      <ul v-else class="rounded-xl border border-slate-200 bg-white">
        <DocumentRow
          v-for="doc in documents"
          :key="doc.id"
          :document="doc"
          :busy="busy"
          @retry="onRetry"
          @remove="onRemove"
        />
      </ul>
    </div>
  </div>
</template>
