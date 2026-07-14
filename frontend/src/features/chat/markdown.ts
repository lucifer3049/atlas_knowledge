// assistant 內容一律 markdown-it 轉譯 + DOMPurify 消毒後才 v-html(§C.6.1)。
import DOMPurify from 'dompurify'
import MarkdownIt from 'markdown-it'

const md = new MarkdownIt({ linkify: true, breaks: true })

export function renderMarkdown(source: string): string {
  return DOMPurify.sanitize(md.render(source))
}
