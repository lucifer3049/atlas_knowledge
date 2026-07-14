<script setup lang="ts">
import type { ConversationOut } from '@/api/types'

defineProps<{
  conversations: ConversationOut[]
  activeId: string | undefined
}>()

defineEmits<{
  select: [id: string]
}>()
</script>

<template>
  <nav class="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
    <p v-if="conversations.length === 0" class="px-2 py-4 text-sm text-slate-400">尚無對話</p>
    <ul class="space-y-1">
      <li v-for="c in conversations" :key="c.id">
        <button
          class="w-full truncate rounded-lg px-3 py-2 text-left text-sm hover:bg-slate-100"
          :class="c.id === activeId ? 'bg-slate-100 font-medium' : ''"
          @click="$emit('select', c.id)"
        >
          {{ c.title ?? '新對話' }}
        </button>
      </li>
    </ul>
  </nav>
</template>
