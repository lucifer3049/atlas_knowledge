import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { ref } from 'vue'

import { apiFetch } from '@/api/client'
import type { MessageOut, MessagePage } from '@/api/types'

// 快取形狀:items 為顯示用「時間正序」;earlierCursor 用於「載入更早」(§13)。
export interface MessagesCache {
  items: MessageOut[]
  earlierCursor: string | null
}

export function messagesKey(conversationId: string): readonly ['messages', string] {
  return ['messages', conversationId]
}

async function fetchPage(conversationId: string, cursor: string | null): Promise<MessagePage> {
  const q = cursor === null ? '' : `&cursor=${encodeURIComponent(cursor)}`
  return apiFetch<MessagePage>(`/conversations/${conversationId}/messages?limit=20${q}`)
}

export function useMessages(conversationId: string) {
  const qc = useQueryClient()
  const loadingEarlier = ref(false)

  const query = useQuery({
    queryKey: messagesKey(conversationId),
    queryFn: async (): Promise<MessagesCache> => {
      const page = await fetchPage(conversationId, null)
      // 後端回 desc(新→舊);顯示需 asc,故反轉。
      return { items: [...page.items].reverse(), earlierCursor: page.next_cursor }
    },
  })

  async function loadEarlier(): Promise<void> {
    const cache = qc.getQueryData<MessagesCache>(messagesKey(conversationId))
    if (cache === undefined || cache.earlierCursor === null || loadingEarlier.value) return
    loadingEarlier.value = true
    try {
      const page = await fetchPage(conversationId, cache.earlierCursor)
      qc.setQueryData<MessagesCache>(messagesKey(conversationId), {
        items: [...[...page.items].reverse(), ...cache.items],
        earlierCursor: page.next_cursor,
      })
    } finally {
      loadingEarlier.value = false
    }
  }

  return { query, loadEarlier, loadingEarlier }
}
