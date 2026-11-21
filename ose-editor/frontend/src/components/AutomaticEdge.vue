<script setup lang="ts">
import { computed } from 'vue'
import { BaseEdge, getBezierPath, getSmoothStepPath, getStraightPath, type EdgeProps } from '@vue-flow/core'
import { automaticRoute, roundedRoute, type RoutingNode } from '../automatic-routing'
const props = withDefaults(defineProps<EdgeProps & { nodes: RoutingNode[] }>(), { labelShowBg: true })
const route = computed(() => {
  const source = { x: props.sourceX, y: props.sourceY }, target = { x: props.targetX, y: props.targetY }
  if (props.source === props.target || target.x <= source.x) {
    const points = automaticRoute(source, target, props.source, props.target, props.nodes)
    if (points) return roundedRoute(points, props.type === 'straight' ? 0 : props.type === 'smoothstep' ? 8 : 20)
  }
  const options = { sourceX: props.sourceX, sourceY: props.sourceY, targetX: props.targetX, targetY: props.targetY,
    sourcePosition: props.sourcePosition, targetPosition: props.targetPosition }
  const [path, x, y] = (props.type === 'straight' ? getStraightPath : props.type === 'smoothstep' ? getSmoothStepPath : getBezierPath)(options)
  return { path, x, y }
})
</script>
<template>
  <BaseEdge :id="id" :path="route.path" :label="label" :label-x="route.x" :label-y="route.y" :label-style="labelStyle" :label-show-bg="labelShowBg" :label-bg-style="labelBgStyle" :label-bg-padding="labelBgPadding" :label-bg-border-radius="labelBgBorderRadius" :marker-start="markerStart" :marker-end="markerEnd" :style="style" :interaction-width="20" />
</template>
