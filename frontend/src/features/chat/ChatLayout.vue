<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { apiFetch } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

import ChatWindow from './ChatWindow.vue'
import ConversationList from './ConversationList.vue'
import { useConversations, useCreateConversation } from './useConversations'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const { data: conversations, isLoading } = useConversations()
const createConversation = useCreateConversation()

const activeId = computed(() =>
  typeof route.params.id === 'string' && route.params.id !== '' ? route.params.id : undefined,
)

async function onNew(): Promise<void> {
  const conv = await createConversation.mutateAsync()
  await router.push(`/chat/${conv.id}`)
}

function onSelect(id: string): void {
  void router.push(`/chat/${id}`)
}

async function onLogout(): Promise<void> {
  try {
    await apiFetch('/auth/logout', { method: 'POST' })
  } finally {
    auth.clear()
    await router.push('/login')
  }
}
</script>

<template>
  <div class="flex h-screen bg-slate-50 text-slate-800">
    <aside class="flex w-72 shrink-0 flex-col border-r border-slate-200 bg-white">
      <div class="flex items-center justify-between p-3">
        <span class="font-semibold">AI 知識問答</span>
        <button class="text-sm text-slate-500 hover:text-slate-800" @click="onLogout">登出</button>
      </div>
      <div class="px-3 pb-2">
        <button
          class="w-full rounded-lg bg-slate-800 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          :disabled="createConversation.isPending.value"
          @click="onNew"
        >
          + 新對話
        </button>
      </div>
      <p v-if="isLoading" class="px-3 text-sm text-slate-400">載入中…</p>
      <ConversationList
        v-else
        :conversations="conversations ?? []"
        :active-id="activeId"
        @select="onSelect"
      />
    </aside>

    <main class="flex min-w-0 flex-1 flex-col">
      <ChatWindow v-if="activeId" :key="activeId" :conversation-id="activeId" />
      <div v-else class="flex flex-1 items-center justify-center text-slate-400">
        選擇左側對話,或建立新對話開始提問
      </div>
    </main>
  </div>
</template>
