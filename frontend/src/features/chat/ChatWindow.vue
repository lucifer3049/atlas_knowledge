<script setup lang="ts">
import { computed } from 'vue'

import MessageInput from './MessageInput.vue'
import MessageList from './MessageList.vue'
import StreamingMessage from './StreamingMessage.vue'
import { useChatStream } from './useChatStream'
import { useMessages } from './useMessages'

const props = defineProps<{ conversationId: string }>()

const { query, loadEarlier, loadingEarlier } = useMessages(props.conversationId)
const { status, streamingText, errorMessage, send, abort } = useChatStream(props.conversationId)

const messages = computed(() => query.data.value?.items ?? [])
const canLoadEarlier = computed(() => (query.data.value?.earlierCursor ?? null) !== null)
const streaming = computed(() => status.value === 'streaming')

function onSend(content: string): void {
  void send(content)
}
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col">
    <div class="min-h-0 flex-1 overflow-y-auto">
      <p v-if="query.isLoading.value" class="p-4 text-center text-sm text-slate-400">載入中…</p>
      <MessageList
        v-else
        :messages="messages"
        :can-load-earlier="canLoadEarlier"
        :loading-earlier="loadingEarlier"
        @load-earlier="loadEarlier"
      />
      <StreamingMessage v-if="streaming || streamingText !== ''" :text="streamingText" />
    </div>

    <p
      v-if="status === 'error' && errorMessage !== null"
      class="px-4 py-1 text-center text-sm text-red-600"
      role="alert"
    >
      {{ errorMessage }}(可重新送出)
    </p>

    <MessageInput :streaming="streaming" @send="onSend" @abort="abort" />
  </div>
</template>
