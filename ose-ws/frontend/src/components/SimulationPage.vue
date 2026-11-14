<script setup lang="ts">
import { computed, ref } from 'vue'
import { can, command } from '../api'
import { secondsToNanoseconds, simulatedSeconds } from '../simulation'
const props = defineProps<{ execution: any; connected: boolean }>()
const emit = defineEmits<{ refresh: [] }>()
const duration = ref('0.1'), target = ref('1'), breakpoint = ref('1'), count = ref(1)
const persistentBreakpoint = ref(false)
const pending = ref(0), error = ref('')
const ready = computed(() => can('simulation.control') && props.connected && !pending.value && props.execution?.state === 'paused' && !props.execution?.active_operation)
async function send(action: string, extra: any = {}) {
  pending.value++; error.value = ''
  try {
    await command('/simulation/commands', 'POST', { epoch: props.execution.epoch, action, ...extra })
  } catch (e: any) { error.value = e.message }
  finally { pending.value--; emit('refresh') }
}
function timed(action: string, value: string) {
  try { void send(action, { time_ns: secondsToNanoseconds(value), ...(action === 'break_add' ? { persistent: persistentBreakpoint.value } : {}) }) }
  catch (e: any) { error.value = e.message }
}
</script>
<template>
  <section class="panel simulation-panel" aria-label="Simulation controls">
    <div class="section-title"><h2>Execution controls</h2><span class="badge">{{ execution?.state || 'Preparing' }}<template v-if="execution?.state === 'paused' && execution?.pause_reason"> ({{ execution.pause_reason }})</template></span></div>
    <p>Simulated time <strong>{{ simulatedSeconds(execution?.position_ns) }} s</strong> / {{ simulatedSeconds(execution?.horizon_ns) }} s<span v-if="execution?.microsteps"> · microstep {{ execution.microstep }}</span></p>
    <p v-if="execution?.stop_reason" class="muted">Stop reason: {{ execution.stop_reason }}</p>
    <p v-if="execution?.error" class="notice error" role="alert">{{ execution.error }}</p>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <p v-if="!can('simulation.control')" class="notice">Observation only. Simulation control permission is required to issue commands.</p>
    <div class="toolbar">
      <button :disabled="!ready" @click="send('resume')">Resume simulation</button>
      <button :disabled="!can('simulation.control') || !connected || execution?.state !== 'running'" @click="send('pause')">Pause simulation</button>
      <label>Future instants<input v-model.number="count" type="number" min="1" max="1000000" step="1" /></label>
      <button :disabled="!ready" @click="send('step_instants', { count })">Step instants</button>
      <button v-if="execution?.microsteps" :disabled="!ready" @click="send('step_microsteps', { count })">Step microsteps</button>
    </div>
    <div class="toolbar">
      <label>Advance duration (simulated seconds)<input v-model="duration" inputmode="decimal" /></label><button :disabled="!ready" @click="timed('step_duration', duration)">Advance by duration</button>
      <label>Target (simulated seconds)<input v-model="target" inputmode="decimal" /></label><button :disabled="!ready" @click="timed('run_until', target)">Advance to target</button>
    </div>
    <p class="muted small">Time targets are exclusive: events exactly at the target have not run. Instant stepping settles the current instant, then advances to the requested future instant before its callbacks run. Advancing backwards requires a reset.</p>
    <div class="toolbar"><label>Breakpoint (simulated seconds)<input v-model="breakpoint" inputmode="decimal" /></label><label class="persistent-breakpoint" :title="execution?.capabilities?.persistent_breakpoints ? 'Re-arm this breakpoint after every reset' : 'Restart the simulation server to enable persistent breakpoints'"><input v-model="persistentBreakpoint" type="checkbox" :disabled="!execution?.capabilities?.persistent_breakpoints" /><span>Persistent</span></label><button :disabled="!ready" @click="timed('break_add', breakpoint)">Add time breakpoint</button></div>
    <ul v-if="execution?.breakpoints_ns?.length"><li v-for="at in execution.breakpoints_ns" :key="at">{{ simulatedSeconds(at) }} s<span v-if="execution?.persistent_breakpoints_ns?.includes(at)"> · persistent<template v-if="!execution?.armed_breakpoints_ns?.includes(at)"> · waiting for reset</template></span> <button class="quiet" :disabled="!ready" @click="send('break_remove', { time_ns: at })">Remove breakpoint</button></li></ul>
    <p v-else class="muted">No time breakpoints.</p>
    <div v-if="execution?.capabilities?.reset" class="reset-session"><p>Reset recreates components, parameters and virtual time from the initial configuration. A new execution epoch separates observations and logs.</p><button :disabled="!can('simulation.control') || !connected || !!pending || !!execution?.active_operation || !['paused','finished','failed'].includes(execution?.state)" @click="send('reset')">Reset session to initial state</button></div>
    <details v-if="execution?.operations?.length"><summary>Recent simulation operations</summary><p v-for="operation in [...execution.operations].reverse()" :key="operation.id">{{ operation.action }} · {{ operation.status }} <span v-if="operation.error">· {{ operation.error }}</span></p></details>
    <p class="muted small">Closing this page leaves the simulation running. Freeze view on another page freezes that display only.</p>
  </section>
</template>
<style scoped>
.persistent-breakpoint{display:flex;flex-direction:row;align-items:center;gap:6px}.persistent-breakpoint input{width:auto}
.simulation-panel{padding:20px}.simulation-panel .toolbar{align-items:end;flex-wrap:wrap;margin:20px 0}.simulation-panel input{max-width:240px}.simulation-panel p{line-height:1.6}.reset-session{border-top:1px solid #e3ebf0;margin-top:24px;padding-top:12px}.simulation-panel details{margin-top:20px}
</style>
