<script setup lang="ts">
defineProps<{ schema: any }>()
</script>
<template>
  <div class="schema-view">
    <p><strong>Root</strong> <code>{{ schema.root }}</code></p>
    <details v-for="(type, name) in schema.types" :key="name" :open="name === schema.root">
      <summary><code>{{ name }}</code> · {{ type.kind }}</summary>
      <table v-if="type.kind === 'message'"><thead><tr><th>ID</th><th>Field</th><th>Type</th><th>Cardinality</th><th>Wire</th></tr></thead><tbody><tr v-for="field in type.fields" :key="field.id"><td data-label="ID">{{ field.id }}</td><td data-label="Field">{{ field.name }}</td><td data-label="Type"><code>{{ field.type }}{{ field.dimensions.map((n: number) => `[${n}]`).join('') }}</code></td><td data-label="Cardinality">{{ field.cardinality }}</td><td data-label="Wire"><code>{{ JSON.stringify(field.wire) }}</code></td></tr></tbody></table>
      <p v-else>{{ type.values.map((v: any) => `${v.name} = ${v.value}`).join(', ') }}</p>
    </details>
  </div>
</template>
