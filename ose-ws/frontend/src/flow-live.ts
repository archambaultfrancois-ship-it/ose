import { reactive } from 'vue'

// One selected link per browser; transport is the app's existing WebSocket.
export const flowStream = reactive({
  watchId: null as string | null,
  intervalMs: 500,
  connected: false,
  update: null as any,
})
