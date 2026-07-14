import { useQueryClient } from '@tanstack/vue-query'
import { ref } from 'vue'

import { streamChat } from '@/api/sse'
import type { MessageOut, SseDelta, SseDone, SseError, SseMessageStart } from '@/api/types'

import { messagesKey, type MessagesCache } from './useMessages'

export type ChatStatus = 'idle' | 'streaming' | 'done' | 'error'

function nowIso(): string {
  return new Date().toISOString()
}

function message(id: string, role: string, content: string, done: SseDone | null): MessageOut {
  return {
    id,
    role,
    content,
    content_meta: {},
    tokens_in: done?.tokens_in ?? null,
    tokens_out: done?.tokens_out ?? null,
    latency_ms: done?.latency_ms ?? null,
    created_at: nowIso(),
  }
}

function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError'
}

// {status, streamingText, send, abort}(§13);內部更新 ['messages', id] 與 ['conversations'] 快取。
export function useChatStream(conversationId: string) {
  const qc = useQueryClient()
  const status = ref<ChatStatus>('idle')
  const streamingText = ref('')
  const errorMessage = ref<string | null>(null)
  let controller: AbortController | null = null

  function patch(fn: (items: MessageOut[]) => MessageOut[]): void {
    qc.setQueryData<MessagesCache>(messagesKey(conversationId), (old) => {
      const base: MessagesCache = old ?? { items: [], earlierCursor: null }
      return { ...base, items: fn(base.items) }
    })
  }

  async function send(content: string): Promise<void> {
    const clientMessageId = crypto.randomUUID()
    const tempUserId = `temp-${clientMessageId}`
    status.value = 'streaming'
    streamingText.value = ''
    errorMessage.value = null
    // 樂觀插入 user 訊息;message_start 後以真實 id 取代。
    patch((items) => [...items, message(tempUserId, 'user', content, null)])
    controller = new AbortController()
    let assistantId: string | null = null

    try {
      for await (const ev of streamChat(
        `/conversations/${conversationId}/messages`,
        { content, client_message_id: clientMessageId },
        controller.signal,
      )) {
        if (ev.event === 'message_start') {
          const d = JSON.parse(ev.data) as SseMessageStart
          assistantId = d.assistant_message_id
          patch((items) =>
            items.map((m) => (m.id === tempUserId ? { ...m, id: d.user_message_id } : m)),
          )
        } else if (ev.event === 'delta') {
          streamingText.value += (JSON.parse(ev.data) as SseDelta).text
        } else if (ev.event === 'done') {
          const d = JSON.parse(ev.data) as SseDone
          patch((items) => [...items, message(assistantId ?? d.message_id, 'assistant', streamingText.value, d)])
          status.value = 'done'
        } else if (ev.event === 'error') {
          errorMessage.value = (JSON.parse(ev.data) as SseError).message
          status.value = 'error'
        }
        // 未知 event 一律忽略(§9 前向相容)
      }
    } catch (err) {
      if (isAbort(err)) {
        status.value = 'idle'
      } else {
        errorMessage.value = err instanceof Error ? err.message : '傳送失敗'
        status.value = 'error'
      }
    } finally {
      streamingText.value = ''
      controller = null
      // 側欄依 updated_at 重排
      void qc.invalidateQueries({ queryKey: ['conversations'] })
      // 非正常完成(error/aborted):與伺服器對齊(已落 partial)
      if (status.value !== 'done') {
        void qc.invalidateQueries({ queryKey: messagesKey(conversationId) })
      }
    }
  }

  function abort(): void {
    controller?.abort()
  }

  return { status, streamingText, errorMessage, send, abort }
}
