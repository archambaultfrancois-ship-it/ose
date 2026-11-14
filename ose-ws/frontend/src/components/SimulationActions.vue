<script setup lang="ts">
import { computed, ref } from 'vue'
import { can, command } from '../api'

const props = defineProps<{ execution: any; connected: boolean }>()
const emit = defineEmits<{ refresh: [] }>()
const pending = ref(0), error = ref('')
const allowed = computed(() => can('simulation.control') && props.connected)
const running = computed(() => props.execution?.state === 'running')
const ready = computed(() => allowed.value && !pending.value && props.execution?.state === 'paused' && !props.execution?.active_operation)
async function send(action: string, extra: any = {}) {
  pending.value++; error.value = ''
  try { await command('/simulation/commands', 'POST', { epoch: props.execution.epoch, action, ...extra }) }
  catch (e: any) { error.value = e.message }
  finally { pending.value--; emit('refresh') }
}
</script>

<template>
  <div class="simulation-actions">
    <div class="buttons" role="group" aria-label="Simulation actions">
      <button class="quiet" :title="running ? 'Pause simulation' : 'Resume simulation'" :aria-label="running ? 'Pause simulation' : 'Resume simulation'" :disabled="running ? !allowed : !ready" @click="send(running ? 'pause' : 'resume')">{{ running ? 'Ⅱ' : '▶' }}</button>
      <button class="quiet" title="Step one future instant" aria-label="Step one future instant" :disabled="!ready" @click="send('step_instants', { count: 1 })">▷│</button>
      <button v-if="execution?.microsteps" class="quiet" title="Step one microstep" aria-label="Step one microstep" :disabled="!ready" @click="send('step_microsteps', { count: 1 })">·▷</button>
      <button class="quiet" title="Stop simulation session" aria-label="Stop simulation session" :disabled="!allowed || !['paused', 'running'].includes(execution?.state)" @click="send('stop')">■</button>
      <button v-if="execution?.capabilities?.reset" class="quiet" title="Reset session to initial state" aria-label="Reset session to initial state" :disabled="!allowed || !!pending || !!execution?.active_operation || !['paused', 'finished', 'failed'].includes(execution?.state)" @click="send('reset')">↺</button>
    </div>
    <span v-if="error" class="error" role="alert">{{ error }}</span>
  </div>
</template>

<style scoped>
.simulation-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.buttons{display:flex;gap:4px}.buttons button{display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;padding:0;font-size:17px;line-height:1}.error{font-size:12px}
</style>
