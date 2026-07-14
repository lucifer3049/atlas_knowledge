import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { apiFetch } from './client'
import type { TokenResponse } from './types'

const TOKEN: TokenResponse = {
  access_token: 'new-token',
  token_type: 'bearer',
  expires_in: 1200,
  user: { id: '1', email: 'a@example.com', role: 'user', created_at: 'now' },
}

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('apiFetch 401 single-flight refresh', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('並發 401 只觸發一次 refresh,且重試後成功', async () => {
    let refreshed = false
    let refreshCount = 0

    const fetchMock = vi.fn((input: string | URL): Promise<Response> => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url === '/api/auth/refresh') {
        refreshCount += 1
        refreshed = true
        return Promise.resolve(jsonResponse(TOKEN, 200))
      }
      if (!refreshed) {
        return Promise.resolve(
          jsonResponse({ error: { code: 'invalid_token', message: 'x', trace_id: 't' } }, 401),
        )
      }
      return Promise.resolve(jsonResponse({ ok: true }, 200))
    })
    vi.stubGlobal('fetch', fetchMock)

    // 兩個並發請求同時撞 401 → 共享單一 refresh
    const [a, b] = await Promise.all([
      apiFetch<{ ok: boolean }>('/conversations'),
      apiFetch<{ ok: boolean }>('/conversations'),
    ])

    expect(refreshCount).toBe(1)
    expect(a.ok).toBe(true)
    expect(b.ok).toBe(true)
  })
})
