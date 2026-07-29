// 與後端 Pydantic schema 一一對應的手寫型別(§C.6.3);後端改 schema 的 PR MUST 同步改此檔。

export interface UserOut {
  id: string
  email: string
  role: string
  created_at: string
}

export interface TokenResponse {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  user: UserOut
}

export interface ConversationOut {
  id: string
  title: string | null
  channel: string
  model_alias: string
  created_at: string
  updated_at: string
}

export interface ConversationPage {
  items: ConversationOut[]
  next_cursor: string | null
}

export interface MessageOut {
  id: string
  role: string
  content: string
  content_meta: Record<string, unknown>
  tokens_in: number | null
  tokens_out: number | null
  latency_ms: number | null
  created_at: string
  citations: CitationOut[]
}

export interface MessagePage {
  items: MessageOut[]
  next_cursor: string | null
}

// ── 文件與引用(P2 §11.1)──────────────────────────────────────────────────
export type DocumentStatus =
  | 'pending'
  | 'parsing'
  | 'chunking'
  | 'embedding'
  | 'ready'
  | 'failed'

export interface DocumentOut {
  id: string
  source_id: string
  filename: string
  mime: string
  size_bytes: number
  status: DocumentStatus
  error: string | null
  created_at: string
  updated_at: string
}

export interface DocumentUploadResponse extends DocumentOut {
  deduplicated: boolean // D8:重複上傳回既有文件,不是錯誤
}

export interface DocumentPage {
  items: DocumentOut[]
  next_cursor: string | null
}

export interface CitationOut {
  rank: number
  chunk_id: string | null // 軟引用(D7):來源刪除後仍保留快照
  document_id: string | null
  filename: string
  snippet: string
  score: number
}

// 統一錯誤 envelope(所有非 2xx;§C.5.1)
export interface ApiErrorBody {
  error: { code: string; message: string; trace_id: string }
}

// ── SSE 事件 data(§H.3;凍結)。未知 event 名稱一律忽略。 ──────────────────
export interface SseMessageStart {
  user_message_id: string
  assistant_message_id: string
}
export interface SseDelta {
  text: string
}
export type SseFinishReason = 'stop' | 'length' | 'aborted' | 'error'
export interface SseDone {
  message_id: string
  finish_reason: SseFinishReason
  tokens_in: number | null
  tokens_out: number | null
  latency_ms: number
}
export interface SseError {
  code: string
  message: string
  trace_id: string
}
// D6:message_start 之後、首個 delta 之前;純聊天與查無結果時 NEVER 送出。
export interface SseCitations {
  items: CitationOut[]
}
