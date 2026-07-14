<script setup lang="ts">
import { renderMarkdown } from './markdown'

defineProps<{ text: string }>()
</script>

<template>
  <div class="mx-auto max-w-3xl px-4 pb-4">
    <div class="flex justify-start">
      <div class="max-w-[80%] rounded-2xl bg-white px-4 py-2 text-slate-800 shadow-sm">
        <!-- 串流中內容已 sanitize 後才 v-html -->
        <div
          v-if="text !== ''"
          class="prose prose-sm max-w-none break-words"
          v-html="renderMarkdown(text)"
        ></div>
        <span v-else class="text-slate-400">思考中…</span>
      </div>
    </div>
  </div>
</template>
