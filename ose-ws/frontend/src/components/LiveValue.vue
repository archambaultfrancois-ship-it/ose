<script setup lang="ts">
defineProps<{ node: any; depth?: number }>()
function text(node: any) {
  return node.kind === 'absent' ? 'Absent' : node.kind === 'truncated' ? 'Display limit reached' : String(node.value)
}
</script>
<template>
  <div class="live-value">
    <details v-if="node.children" :open="!depth">
      <summary><strong>{{ node.name }}</strong> <code>{{ node.type }}</code><span v-if="node.kind === 'array'" class="muted"> · {{ node.length }} items<span v-if="node.children.length"> · showing {{ node.offset }}–{{ node.offset + node.children.length - 1 }}</span></span><span v-if="node.truncated" class="live-truncated"> · truncated</span></summary>
      <LiveValue v-for="(child, index) in node.children" :key="child.name + index" :node="child" :depth="(depth || 0) + 1" />
    </details>
    <div v-else class="live-scalar"><span><strong>{{ node.name }}</strong><small class="muted">{{ node.type }}</small></span><code :class="{ muted: node.kind !== 'value' }">{{ text(node) }}<span v-if="node.truncated" class="live-truncated"> (truncated)</span></code></div>
  </div>
</template>
<style scoped>
.live-value{min-width:0;overflow-wrap:anywhere}.live-value .live-value{margin-left:10px;padding-left:10px;border-left:1px solid #d8e7e7}.live-value summary{padding:8px 0;line-height:1.5}.live-value summary code{font-size:10px;color:#7a909e;margin-left:6px}.live-scalar{display:flex;gap:14px;justify-content:space-between;align-items:baseline;padding:7px 0;border-bottom:1px solid #e1ebeb;font-size:12px}.live-scalar>span{min-width:0}.live-scalar small{display:block;font-size:10px;margin-top:2px}.live-scalar>code{text-align:right;white-space:pre-wrap;min-width:0;max-width:70%}.live-truncated{color:#976820;font-size:11px}
</style>
