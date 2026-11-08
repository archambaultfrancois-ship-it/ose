<script setup lang="ts">
import { computed } from 'vue'
import { useProject } from '../store'
import { atPath } from '../hints'
const store = useProject()
const path = computed(() => store.selection?.path || [])
const model = computed(() => store.project?.documents[store.project.model]?.value)
const validation = computed(() => store.project?.validation?.revision === store.project?.revision ? store.project.validation : null)
const object = computed(() => store.selection ? atPath(store.project.documents[store.selection.file]?.value, path.value) : null)
const contract = computed(() => path.value[0] === 'subscriptions' && path.value.length === 2 ? validation.value?.contracts?.[object.value?.id] : null)
const initial = computed(() => path.value[0] === 'shared_parameters' ? validation.value?.catalogues?.[model.value.shared_parameters[path.value[1]]?.schema || model.value.schemas]?.initial?.[path.value[1]] : null)
const schema = computed(() => object.value?.root ? validation.value?.catalogues?.[object.value.schema || model.value.schemas] : null)
const fields = computed(() => schema.value?.messages?.[object.value?.root] || [])
const qos = computed(() => path.value[0] === 'subscriptions' && path.value.length === 2 ? { buffer: object.value.qos?.buffer || 'fifo', capacity: object.value.qos?.capacity ?? (object.value.qos?.buffer === 'latest' ? 1 : 1024), overflow: object.value.qos?.overflow || (object.value.qos?.buffer === 'latest' ? 'drop_oldest' : 'reject_new'), ack: object.value.qos?.ack || 'none' } : null)
</script>
<template><section v-if="object?.root || qos || initial" class="contract-view"><h3>Effective contract</h3><p v-if="!validation" class="empty-state">Validate this revision to resolve schemas and check directional compatibility.</p><template v-if="contract"><p :class="contract.compatible ? 'compatible' : 'error'">{{ contract.compatible ? '✓ Compatible' : 'Incompatible: ' + contract.code }}</p><small>{{ contract.direction }} · {{ contract.root }}</small><p v-if="contract.path">{{ contract.path }} · {{ contract.message }}</p></template><details v-if="qos"><summary>Effective QoS defaults</summary><pre>{{ JSON.stringify(qos, null, 2) }}</pre></details><details v-if="fields.length"><summary>{{ object.root }} · {{ fields.length }} fields</summary><pre>{{ JSON.stringify(fields, null, 2) }}</pre><small>Catalogue {{ schema.identity }}</small></details><details v-if="initial"><summary>Initial value after SDL encoding</summary><pre>{{ JSON.stringify(initial, null, 2) }}</pre></details></section></template>
