import { useQueryClient } from '@tanstack/vue-query'
import { onScopeDispose, ref } from 'vue'

import { streamChat } from '@/api/sse'
import type {
  CitationOut,
  MessageOut,
  SseCitations,
  SseDelta,
  SseDone,
  SseError,
  SseMessageStart,
} from '@/api/types'

import { messagesKey, type MessagesCache } from './useMessages'

export type ChatStatus = 'idle' | 'streaming' | 'done' | 'error'

function nowIso(): string {
  return new Date().toISOString()
}

function message(
  id: string,
  role: string,
  content: string,
  done: SseDone | null,
  citations: CitationOut[] = [],
): MessageOut {
  return {
    id,
    role,
    content,
    content_meta: {},
    tokens_in: done?.tokens_in ?? null,
    tokens_out: done?.tokens_out ?? null,
    latency_ms: done?.latency_ms ?? null,
    created_at: nowIso(),
    citations,
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
  // citations 在首個 delta 之前抵達(D6),故串流中即可渲染來源。
  const streamingCitations = ref<CitationOut[]>([])
  const errorMessage = ref<string | null>(null)
  let controller: AbortController | null = null

  function patch(fn: (items: MessageOut[]) => MessageOut[]): void {
    qc.setQueryData<MessagesCache>(messagesKey(conversationId), (old) => {
      const base: MessagesCache = old ?? { items: [], earlierCursor: null }
      return { ...base, items: fn(base.items) }
    })
  }

  /** `useKnowledge` = 使用知識庫(§10.1:送 knowledge_scope,P2 為全部來源)。 */
  async function send(content: string, useKnowledge = false): Promise<void> {
    const clientMessageId = crypto.randomUUID()
    const tempUserId = `temp-${clientMessageId}`
    status.value = 'streaming'
    streamingText.value = ''
    streamingCitations.value = []
    errorMessage.value = null
    // 樂觀插入 user 訊息;message_start 後以真實 id 取代。
    patch((items) => [...items, message(tempUserId, 'user', content, null)])
    controller = new AbortController()
    let assistantId: string | null = null

    try {
      for await (const ev of streamChat(
        `/conversations/${conversationId}/messages`,
        {
          content,
          client_message_id: clientMessageId,
          // 未使用知識庫時 NEVER 送 knowledge_scope(= 純聊天,P1 行為)
          ...(useKnowledge ? { knowledge_scope: { source_ids: [] } } : {}),
        },
        controller.signal,
      )) {
        if (ev.event === 'message_start') {
          const d = JSON.parse(ev.data) as SseMessageStart
          assistantId = d.assistant_message_id
          patch((items) =>
            items.map((m) => (m.id === tempUserId ? { ...m, id: d.user_message_id } : m)),
          )
        } else if (ev.event === 'citations') {
          streamingCitations.value = (JSON.parse(ev.data) as SseCitations).items
        } else if (ev.event === 'delta') {
          streamingText.value += (JSON.parse(ev.data) as SseDelta).text
        } else if (ev.event === 'done') {
          const d = JSON.parse(ev.data) as SseDone
          const citations = streamingCitations.value
          patch((items) => [
            ...items,
            message(assistantId ?? d.message_id, 'assistant', streamingText.value, d, citations),
          ])
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
      // 伺服器異常關閉(串流結束但無 done/error 終端事件):NEVER 卡在 streaming
      if (status.value === 'streaming') {
        errorMessage.value = '連線中斷,請重新送出'
        status.value = 'error'
      }
      streamingText.value = ''
      streamingCitations.value = []
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

  // 元件卸載(切換/離開對話)時中止進行中的串流:NEVER 留下孤兒請求繼續耗用 LLM
  onScopeDispose(abort)

  return { status, streamingText, streamingCitations, errorMessage, send, abort }
}
