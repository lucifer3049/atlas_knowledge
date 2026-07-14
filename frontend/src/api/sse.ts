// SSE 消費(§C.6.3):fetch + ReadableStream 自寫薄層(EventSource 不支援 POST)。
// 以空行切 frame、讀 event:/data:;註解行(心跳 ": ping")與未知 event 交由消費端忽略。
// 401 沿用 client 的 single-flight refresh 重試一次。

import { refreshSession, toApiError } from './client'
import { useAuthStore } from '@/stores/auth'

export interface SseEvent {
  event: string
  data: string
}

function postSse(path: string, body: unknown, signal: AbortSignal): Promise<Response> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = useAuthStore().accessToken
  if (token !== null) headers['Authorization'] = `Bearer ${token}`
  return fetch(`/api${path}`, { method: 'POST', headers, body: JSON.stringify(body), signal })
}

export function parseFrame(frame: string): SseEvent | null {
  let event: string | null = null
  let data = ''
  for (const raw of frame.split('\n')) {
    const line = raw.replace(/\r$/, '')
    if (line.startsWith(':')) continue // 註解行(心跳)
    if (line.startsWith('event:')) event = line.slice('event:'.length).trim()
    else if (line.startsWith('data:')) data = line.slice('data:'.length).trim()
  }
  return event === null ? null : { event, data }
}

export async function* streamChat(
  path: string,
  body: unknown,
  signal: AbortSignal,
): AsyncGenerator<SseEvent> {
  let res = await postSse(path, body, signal)
  if (res.status === 401 && (await refreshSession())) {
    res = await postSse(path, body, signal)
  }
  // 串流開始前的錯誤(401/404/409/422)走一般 JSON,於此拋出(§9)。
  if (!res.ok || res.body === null) throw await toApiError(res)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let sep = buffer.indexOf('\n\n')
    while (sep !== -1) {
      const frame = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      const ev = parseFrame(frame)
      if (ev !== null) yield ev
      sep = buffer.indexOf('\n\n')
    }
  }
}
