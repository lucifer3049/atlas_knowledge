<script setup lang="ts">
import { ref } from 'vue'

import type { CitationOut } from '@/api/types'

// 引用快照(D7):snippet 為後端存的前 200 字,展開只是顯示更多,NEVER 另打 API 取全文。
defineProps<{ citations: CitationOut[] }>()

const expanded = ref<number | null>(null)

function toggle(rank: number): void {
  expanded.value = expanded.value === rank ? null : rank
}
</script>

<template>
  <div v-if="citations.length > 0" class="mt-2 border-t border-slate-100 pt-2">
    <p class="mb-1 text-xs font-medium text-slate-400">參考來源({{ citations.length }})</p>
    <ul class="space-y-1">
      <li v-for="c in citations" :key="c.rank">
        <button
          type="button"
          class="flex w-full items-baseline gap-1 text-left text-xs text-slate-500 hover:text-slate-800"
          :aria-expanded="expanded === c.rank"
          @click="toggle(c.rank)"
        >
          <span class="shrink-0 font-mono">[{{ c.rank }}]</span>
          <span class="truncate">{{ c.filename }}</span>
        </button>
        <p
          v-if="expanded === c.rank"
          class="mt-1 whitespace-pre-wrap break-words rounded-lg bg-slate-50 p-2 text-xs text-slate-600"
        >
          {{ c.snippet }}
        </p>
      </li>
    </ul>
  </div>
</template>
