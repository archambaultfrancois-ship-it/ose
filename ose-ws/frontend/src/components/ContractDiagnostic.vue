<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'

const props = defineProps<{ subscription: string; epoch?: string | null }>()
const report = ref<any>(null), error = ref(''), loading = ref(false), differencesOnly = ref(false)
const fields = computed(() => (report.value?.fields || []).filter((field: any) => !differencesOnly.value || field.status !== 'matching'))
let request = 0
function reset() { request++; report.value = null; error.value = ''; loading.value = false; differencesOnly.value = false }
async function load() {
  const current = ++request
  report.value = null; error.value = ''; loading.value = true
  try {
    const result = await api(`/flow/diagnostic?subscription=${encodeURIComponent(props.subscription)}&epoch=${encodeURIComponent(props.epoch || '')}`)
    if (current === request) report.value = result
  } catch (e: any) {
    if (current === request) error.value = e.message
  } finally {
    if (current === request) loading.value = false
  }
}
function type(field: any) { return field.type + field.dimensions.map((n: number) => `[${n}]`).join('') }
watch(() => [props.subscription, props.epoch], reset)
onBeforeUnmount(() => { request++ })
</script>
<template>
  <section class="contract-diagnostic" aria-label="Link contract diagnostic" :aria-busy="loading">
    <div class="section-title"><h4>Contract diagnostic</h4><button class="quiet" :disabled="loading || !epoch" @click="load">{{ loading ? 'Checking contracts…' : report ? 'Check again' : error ? 'Retry diagnostic' : 'Diagnose compatibility' }}</button></div>
    <p class="muted small">Source → destination · fields matched by numeric ID · required destination fields must be published</p>
    <p v-if="error" class="notice warning" role="alert">{{ error }}</p>
    <template v-if="report">
      <p class="diagnostic-result" :class="{ incompatible: !report.compatible }" role="status"><strong>{{ report.compatible ? 'Compatible' : 'Incompatible' }}</strong><span v-if="report.identical"> · identical catalogues</span></p>
      <div class="contract-endpoints"><p><strong>Source</strong><code>{{ report.source.endpoint }}</code><span>{{ report.source.root }}</span></p><p><strong>Destination</strong><code>{{ report.destination.endpoint }}</code><span>{{ report.destination.root }}</span></p></div>
      <p v-if="!report.compatible" class="notice error">{{ report.diagnostic.code === 'ROOT_MISMATCH' ? 'The source and destination root types differ.' : 'SDL rejects this source → destination contract.' }} <code>{{ report.diagnostic.code }}</code><span v-if="report.diagnostic.path"> at <code>{{ report.diagnostic.path }}</code></span></p>
      <template v-if="report.fields.length">
        <label class="inline diagnostic-filter"><input type="checkbox" v-model="differencesOnly" /> Differences only</label>
        <div class="table-scroll"><table class="contract-fields"><thead><tr><th>Field / ID</th><th>Source</th><th>Destination</th><th>Diagnostic</th></tr></thead><tbody>
          <tr v-for="field in fields" :key="field.path + ':' + field.field_id" :data-status="field.status">
            <td><code>{{ field.path }}</code><small>ID {{ field.field_id }}</small><small v-if="field.destination_path !== field.path">→ {{ field.destination_path }}</small></td>
            <td><template v-if="field.source"><code>{{ field.source.name }}</code><small>{{ type(field.source) }} · {{ field.source.cardinality }}</small><small v-if="Object.keys(field.source.wire).length"><code>{{ JSON.stringify(field.source.wire) }}</code></small></template><span v-else class="muted">Absent</span></td>
            <td><template v-if="field.destination"><code>{{ field.destination.name }}</code><small>{{ type(field.destination) }} · {{ field.destination.cardinality }}</small><small v-if="Object.keys(field.destination.wire).length"><code>{{ JSON.stringify(field.destination.wire) }}</code></small></template><span v-else class="muted">Not declared</span></td>
            <td><strong>{{ field.status === 'incompatible' ? 'Incompatible' : field.status === 'different' ? 'Compatible difference' : 'Matching' }}</strong><p v-for="finding in field.findings" :key="finding.code" :class="{ 'field-error': finding.severity === 'error' }">{{ finding.message }}</p></td>
          </tr>
          <tr v-if="!fields.length"><td colspan="4" class="empty">No differences in the displayed fields.</td></tr>
        </tbody></table></div>
      </template>
      <p v-else-if="report.compatible" class="muted small">No fields to compare in this root.</p>
      <p v-if="report.truncated" class="notice warning">Field details are limited to 256 rows and 16 nested levels. The compatibility result still checks the complete root contract.</p>
      <p class="muted small">Checks the compiled contracts using SDL's source → destination rules. This does not inspect message values or confirm delivery.</p>
    </template>
  </section>
</template>
<style scoped>
.contract-diagnostic{margin-top:18px;padding-top:16px;border-top:1px solid #cfe1df;min-width:0}.contract-diagnostic h4{margin:0;font-size:14px}.contract-diagnostic .section-title{gap:12px;flex-wrap:wrap}.contract-diagnostic p{margin:8px 0}.contract-diagnostic button{min-height:44px}.diagnostic-result{color:#187966}.diagnostic-result.incompatible,.field-error{color:#a3324b}.contract-endpoints{display:flex;gap:24px;flex-wrap:wrap}.contract-endpoints p{min-width:0;display:grid;gap:4px;font-size:12px;overflow-wrap:anywhere}.contract-endpoints strong{font-size:11px;color:#708693}.diagnostic-filter{margin:12px 0}.contract-fields{font-size:12px}.contract-fields td{vertical-align:top;overflow-wrap:anywhere;min-width:140px}.contract-fields td:last-child{min-width:220px}.contract-fields small{display:block;margin-top:4px;color:#708693}.contract-fields p{margin:6px 0;line-height:1.5}.contract-fields tr[data-status=incompatible]{background:#fff4f5}.contract-fields tr[data-status=different]{background:#f6fafb}
</style>
