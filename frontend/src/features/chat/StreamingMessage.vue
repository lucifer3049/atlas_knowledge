<script setup lang="ts">
import type { CitationOut } from '@/api/types'

import CitationsPanel from './CitationsPanel.vue'
import { renderMarkdown } from './markdown'

// citations 於首個 delta 之前抵達(D6):來源先渲染,不必等回答生成完(§10.3)
withDefaults(defineProps<{ text: string; citations?: CitationOut[] }>(), { citations: () => [] })
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
        <CitationsPanel :citations="citations" />
      </div>
    </div>
  </div>
</template>
