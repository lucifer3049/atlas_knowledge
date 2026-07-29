<script setup lang="ts">
import { computed } from 'vue'

import type { DocumentOut, DocumentStatus } from '@/api/types'

const props = defineProps<{ document: DocumentOut; busy: boolean }>()
defineEmits<{ retry: [id: string]; remove: [id: string] }>()

const STATUS_LABEL: Record<DocumentStatus, string> = {
  pending: '待處理',
  parsing: '解析中',
  chunking: '切塊中',
  embedding: '嵌入中',
  ready: '可檢索',
  failed: '失敗',
}

const STATUS_CLASS: Record<DocumentStatus, string> = {
  pending: 'bg-slate-100 text-slate-600',
  parsing: 'bg-blue-50 text-blue-700',
  chunking: 'bg-blue-50 text-blue-700',
  embedding: 'bg-blue-50 text-blue-700',
  ready: 'bg-emerald-50 text-emerald-700',
  failed: 'bg-red-50 text-red-700',
}

const sizeLabel = computed(() => {
  const kb = props.document.size_bytes / 1024
  return kb < 1024 ? `${kb.toFixed(0)} KB` : `${(kb / 1024).toFixed(1)} MB`
})

function confirmRemove(): boolean {
  return window.confirm(`確定刪除「${props.document.filename}」?此操作無法復原。`)
}
</script>

<template>
  <li class="flex items-center gap-3 border-b border-slate-100 px-4 py-3 last:border-b-0">
    <div class="min-w-0 flex-1">
      <p class="truncate font-medium text-slate-800">{{ document.filename }}</p>
      <p class="text-xs text-slate-400">{{ sizeLabel }}</p>
    </div>

    <!-- error 全文以 title 呈現(hover tooltip),清單不因長訊息破版 -->
    <span
      class="shrink-0 rounded-full px-2 py-0.5 text-xs font-medium"
      :class="STATUS_CLASS[document.status]"
      :title="document.error ?? undefined"
    >
      {{ STATUS_LABEL[document.status] }}
    </span>

    <button
      v-if="document.status === 'failed'"
      type="button"
      class="shrink-0 rounded-lg border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50 disabled:opacity-50"
      :disabled="busy"
      @click="$emit('retry', document.id)"
    >
      重試
    </button>
    <button
      type="button"
      class="shrink-0 rounded-lg px-3 py-1 text-sm text-red-600 hover:bg-red-50 disabled:opacity-50"
      :disabled="busy"
      @click="confirmRemove() && $emit('remove', document.id)"
    >
      刪除
    </button>
  </li>
</template>
