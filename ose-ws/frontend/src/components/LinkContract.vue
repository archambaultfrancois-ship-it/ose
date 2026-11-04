<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'
import SchemaView from './SchemaView.vue'
import FlowLive from './FlowLive.vue'

const props = defineProps<{ subscription: string; epoch?: string | null; active?: boolean; connected?: boolean; frozen?: boolean }>()
const schema = ref<any>(null), error = ref(''), loading = ref(false)
let request = 0
async function load() {
  const current = ++request
  schema.value = null; error.value = ''; loading.value = false
  if (!props.epoch) { error.value = 'Contract unavailable until application status is available.'; return }
  loading.value = true
  try {
    const result = await api(`/flow/schema?subscription=${encodeURIComponent(props.subscription)}&epoch=${encodeURIComponent(props.epoch)}`)
    if (current === request) schema.value = result
  } catch (e: any) {
    if (current === request) error.value = e.message
  } finally {
    if (current === request) loading.value = false
  }
}
watch(() => [props.subscription, props.epoch], load, { immediate: true })
onBeforeUnmount(() => { request++ })
</script>
<template>
  <section class="link-contract" aria-label="Link SDL contract" :aria-busy="loading">
    <h4>SDL contract</h4>
    <p class="muted small">Source port contract</p>
    <p v-if="loading" role="status" class="muted">Loading SDL contract…</p>
    <div v-else-if="error" class="notice warning" role="alert"><p>{{ error }}</p><button class="quiet" @click="load">Retry contract</button></div>
    <template v-else-if="schema"><SchemaView :schema="schema" /><FlowLive :subscription="subscription" :epoch="epoch" :active="active" :connected="connected" :frozen="frozen" /></template>
  </section>
</template>
<style scoped>
.link-contract{margin-top:18px;min-width:0}.link-contract h4{margin:0 0 5px;font-size:14px}.link-contract>p{margin:0 0 10px}.link-contract .notice{margin-top:10px}.link-contract .notice p{margin:0 0 10px}
</style>
