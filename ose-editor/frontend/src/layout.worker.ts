import ELK from 'elkjs/lib/elk.bundled.js'
const elk = new ELK()
self.onmessage = async (event: MessageEvent) => {
  try {
    const graph = await elk.layout(event.data.graph)
    self.postMessage({ id: event.data.id, graph })
  } catch (error) { self.postMessage({ id: event.data.id, error: (error as Error).message }) }
}
