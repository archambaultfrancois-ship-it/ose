// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest'
import { useWorkspacePanels } from '../src/workspace-panels'

describe('workspace resizing', () => {
  it('supports bounded keyboard resizing on either side and below the canvas', () => {
    const panels = useWorkspacePanels()
    panels.resizeKey(new KeyboardEvent('keydown', { key: 'ArrowRight' }), 'explorer')
    expect(panels.widths.explorer).toBe(255)
    panels.resizeKey(new KeyboardEvent('keydown', { key: 'ArrowLeft' }), 'inspector')
    expect(panels.widths.inspector).toBe(350)
    panels.resizeKey(new KeyboardEvent('keydown', { key: 'ArrowUp' }), 'diagnostics')
    expect(panels.diagnosticsHeight.value).toBe(200)
    for (let i = 0; i < 30; i++) panels.resizeKey(new KeyboardEvent('keydown', { key: 'ArrowUp' }), 'diagnostics')
    expect(panels.diagnosticsHeight.value).toBe(500)
    panels.resizeKey(new KeyboardEvent('keydown', { key: 'Home' }), 'inspector')
    expect(panels.widths.inspector).toBe(260)
  })

  it('captures dragging and releases listeners and body styles when cancelled', () => {
    const panels = useWorkspacePanels(), handle = document.createElement('div')
    handle.setPointerCapture = vi.fn(); handle.hasPointerCapture = vi.fn(() => true); handle.releasePointerCapture = vi.fn()
    const event = new PointerEvent('pointerdown', { button: 0, pointerId: 1, clientX: 100 })
    Object.defineProperty(event, 'currentTarget', { value: handle })
    const cursor = document.body.style.cursor, userSelect = document.body.style.userSelect
    panels.resize(event, 'inspector')
    handle.dispatchEvent(new PointerEvent('pointermove', { clientX: 40, pointerId: 1 }))
    expect(panels.widths.inspector).toBe(390)
    handle.dispatchEvent(new PointerEvent('pointercancel', { pointerId: 1 }))
    expect(handle.releasePointerCapture).toHaveBeenCalledWith(1)
    expect(document.body.style.cursor).toBe(cursor)
    expect(document.body.style.userSelect).toBe(userSelect)
    handle.dispatchEvent(new PointerEvent('pointermove', { clientX: 10, pointerId: 1 }))
    expect(panels.widths.inspector).toBe(390)
  })
})
