import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'

import { apiFetch } from '@/api/client'
import type { ConversationOut, ConversationPage } from '@/api/types'

// 側欄對話清單(依 updated_at 排序;P1 取單頁 50 筆,分頁載入為 backlog)。
export function useConversations() {
  return useQuery({
    queryKey: ['conversations'],
    queryFn: async (): Promise<ConversationOut[]> => {
      const page = await apiFetch<ConversationPage>('/conversations?limit=50')
      return page.items
    },
  })
}

export function useCreateConversation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (): Promise<ConversationOut> =>
      apiFetch<ConversationOut>('/conversations', { method: 'POST', body: {} }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['conversations'] })
    },
  })
}
