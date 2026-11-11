import { reactive, ref } from 'vue'

type Panel = 'explorer' | 'inspector' | 'diagnostics'
const limits = { explorer: [180, 420], inspector: [260, 560], diagnostics: [110, 500] } as const

export function useWorkspacePanels() {
  const explorerOpen = ref(true), inspectorOpen = ref(true), diagnosticsOpen = ref(false)
  const widths = reactive({ explorer: 235, inspector: 330 })
  const diagnosticsHeight = ref(180)
  let cleanup: (() => void) | undefined
  function size(panel: Panel) { return panel === 'diagnostics' ? diagnosticsHeight.value : widths[panel] }
  function setSize(panel: Panel, value: number) {
    const [min, max] = limits[panel]
    value = Math.max(min, Math.min(max, value))
    if (panel === 'diagnostics') diagnosticsHeight.value = value
    else widths[panel] = value
  }
  function stopResize() { cleanup?.(); cleanup = undefined }
  function resize(event: PointerEvent, panel: Panel) {
    if (event.button !== 0) return
    event.preventDefault(); stopResize()
    const handle = event.currentTarget as HTMLElement
    const vertical = panel === 'diagnostics', direction = panel === 'explorer' ? 1 : -1
    const start = vertical ? event.clientY : event.clientX, initial = size(panel)
    handle.setPointerCapture(event.pointerId)
    const move = (next: PointerEvent) => setSize(panel, initial + direction * ((vertical ? next.clientY : next.clientX) - start))
    const cursor = document.body.style.cursor, selection = document.body.style.userSelect
    document.body.style.cursor = vertical ? 'row-resize' : 'col-resize'
    document.body.style.userSelect = 'none'
    handle.addEventListener('pointermove', move)
    handle.addEventListener('pointerup', stopResize)
    handle.addEventListener('pointercancel', stopResize)
    handle.addEventListener('lostpointercapture', stopResize)
    cleanup = () => {
      handle.removeEventListener('pointermove', move)
      handle.removeEventListener('pointerup', stopResize)
      handle.removeEventListener('pointercancel', stopResize)
      handle.removeEventListener('lostpointercapture', stopResize)
      if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId)
      document.body.style.cursor = cursor; document.body.style.userSelect = selection
    }
  }
  function resizeKey(event: KeyboardEvent, panel: Panel) {
    const keys = panel === 'diagnostics' ? ['ArrowDown', 'ArrowUp'] : panel === 'explorer' ? ['ArrowLeft', 'ArrowRight'] : ['ArrowRight', 'ArrowLeft']
    if (event.key === 'Home' || event.key === 'End') setSize(panel, limits[panel][event.key === 'Home' ? 0 : 1])
    else if (keys.includes(event.key)) setSize(panel, size(panel) + (event.key === keys[0] ? -20 : 20))
    else return
    event.preventDefault()
  }
  return { explorerOpen, inspectorOpen, diagnosticsOpen, widths, diagnosticsHeight, resize, resizeKey, stopResize }
}
