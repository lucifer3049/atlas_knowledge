import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { describe, expect, it, vi } from 'vitest'
import { createApp, effectScope, type EffectScope } from 'vue'

import type { SseEvent } from '@/api/sse'

// streamChat 以腳本驅動:模擬伺服器送出的 SSE 事件序列;
// hangAfterScript 模擬「伺服器持續串流中」(直到 signal abort 才結束)。
const script: SseEvent[] = []
let hangAfterScript = false
const capturedSignals: AbortSignal[] = []
const capturedBodies: unknown[] = []

vi.mock('@/api/sse', () => ({
  streamChat: vi.fn(async function* (
    _path: unknown,
    body: unknown,
    signal: AbortSignal,
  ): AsyncGenerator<SseEvent> {
    capturedSignals.push(signal)
    capturedBodies.push(body)
    for (const ev of script) yield ev
    if (hangAfterScript) {
      if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
      await new Promise((_, reject) => {
        signal.addEventListener('abort', () =>
          reject(new DOMException('Aborted', 'AbortError')),
        )
      })
    }
  }),
}))

import { useChatStream } from './useChatStream'
import { messagesKey, type MessagesCache } from './useMessages'

// useQueryClient 需要 vue-query 注入環境;onScopeDispose 需要 effect scope
function inScope<T>(fn: () => T): { result: T; scope: EffectScope; qc: QueryClient } {
  const scope = effectScope()
  const app = createApp({ render: () => null })
  const qc = new QueryClient()
  app.use(VueQueryPlugin, { queryClient: qc })
  const result = app.runWithContext(() => scope.run(fn) as T)
  return { result, scope, qc }
}

describe('useChatStream', () => {
  it('正常 done → status=done', async () => {
    script.length = 0
    hangAfterScript = false
    script.push(
      { event: 'message_start', data: '{"user_message_id":"u1","assistant_message_id":"a1"}' },
      { event: 'delta', data: '{"text":"哈"}' },
      {
        event: 'done',
        data: '{"message_id":"a1","finish_reason":"stop","tokens_in":1,"tokens_out":1,"latency_ms":5}',
      },
    )
    const { result } = inScope(() => useChatStream('conv-1'))
    await result.send('hi')
    expect(result.status.value).toBe('done')
  })

  it('串流結束但無 done/error 終端事件 → status 收斂為 error,不卡在 streaming', async () => {
    script.length = 0
    hangAfterScript = false
    script.push(
      { event: 'message_start', data: '{"user_message_id":"u1","assistant_message_id":"a1"}' },
      { event: 'delta', data: '{"text":"哈"}' },
      // 伺服器異常關閉:沒有終端事件
    )
    const { result } = inScope(() => useChatStream('conv-1'))
    await result.send('hi')
    expect(result.status.value).toBe('error')
    expect(result.errorMessage.value).not.toBeNull()
  })

  it('citations 事件 → 串流中即可讀,done 後併入該則 assistant 訊息', async () => {
    script.length = 0
    hangAfterScript = false
    script.push(
      { event: 'message_start', data: '{"user_message_id":"u1","assistant_message_id":"a1"}' },
      {
        event: 'citations',
        data: '{"items":[{"rank":1,"chunk_id":"c1","document_id":"d1","filename":"手冊.pdf","snippet":"報帳流程","score":0.03}]}',
      },
      { event: 'delta', data: '{"text":"依據 [1]"}' },
      {
        event: 'done',
        data: '{"message_id":"a1","finish_reason":"stop","tokens_in":1,"tokens_out":1,"latency_ms":5}',
      },
    )
    const { result, qc } = inScope(() => useChatStream('conv-1'))
    await result.send('報帳怎麼跑?', true)

    const cache = qc.getQueryData<MessagesCache>(messagesKey('conv-1'))
    const assistant = cache?.items.find((m) => m.role === 'assistant')
    expect(assistant?.citations).toEqual([
      {
        rank: 1,
        chunk_id: 'c1',
        document_id: 'd1',
        filename: '手冊.pdf',
        snippet: '報帳流程',
        score: 0.03,
      },
    ])
    expect(result.streamingCitations.value).toEqual([]) // 串流結束即重置
  })

  it('未使用知識庫 → NEVER 送 knowledge_scope(純聊天,P1 行為)', async () => {
    script.length = 0
    hangAfterScript = false
    capturedBodies.length = 0
    script.push({
      event: 'done',
      data: '{"message_id":"a1","finish_reason":"stop","tokens_in":null,"tokens_out":null,"latency_ms":5}',
    })
    const { result } = inScope(() => useChatStream('conv-1'))
    await result.send('hi')
    expect(capturedBodies[0]).not.toHaveProperty('knowledge_scope')

    await result.send('hi', true)
    expect(capturedBodies[1]).toMatchObject({ knowledge_scope: { source_ids: [] } })
  })

  it('未知 event(如 P6 tool_call_started)一律忽略,不影響終端狀態', async () => {
    script.length = 0
    hangAfterScript = false
    script.push(
      { event: 'message_start', data: '{"user_message_id":"u1","assistant_message_id":"a1"}' },
      { event: 'tool_call_started', data: '{"call_id":"t1","name":"future_tool"}' },
      { event: 'delta', data: '{"text":"哈"}' },
      {
        event: 'done',
        data: '{"message_id":"a1","finish_reason":"stop","tokens_in":null,"tokens_out":null,"latency_ms":5}',
      },
    )
    const { result } = inScope(() => useChatStream('conv-1'))
    await result.send('hi')
    expect(result.status.value).toBe('done')
  })

  it('scope dispose(元件卸載/切換對話)→ abort 進行中的串流', async () => {
    script.length = 0
    capturedSignals.length = 0
    script.push({
      event: 'message_start',
      data: '{"user_message_id":"u1","assistant_message_id":"a1"}',
    })
    hangAfterScript = true // 串流懸掛中,僅 abort 能結束

    const { result, scope } = inScope(() => useChatStream('conv-1'))
    const sending = result.send('hi')
    await vi.waitFor(() => expect(capturedSignals.length).toBeGreaterThan(0))

    scope.stop() // 模擬元件卸載
    await sending

    expect(capturedSignals[0]?.aborted).toBe(true) // NEVER 留下孤兒串流繼續耗用 LLM
    expect(result.status.value).toBe('idle') // abort 語意,非 error
    hangAfterScript = false
  })
})
