<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError, apiFetch } from '@/api/client'
import type { TokenResponse, UserOut } from '@/api/types'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

const email = ref('')
const password = ref('')
const error = ref<string | null>(null)
const submitting = ref(false)

async function onSubmit(): Promise<void> {
  error.value = null
  submitting.value = true
  try {
    // 註冊回 201 UserOut(無 token);緊接自動登入還原體驗(§10.3)。
    await apiFetch<UserOut>('/auth/register', {
      method: 'POST',
      body: { email: email.value, password: password.value },
    })
    const res = await apiFetch<TokenResponse>('/auth/login', {
      method: 'POST',
      body: { email: email.value, password: password.value },
    })
    auth.setAuth(res.access_token, res.user)
    await router.push('/chat')
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : '註冊失敗,請稍後再試'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="flex min-h-screen items-center justify-center bg-slate-50 p-4">
    <form
      class="w-full max-w-sm space-y-4 rounded-xl bg-white p-6 shadow-sm"
      @submit.prevent="onSubmit"
    >
      <h1 class="text-xl font-bold text-slate-800">註冊</h1>

      <label class="block space-y-1">
        <span class="text-sm text-slate-600">Email</span>
        <input
          v-model="email"
          type="email"
          required
          autocomplete="email"
          class="w-full rounded-lg border border-slate-300 px-3 py-2 focus:border-slate-500 focus:outline-none"
        />
      </label>

      <label class="block space-y-1">
        <span class="text-sm text-slate-600">密碼(至少 8 碼)</span>
        <input
          v-model="password"
          type="password"
          required
          minlength="8"
          autocomplete="new-password"
          class="w-full rounded-lg border border-slate-300 px-3 py-2 focus:border-slate-500 focus:outline-none"
        />
      </label>

      <p v-if="error" class="text-sm text-red-600" role="alert">{{ error }}</p>

      <button
        type="submit"
        :disabled="submitting"
        class="w-full rounded-lg bg-slate-800 py-2 font-medium text-white hover:bg-slate-700 disabled:opacity-50"
      >
        {{ submitting ? '註冊中…' : '註冊' }}
      </button>

      <p class="text-center text-sm text-slate-500">
        已有帳號?
        <RouterLink to="/login" class="text-slate-800 underline">登入</RouterLink>
      </p>
    </form>
  </main>
</template>
