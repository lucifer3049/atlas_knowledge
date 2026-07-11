import { render, screen } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import App from './App.vue'

describe('App', () => {
  it('顯示平台標題', () => {
    render(App)
    expect(screen.getByRole('heading', { name: 'AI 知識問答平台' })).toBeTruthy()
  })
})
