import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'

import { apiFetch, apiUpload } from '@/api/client'
import type { DocumentOut, DocumentPage, DocumentStatus, DocumentUploadResponse } from '@/api/types'

// 終態 = 不會再自行變動的狀態;非終態存在時才輪詢(§13、§C.6.3)。
const TERMINAL: readonly DocumentStatus[] = ['ready', 'failed']
const POLL_INTERVAL_MS = 3000

export const documentsKey = ['documents'] as const

export function isTerminal(status: DocumentStatus): boolean {
  return TERMINAL.includes(status)
}

export function useDocuments() {
  return useQuery({
    queryKey: documentsKey,
    queryFn: async (): Promise<DocumentOut[]> => {
      const page = await apiFetch<DocumentPage>('/documents?limit=50')
      return page.items
    },
    // 僅在存在非終態文件時輪詢,全部終態即停;NEVER 無條件輪詢(§13)。
    refetchInterval: (query) => {
      const items = query.state.data ?? []
      return items.some((d) => !isTerminal(d.status)) ? POLL_INTERVAL_MS : false
    },
  })
}

export function useUploadDocument() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (file: File): Promise<DocumentUploadResponse> => {
      const form = new FormData()
      form.append('file', file)
      return apiUpload<DocumentUploadResponse>('/documents', form)
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: documentsKey })
    },
  })
}

export function useRetryDocument() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string): Promise<DocumentOut> =>
      apiFetch<DocumentOut>(`/documents/${id}/retry`, { method: 'POST' }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: documentsKey })
    },
  })
}

export function useDeleteDocument() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string): Promise<void> =>
      apiFetch<void>(`/documents/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: documentsKey })
    },
  })
}
