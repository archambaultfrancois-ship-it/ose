<script setup lang="ts">
import { computed, ref } from 'vue'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Select from 'primevue/select'
import InputText from 'primevue/inputtext'
import { useProject } from '../store'
import { tableRows, type TableRow } from '../table-rows'
const store = useProject(), category = ref('all'), search = ref('')
const rows = computed(() => tableRows(store.project, category.value).filter(row =>
  [row.name, row.detail, row.type].some(value => String(value).toLowerCase().includes(search.value.toLowerCase()))))
function selectRow(row: TableRow) {
  if (!row.file) return
  if (row.file !== store.project.model && store.project.model_contexts?.[row.file]) {
    store.selection = { file: row.file, path: row.path }
  } else store.select(row.file, row.path)
}
</script>
<template><div class="table-workspace"><div class="graph-toolbar"><Select v-model="category" :options="['all', 'components', 'subsystems', 'groups', 'external_topics', 'subscriptions', 'ports', 'activations', 'parameters', 'deployments']" aria-label="Table category" /><InputText v-model="search" placeholder="Filter rows…" aria-label="Filter configuration rows" /></div><DataTable :value="rows" paginator :rows="30" striped-rows selection-mode="single" @row-select="event => selectRow(event.data)"><Column field="category" header="Kind" sortable /><Column field="name" header="Name" sortable /><Column field="detail" header="Context" sortable /><Column field="type" header="Configuration" sortable /></DataTable></div></template>
