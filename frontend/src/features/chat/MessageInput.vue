<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{ streaming: boolean }>()
const emit = defineEmits<{ send: [content: string, useKnowledge: boolean]; abort: [] }>()

const text = ref('')
// 預設關閉 = P1 純聊天行為;開啟則檢索全部來源(P2 無來源多選,列 P3 ScopeSelector)
const useKnowledge = ref(false)

function submit(): void {
  const content = text.value.trim()
  if (content === '' || props.streaming) return
  emit('send', content, useKnowledge.value)
  text.value = ''
}

function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    submit()
  }
}
</script>

<template>
  <div class="border-t border-slate-200 bg-white p-3">
    <div class="mx-auto mb-2 flex max-w-3xl items-center gap-2">
      <label class="flex cursor-pointer items-center gap-2 text-sm text-slate-600">
        <input v-model="useKnowledge" type="checkbox" class="h-4 w-4 accent-slate-800" />
        使用知識庫
      </label>
      <span v-if="useKnowledge" class="text-xs text-slate-400">回答將附上參考來源</span>
    </div>
    <div class="mx-auto flex max-w-3xl items-end gap-2">
      <textarea
        v-model="text"
        rows="1"
        placeholder="輸入訊息…(Enter 送出,Shift+Enter 換行)"
        class="max-h-40 min-h-[2.5rem] flex-1 resize-none rounded-lg border border-slate-300 px-3 py-2 focus:border-slate-500 focus:outline-none"
        @keydown="onKeydown"
      ></textarea>
      <button
        v-if="streaming"
        type="button"
        class="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-500"
        @click="emit('abort')"
      >
        停止
      </button>
      <button
        v-else
        type="button"
        :disabled="text.trim() === ''"
        class="rounded-lg bg-slate-800 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        @click="submit"
      >
        送出
      </button>
    </div>
  </div>
</template>
