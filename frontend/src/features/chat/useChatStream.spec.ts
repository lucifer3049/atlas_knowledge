import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { describe, expect, it, vi } from 'vitest'
import { createApp } from 'vue'

import type { SseEvent } from '@/api/sse'

// streamChat 以腳本驅動:模擬伺服器送出的 SSE 事件序列
const script: SseEvent[] = []
vi.mock('@/api/sse', () => ({
  streamChat: vi.fn(async function* (): AsyncGenerator<SseEvent> {
    for (const ev of script) yield ev
  }),
}))

import { useChatStream } from './useChatStream'

// useQueryClient 需要 vue-query 的注入環境:以 app.runWithContext 提供
function withQueryClient<T>(fn: () => T): T {
  const app = createApp({ render: () => null })
  app.use(VueQueryPlugin, { queryClient: new QueryClient() })
  return app.runWithContext(fn)
}

describe('useChatStream 終端事件收斂', () => {
  it('正常 done → status=done', async () => {
    script.length = 0
    script.push(
      { event: 'message_start', data: '{"user_message_id":"u1","assistant_message_id":"a1"}' },
      { event: 'delta', data: '{"text":"哈"}' },
      {
        event: 'done',
        data: '{"message_id":"a1","finish_reason":"stop","tokens_in":1,"tokens_out":1,"latency_ms":5}',
      },
    )
    const { status, send } = withQueryClient(() => useChatStream('conv-1'))
    await send('hi')
    expect(status.value).toBe('done')
  })

  it('串流結束但無 done/error 終端事件 → status 收斂為 error,不卡在 streaming', async () => {
    script.length = 0
    script.push(
      { event: 'message_start', data: '{"user_message_id":"u1","assistant_message_id":"a1"}' },
      { event: 'delta', data: '{"text":"哈"}' },
      // 伺服器異常關閉:沒有終端事件
    )
    const { status, errorMessage, send } = withQueryClient(() => useChatStream('conv-1'))
    await send('hi')
    expect(status.value).toBe('error')
    expect(errorMessage.value).not.toBeNull()
  })
})
