import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/chat' },
    {
      path: '/login',
      name: 'login',
      component: () => import('@/features/auth/LoginPage.vue'),
      meta: { public: true },
    },
    {
      path: '/register',
      name: 'register',
      component: () => import('@/features/auth/RegisterPage.vue'),
      meta: { public: true },
    },
    {
      path: '/chat/:id?',
      name: 'chat',
      component: () => import('@/features/chat/ChatLayout.vue'),
    },
  ],
})

// session 於 app 啟動先行還原(main.ts),故此處只需檢查 store 狀態。
router.beforeEach((to) => {
  const auth = useAuthStore()
  if (to.meta.public !== true && !auth.isAuthenticated) return { name: 'login' }
  if (to.meta.public === true && auth.isAuthenticated) return { name: 'chat' }
  return true
})

export default router
