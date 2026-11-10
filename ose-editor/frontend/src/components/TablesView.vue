<script setup lang="ts">
import { computed, ref } from 'vue'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Select from 'primevue/select'
import InputText from 'primevue/inputtext'
import { useProject } from '../store'
import { activationLinks, inputLabel } from '../execution'
const store = useProject(), category = ref('components'), search = ref('')
const model = computed(() => store.project.documents[store.project.model].value)
const rows = computed(() => {
  let result: any[] = []
  if (model.value?.version !== 2) return result
  if (category.value === 'components') result = Object.entries(model.value.components || {}).map(([name, value]: any) => ({ name, detail: 'Per-input callbacks', type: activationLinks(value).length + ' activation link(s)', file: store.project.model, path: ['components', name] }))
  if (category.value === 'subscriptions') result = (model.value.subscriptions || []).map((sub: any, i: number) => ({ name: sub.id, detail: (sub.from || sub.from_topic) + ' → ' + sub.to, type: sub.qos?.buffer || 'fifo (default)', file: store.project.model, path: ['subscriptions', String(i)] }))
  if (category.value === 'ports') result = Object.entries(model.value.components || {}).flatMap(([component, value]: any) => ['inputs', 'outputs'].flatMap(direction => Object.entries(value[direction] || {}).map(([name, port]: any) => ({ name: component + '.' + name, detail: direction === 'inputs' ? inputLabel(port) : direction, type: port.root || 'Timer · no message', file: store.project.model, path: ['components', component, direction, name] }))))
  if (category.value === 'activations') result = Object.entries(model.value.components || {}).flatMap(([name, component]) => activationLinks(component).map(link => ({ name: name + '.' + link.input + ' → ' + link.output, detail: link.event, type: 'Allowed output activation', file: store.project.model, path: ['components', name, 'inputs', link.input] })))
  if (category.value === 'parameters') result = Object.entries(model.value.shared_parameters || {}).map(([name, value]: any) => ({ name, detail: value.schema || model.value.schemas, type: value.root, file: store.project.model, path: ['shared_parameters', name] }))
  if (category.value === 'deployments') result = store.project.deployments.flatMap((file: string) => Object.entries(store.project.documents[file].value?.components || {}).map(([name, value]: any) => ({ name, detail: file, type: value.container ? value.container.language + ' · ' + value.container.handler : value.command?.join(' '), file, path: ['components', name] })))
  return result.filter(row => [row.name, row.detail, row.type].some(value => String(value).toLowerCase().includes(search.value.toLowerCase())))
})
</script>
<template><div class="table-workspace"><div class="graph-toolbar"><Select v-model="category" :options="['components', 'subscriptions', 'ports', 'activations', 'parameters', 'deployments']" aria-label="Table category" /><InputText v-model="search" placeholder="Filter rows…" aria-label="Filter configuration rows" /></div><DataTable :value="rows" paginator :rows="30" striped-rows selection-mode="single" @row-select="event => store.select(event.data.file, event.data.path)"><Column field="name" header="Name" sortable /><Column field="detail" header="Context" sortable /><Column field="type" header="Configuration" sortable /></DataTable></div></template>
