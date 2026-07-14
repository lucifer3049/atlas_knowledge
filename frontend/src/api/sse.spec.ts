import { describe, expect, it } from 'vitest'

import { parseFrame } from './sse'

describe('parseFrame', () => {
  it('解析 event 與 data', () => {
    expect(parseFrame('event: delta\ndata: {"text":"hi"}')).toEqual({
      event: 'delta',
      data: '{"text":"hi"}',
    })
  })

  it('忽略註解 / 心跳行', () => {
    expect(parseFrame(': ping')).toBeNull()
  })

  it('無 event 名稱回 null', () => {
    expect(parseFrame('data: {}')).toBeNull()
  })

  it('容忍 \\r\\n 行尾', () => {
    expect(parseFrame('event: done\r\ndata: {"ok":true}')).toEqual({
      event: 'done',
      data: '{"ok":true}',
    })
  })
})
