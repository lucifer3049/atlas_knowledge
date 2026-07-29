<script setup lang="ts">
import { ref } from 'vue'

// 前端預檢(§13):與後端白名單一致(§11.2);後端仍為權威(415/413 由後端最終把關)。
const ACCEPTED_EXTENSIONS = ['.pdf', '.docx', '.txt', '.md', '.html', '.htm'] as const
const MAX_UPLOAD_MB = 20 // = 後端 MAX_UPLOAD_MB;兩處一併調整

defineProps<{ uploading: boolean }>()
const emit = defineEmits<{ select: [files: File[]] }>()

const dragging = ref(false)
const rejected = ref<string[]>([])
const input = ref<HTMLInputElement | null>(null)

function reject(file: File, reason: string): void {
  rejected.value.push(`${file.name}:${reason}`)
}

function accepted(file: File): boolean {
  const dot = file.name.lastIndexOf('.')
  const ext = dot === -1 ? '' : file.name.slice(dot).toLowerCase()
  if (!ACCEPTED_EXTENSIONS.includes(ext as (typeof ACCEPTED_EXTENSIONS)[number])) {
    reject(file, '不支援的檔案型別')
    return false
  }
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
    reject(file, `超過 ${MAX_UPLOAD_MB} MB 上限`)
    return false
  }
  return true
}

function handle(files: FileList | null): void {
  rejected.value = []
  const picked = Array.from(files ?? []).filter(accepted)
  if (picked.length > 0) emit('select', picked)
}

function onDrop(e: DragEvent): void {
  dragging.value = false
  handle(e.dataTransfer?.files ?? null)
}

function onPick(e: Event): void {
  handle((e.target as HTMLInputElement).files)
  if (input.value !== null) input.value.value = '' // 同一檔案可再次選取
}
</script>

<template>
  <div>
    <div
      class="rounded-xl border-2 border-dashed p-6 text-center transition-colors"
      :class="dragging ? 'border-slate-500 bg-slate-100' : 'border-slate-300 bg-white'"
      @dragover.prevent="dragging = true"
      @dragleave.prevent="dragging = false"
      @drop.prevent="onDrop"
    >
      <p class="text-sm text-slate-600">
        將檔案拖放到此處,或
        <button
          type="button"
          class="font-medium text-slate-800 underline disabled:opacity-50"
          :disabled="uploading"
          @click="input?.click()"
        >
          選擇檔案
        </button>
      </p>
      <p class="mt-1 text-xs text-slate-400">
        支援 {{ ACCEPTED_EXTENSIONS.join('、') }},單檔上限 {{ MAX_UPLOAD_MB }} MB
      </p>
      <p v-if="uploading" class="mt-2 text-sm text-slate-500">上傳中…</p>
      <input
        ref="input"
        type="file"
        multiple
        class="hidden"
        :accept="ACCEPTED_EXTENSIONS.join(',')"
        @change="onPick"
      />
    </div>

    <ul v-if="rejected.length > 0" class="mt-2 space-y-1" role="alert">
      <li v-for="msg in rejected" :key="msg" class="text-sm text-red-600">{{ msg }}</li>
    </ul>
  </div>
</template>
