import { VueQueryPlugin } from '@tanstack/vue-query'
import { createPinia } from 'pinia'
import { createApp } from 'vue'

import { refreshSession } from './api/client'
import App from './App.vue'
import router from './router'
import './style.css'

async function bootstrap(): Promise<void> {
  const app = createApp(App)
  app.use(createPinia())
  app.use(router)
  app.use(VueQueryPlugin)
  // 啟動先嘗試 refresh 還原 session(§C.6.3);pinia 已安裝故 store 可用。
  await refreshSession()
  await router.isReady()
  app.mount('#app')
}

void bootstrap()
