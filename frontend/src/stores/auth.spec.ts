import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import { useAuthStore } from './auth'

describe('auth store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('setAuth 設定 token/user,clear 清空', () => {
    const auth = useAuthStore()
    expect(auth.isAuthenticated).toBe(false)

    auth.setAuth('tok', { id: '1', email: 'a@example.com', role: 'user', created_at: 'now' })
    expect(auth.accessToken).toBe('tok')
    expect(auth.user?.email).toBe('a@example.com')
    expect(auth.isAuthenticated).toBe(true)

    auth.clear()
    expect(auth.accessToken).toBeNull()
    expect(auth.user).toBeNull()
    expect(auth.isAuthenticated).toBe(false)
  })
})
