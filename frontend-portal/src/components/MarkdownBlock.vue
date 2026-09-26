<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const props = defineProps<{ source: string | null | undefined; terms?: string[]; inline?: boolean }>()
const renderer = new MarkdownIt({ html: false, linkify: true, breaks: true })
const host = ref<HTMLElement | null>(null)

function markTerms(root: HTMLElement, terms: string[]) {
  const pattern = terms
    .filter(Boolean)
    .map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .join('|')
  if (!pattern) return
  const matcher = new RegExp(`(${pattern})`, 'gi')
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const targets: Text[] = []
  while (walker.nextNode()) targets.push(walker.currentNode as Text)
  for (const node of targets) {
    if (!matcher.test(node.data)) continue
    matcher.lastIndex = 0
    const fragment = document.createDocumentFragment()
    let cursor = 0
    for (const match of node.data.matchAll(matcher)) {
      const at = match.index ?? 0
      if (at > cursor) fragment.append(document.createTextNode(node.data.slice(cursor, at)))
      const mark = document.createElement('mark')
      mark.textContent = match[0]
      fragment.append(mark)
      cursor = at + match[0].length
    }
    if (cursor < node.data.length) fragment.append(document.createTextNode(node.data.slice(cursor)))
    node.replaceWith(fragment)
  }
}

async function paint() {
  await nextTick()
  if (!host.value) return
  const source = props.source ?? ''
  const html = props.inline ? renderer.renderInline(source) : renderer.render(source)
  host.value.innerHTML = DOMPurify.sanitize(html, { USE_PROFILES: { html: true } })
  markTerms(host.value, props.terms ?? [])
}

watch(() => [props.source, props.terms, props.inline], paint, { deep: true })
onMounted(paint)
</script>

<template>
  <div ref="host" class="markdown" :class="{ 'markdown-inline': inline }" />
</template>

<style scoped>
.markdown :deep(p) {
  margin: 0 0 10px;
}
.markdown :deep(p:last-child) {
  margin-bottom: 0;
}
.markdown :deep(h1),
.markdown :deep(h2),
.markdown :deep(h3),
.markdown :deep(h4) {
  margin: 16px 0 8px;
  font-size: 15px;
}
.markdown :deep(ul),
.markdown :deep(ol) {
  margin: 0 0 10px;
  padding-left: 22px;
}
.markdown :deep(code) {
  background: #f2f4f3;
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 13px;
}
.markdown :deep(pre) {
  background: #f7f8f7;
  padding: 12px;
  border-radius: 4px;
  overflow: auto;
  margin: 0 0 10px;
}
.markdown :deep(pre code) {
  background: none;
  padding: 0;
}
.markdown :deep(table) {
  border-collapse: collapse;
  margin: 0 0 10px;
  width: 100%;
  font-size: 13px;
}
.markdown :deep(th),
.markdown :deep(td) {
  border: 1px solid #e5e7e6;
  padding: 6px 8px;
  text-align: left;
}
.markdown :deep(blockquote) {
  margin: 0 0 10px;
  padding: 2px 12px;
  border-left: 3px solid #d8dedb;
  color: #5c6662;
}
.markdown :deep(a) {
  color: #18a058;
}
.markdown :deep(mark) {
  background: #e7f5ec;
  color: #137a43;
  padding: 0 2px;
}
.markdown-inline {
  display: inline;
}
</style>
