// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { useWorkspacePanels } from '../src/workspace-panels'
import ToolbarMenu from '../src/components/ToolbarMenu.vue'

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

describe('graph toolbar menus', () => {
  it('keeps internal controls usable and closes on focus or pointer moves outside', async () => {
    const wrapper = mount(ToolbarMenu, { attachTo: document.body, props: { label: 'Display' },
      slots: { default: '<input aria-label="Labels" type="checkbox"><button>Style</button>' } })
    const outside = document.createElement('button'); document.body.append(outside)
    try {
      const menu = wrapper.get('details').element as HTMLDetailsElement
      menu.open = true
      wrapper.get('summary').element.focus()
      wrapper.get('input').element.focus()
      expect(menu.open).toBe(true)
      await wrapper.get('button').trigger('pointerdown')
      expect(menu.open).toBe(true)
      await wrapper.get('input').trigger('focusout', { relatedTarget: null })
      await new Promise(resolve => setTimeout(resolve, 0))
      expect(menu.open).toBe(true)
      wrapper.get('input').element.blur()
      await new Promise(resolve => setTimeout(resolve, 0))
      expect(menu.open).toBe(false)
      menu.open = true
      wrapper.get('input').element.focus()
      outside.focus()
      expect(menu.open).toBe(false)
      menu.open = true
      document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
      expect(menu.open).toBe(false)
      menu.open = true
      window.dispatchEvent(new Event('blur'))
      expect(menu.open).toBe(false)
    } finally { wrapper.unmount(); outside.remove() }
  })

  it('closes with Escape and returns focus to the menu button', async () => {
    const wrapper = mount(ToolbarMenu, { attachTo: document.body, props: { label: 'Groups' }, slots: { default: '<button>Group selection</button>' } })
    try {
      const menu = wrapper.get('details').element as HTMLDetailsElement
      menu.open = true
      wrapper.get('button').element.focus()
      await wrapper.get('button').trigger('keydown', { key: 'Escape' })
      expect(menu.open).toBe(false)
      expect(document.activeElement).toBe(wrapper.get('summary').element)
    } finally { wrapper.unmount() }
  })
})
