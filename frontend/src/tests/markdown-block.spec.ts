import { afterEach, expect, it } from 'vitest'
import { flushPromises, mount, VueWrapper } from '@vue/test-utils'
import MarkdownBlock from '../components/MarkdownBlock.vue'

let wrapper: VueWrapper | undefined
afterEach(() => wrapper?.unmount())

it('renders markdown structure instead of showing raw markup', async () => {
  wrapper = mount(MarkdownBlock, {
    props: {
      source: '## 缓存穿透\n\n使用 `布隆过滤器` 拦截空值\n\n- 空值缓存\n- 逻辑过期\n\n| 方案 | 说明 |\n| --- | --- |\n| 布隆 | 有假阳性 |\n',
    },
  })
  await flushPromises()
  expect(wrapper.get('h2').text()).toBe('缓存穿透')
  expect(wrapper.get('code').text()).toBe('布隆过滤器')
  expect(wrapper.findAll('li').map((item) => item.text())).toEqual(['空值缓存', '逻辑过期'])
  expect(wrapper.findAll('tbody tr')).toHaveLength(1)
  expect(wrapper.text()).not.toContain('## 缓存穿透')
})

it('escapes embedded html so documents cannot inject markup', async () => {
  wrapper = mount(MarkdownBlock, { props: { source: '说明\n\n<img src=x onerror="alert(1)">\n\n<script>steal()</script>' } })
  await flushPromises()
  expect(wrapper.find('img').exists()).toBe(false)
  expect(wrapper.find('script').exists()).toBe(false)
  expect(wrapper.html()).toContain('&lt;img')
})

it('marks matched terms inside rendered content and repaints on change', async () => {
  wrapper = mount(MarkdownBlock, { props: { source: 'Redis 缓存穿透使用布隆过滤器', terms: ['布隆过滤器', 'redis'] } })
  await flushPromises()
  expect(wrapper.findAll('mark').map((item) => item.text())).toEqual(['Redis', '布隆过滤器'])
  await wrapper.setProps({ source: '布隆过滤器的容量估算', terms: ['容量估算'] })
  await flushPromises()
  expect(wrapper.findAll('mark').map((item) => item.text())).toEqual(['容量估算'])
})

it('treats regexp characters in a term as literal text', async () => {
  wrapper = mount(MarkdownBlock, { props: { source: '配置 key(默认) 即可', terms: ['key(默认)'] } })
  await flushPromises()
  expect(wrapper.get('mark').text()).toBe('key(默认)')
})

it('renders inline mode without a wrapping paragraph', async () => {
  wrapper = mount(MarkdownBlock, { props: { source: '支持 **加粗** 的行内说明', inline: true } })
  await flushPromises()
  expect(wrapper.find('p').exists()).toBe(false)
  expect(wrapper.get('strong').text()).toBe('加粗')
})
