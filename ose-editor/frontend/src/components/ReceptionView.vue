<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import Button from 'primevue/button'
import { useProject } from '../store'
import { scalarText } from '../hints'
import { receptionFor } from '../reception'
const store = useProject()
const model = computed(() => store.project?.documents?.[store.project.model]?.value)
const target = computed(() => {
  const selection = store.selection, path = selection?.path || []
  if (selection?.file !== store.project?.model) return ''
  if (path[0] === 'components' && path[2] === 'inputs' && path.length === 4) return path[1] + '.' + path[3]
  if (path[0] === 'subscriptions' && path.length >= 2) return model.value?.subscriptions?.[Number(path[1])]?.to || ''
  return ''
})
const reception = computed(() => receptionFor(model.value, target.value))
const draft = ref({ buffer: 'fifo', capacity: '1024', overflow: 'reject_new', wait_timeout_ms: '100' })
watch(() => [target.value, store.project?.revision], () => {
  const policy = reception.value.policy
  draft.value = { buffer: policy.buffer, capacity: scalarText(policy.capacity), overflow: policy.overflow, wait_timeout_ms: scalarText(policy.wait_timeout_ms) }
}, { immediate: true })
function modeChanged() {
  const latest = draft.value.buffer === 'latest'
  draft.value.capacity = latest ? '1' : '1024'
  draft.value.overflow = latest ? 'drop_oldest' : 'reject_new'
}
function apply() {
  const capacity = Number(draft.value.capacity), wait_timeout_ms = Number(draft.value.wait_timeout_ms)
  if (![capacity, wait_timeout_ms].every(value => Number.isSafeInteger(value) && value > 0)) {
    store.error = 'Reception capacity and wait timeout must be positive safe integers.'; return
  }
  store.command({ action: 'set_reception', to: target.value, reception: { ...draft.value, capacity, wait_timeout_ms } })
}
</script>
<template>
  <section v-if="target" class="reception-view" aria-label="Input reception policy">
    <h3>Input reception</h3><code>{{ target }}</code>
    <p>{{ reception.subscriptions.length }} incoming link(s) · one shared {{ reception.policy.buffer }} buffer</p>
    <p v-if="reception.conflict" class="error">Reception settings conflict. Apply a common policy to repair all incoming links.</p>
    <ul><li v-for="sub in reception.subscriptions" :key="sub.id"><button @click="store.select(store.project.model, ['subscriptions', String(sub.index)])">{{ sub.id }} · {{ sub.from || sub.from_topic }}</button></li></ul>
    <template v-if="reception.subscriptions.length">
      <label>Buffer<select v-model="draft.buffer" aria-label="Shared reception buffer" @change="modeChanged"><option>fifo</option><option>latest</option></select></label>
      <label>Shared capacity<input v-model="draft.capacity" aria-label="Shared reception capacity" type="number" min="1" :disabled="draft.buffer === 'latest'"></label>
      <label>Overflow<select v-model="draft.overflow" aria-label="Shared reception overflow" :disabled="draft.buffer === 'latest'"><option>reject_new</option><option>drop_oldest</option><option>wait</option></select></label>
      <label>Wait timeout (ms)<input v-model="draft.wait_timeout_ms" aria-label="Shared reception wait timeout" type="number" min="1"></label>
      <p>Applies to every incoming link. FIFO merges admission order; latest keeps the last valid admission. ACK, retry and transport remain per link.</p>
      <Button label="Apply shared reception" size="small" :disabled="store.busy || store.project.documents[store.project.model].stale" @click="apply" />
    </template>
  </section>
</template>
<style scoped>
.reception-view{margin-top:20px;padding-top:16px;border-top:1px solid #e3eaf4;display:grid;gap:9px}.reception-view p,.reception-view li{font-size:11px;line-height:1.5}.reception-view label{display:grid;gap:4px;font-size:11px}.reception-view input,.reception-view select{width:100%;padding:6px;border:1px solid #dbe3ef;border-radius:4px}.reception-view ul{padding-left:16px}.reception-view li button{background:none;border:0;text-align:left;color:#3159c7;cursor:pointer;font-size:11px}
</style>
