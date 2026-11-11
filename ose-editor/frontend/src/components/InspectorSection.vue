<script setup lang="ts">
withDefaults(defineProps<{ title: string; initialOpen?: boolean }>(), { initialOpen: true })
</script>
<template>
  <details class="inspector-section" :open="initialOpen">
    <summary>{{ title }}</summary>
    <div class="inspector-section-content"><slot /></div>
  </details>
</template>
