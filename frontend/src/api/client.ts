// 單一 fetch wrapper(§C.6.3):自動帶 Bearer;401 → single-flight refresh(模組層共享
// 一個 in-flight Promise)→ 重試一次 → 仍失敗則 clear() + 導向 /login。全程同源,cookie
// 由瀏覽器隨同源請求自動帶上(refresh cookie 為 HttpOnly)。

import router from '@/router'
import { useAuthStore } from '@/stores/auth'

import type { ApiErrorBody, TokenResponse } from './types'

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly traceId?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export interface RequestOptions {
  method?: string
  body?: unknown
  signal?: AbortSignal
}

let refreshInFlight: Promise<boolean> | null = null

// single-flight:多個並發 401 共享同一個 refresh 請求(§14:refresh 只發生一次)。
export function refreshSession(): Promise<boolean> {
  if (refreshInFlight === null) {
    refreshInFlight = doRefresh().finally(() => {
      refreshInFlight = null
    })
  }
  return refreshInFlight
}

async function doRefresh(): Promise<boolean> {
  const res = await fetch('/api/auth/refresh', { method: 'POST' })
  if (!res.ok) return false
  const data = (await res.json()) as TokenResponse
  useAuthStore().setAuth(data.access_token, data.user)
  return true
}

export async function toApiError(res: Response): Promise<ApiError> {
  let code = 'unknown'
  let message = res.statusText
  let traceId: string | undefined
  try {
    const body = (await res.json()) as ApiErrorBody
    code = body.error.code
    message = body.error.message
    traceId = body.error.trace_id
  } catch {
    // 非 JSON 錯誤(理論上不會發生):沿用 statusText
  }
  return new ApiError(res.status, code, message, traceId)
}

function authHeaders(hasBody: boolean): Record<string, string> {
  const headers: Record<string, string> = {}
  if (hasBody) headers['Content-Type'] = 'application/json'
  const token = useAuthStore().accessToken
  if (token !== null) headers['Authorization'] = `Bearer ${token}`
  return headers
}

// 401 → single-flight refresh → 重試一次;JSON 與 multipart 共用同一條路徑。
async function sendWithRefresh(path: string, send: () => Promise<Response>): Promise<Response> {
  let res = await send()
  // auth 端點自身的 401 不再遞迴 refresh(避免無限迴圈)。
  if (res.status === 401 && !path.startsWith('/auth/')) {
    const ok = await refreshSession()
    if (ok) {
      res = await send()
    } else {
      useAuthStore().clear()
      void router.push('/login')
      throw await toApiError(res)
    }
  }
  return res
}

async function parse<T>(res: Response): Promise<T> {
  if (!res.ok) throw await toApiError(res)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export async function apiFetch<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const res = await sendWithRefresh(path, () =>
    fetch(`/api${path}`, {
      method: opts.method ?? 'GET',
      headers: authHeaders(opts.body !== undefined),
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    }),
  )
  return parse<T>(res)
}

// multipart 上傳(文件上傳,P2 §11.2):Content-Type 交給瀏覽器自動帶 boundary,
// NEVER 自行設定,否則 boundary 遺失、後端解析失敗。
export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  const res = await sendWithRefresh(path, () =>
    fetch(`/api${path}`, { method: 'POST', headers: authHeaders(false), body: form }),
  )
  return parse<T>(res)
}
