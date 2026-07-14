import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import type { UserOut } from '@/api/types'

// access token 只存記憶體(NEVER localStorage);refresh 走 HttpOnly cookie(§C.6.3)。
export const useAuthStore = defineStore('auth', () => {
  const accessToken = ref<string | null>(null)
  const user = ref<UserOut | null>(null)
  const isAuthenticated = computed(() => accessToken.value !== null)

  function setAuth(token: string, u: UserOut): void {
    accessToken.value = token
    user.value = u
  }

  function clear(): void {
    accessToken.value = null
    user.value = null
  }

  return { accessToken, user, isAuthenticated, setAuth, clear }
})
