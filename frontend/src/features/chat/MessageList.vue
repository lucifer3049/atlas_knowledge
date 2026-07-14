<script setup lang="ts">
import type { MessageOut } from '@/api/types'

import { renderMarkdown } from './markdown'

defineProps<{
  messages: MessageOut[]
  canLoadEarlier: boolean
  loadingEarlier: boolean
}>()

defineEmits<{ loadEarlier: [] }>()
</script>

<template>
  <div class="mx-auto flex max-w-3xl flex-col gap-4 p-4">
    <div v-if="canLoadEarlier" class="text-center">
      <button
        class="text-sm text-slate-500 hover:text-slate-800 disabled:opacity-50"
        :disabled="loadingEarlier"
        @click="$emit('loadEarlier')"
      >
        {{ loadingEarlier ? '載入中…' : '載入更早訊息' }}
      </button>
    </div>

    <div
      v-for="m in messages"
      :key="m.id"
      class="flex"
      :class="m.role === 'user' ? 'justify-end' : 'justify-start'"
    >
      <div
        class="max-w-[80%] rounded-2xl px-4 py-2"
        :class="m.role === 'user' ? 'bg-slate-800 text-white' : 'bg-white text-slate-800 shadow-sm'"
      >
        <p v-if="m.role === 'user'" class="whitespace-pre-wrap break-words">{{ m.content }}</p>
        <!-- assistant 內容已 sanitize(renderMarkdown)後才 v-html(§C.6.1) -->
        <div v-else class="prose prose-sm max-w-none break-words" v-html="renderMarkdown(m.content)"></div>
      </div>
    </div>
  </div>
</template>
