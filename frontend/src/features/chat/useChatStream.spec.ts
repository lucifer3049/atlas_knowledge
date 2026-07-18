import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { describe, expect, it, vi } from 'vitest'
import { createApp, effectScope, type EffectScope } from 'vue'

import type { SseEvent } from '@/api/sse'

// streamChat 以腳本驅動:模擬伺服器送出的 SSE 事件序列;
// hangAfterScript 模擬「伺服器持續串流中」(直到 signal abort 才結束)。
const script: SseEvent[] = []
let hangAfterScript = false
const capturedSignals: AbortSignal[] = []

vi.mock('@/api/sse', () => ({
  streamChat: vi.fn(async function* (
    _path: unknown,
    _body: unknown,
    signal: AbortSignal,
  ): AsyncGenerator<SseEvent> {
    capturedSignals.push(signal)
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

// useQueryClient 需要 vue-query 注入環境;onScopeDispose 需要 effect scope
function inScope<T>(fn: () => T): { result: T; scope: EffectScope } {
  const scope = effectScope()
  const app = createApp({ render: () => null })
  app.use(VueQueryPlugin, { queryClient: new QueryClient() })
  const result = app.runWithContext(() => scope.run(fn) as T)
  return { result, scope }
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
