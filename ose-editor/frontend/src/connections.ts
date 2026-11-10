import { receptionFor } from './reception'
import { activationCycle, inputKind } from './execution'

export type Publisher = { endpoint: string; external: boolean }
export type Assessment = { status: 'compatible' | 'incompatible' | 'unknown'; message: string }
export const statusLabel = { compatible: '✓ Compatible', incompatible: '× Unavailable', unknown: '? Contract unchecked' }

export function publishers(model: any): Publisher[] {
  return [
    ...Object.entries(model?.components || {}).flatMap(([name, component]: any) =>
      Object.keys(component.outputs || {}).map(port => ({ endpoint: name + '.' + port, external: false }))),
    ...Object.keys(model?.external_topics || {}).map(endpoint => ({ endpoint, external: true })),
  ]
}

export function inputs(model: any): string[] {
  return Object.entries(model?.components || {}).flatMap(([name, component]: any) =>
    Object.entries(component.inputs || {}).filter(([, desc]) => inputKind(desc) !== 'timer').map(([port]) => name + '.' + port))
}

export function assessConnection(project: any, source: Publisher, target: string): Assessment {
  const reject = (message: string): Assessment => ({ status: 'incompatible', message })
  const document = project?.documents[project.model], model = document?.value
  if (!model || document.stale) return reject('Repair the model source before connecting ports.')
  if (model.version !== 2) return reject('Migrate the application model to v2 before connecting ports.')
  const [sourceComponent, output] = source.endpoint.split('.')
  const [targetComponent, input] = target.split('.')
  const publisher = source.external ? model.external_topics?.[source.endpoint] : model.components?.[sourceComponent]?.outputs?.[output]
  const consumer = model.components?.[targetComponent]?.inputs?.[input]
  if (!publisher || !consumer) return reject('Choose an existing output and destination input.')
  if (inputKind(consumer) === 'timer') return reject('Timer inputs cannot receive subscriptions.')
  if (!publisher.root || !consumer.root) return reject('Both ports need a logical root type.')
  if (publisher.root !== consumer.root) return reject(`Different logical roots: ${publisher.root} → ${consumer.root}.`)
  const subscription = source.external ? { from_topic: source.endpoint, to: target } : { from: source.endpoint, to: target }
  const candidate = { ...model, subscriptions: [...(model.subscriptions || []), subscription] }
  const cycle = activationCycle(candidate)
  const executionNote = cycle ? ' Requires simulated execution with --microsteps for activation cycle: ' + cycle.join(' → ') : ''
  if (receptionFor(model, target).conflict) return reject('The destination has conflicting reception settings. Repair its shared reception policy first.')
  const validation = project.validation?.revision === project.revision ? project.validation : null
  const report = validation?.connection_contracts?.find((item: any) => item.root === publisher.root &&
    item.publisher_schema === (publisher.schema || model.schemas) && item.consumer_schema === (consumer.schema || model.schemas))
  if (!report) return { status: 'unknown', message: 'Structure allows this connection. Validate the current revision to check publisher → consumer SDL compatibility.' + executionNote }
  if (!report.compatible) {
    const reasons: Record<string, string> = {
      REQUIRED_FIELD_MISSING: 'The publisher is missing a field required by the consumer',
      FIELD_TYPE_MISMATCH: `Field types differ (${report.publisher_type} → ${report.consumer_type})`,
      FIELD_DIMENSIONS_MISMATCH: 'Fixed array dimensions differ',
      FIELD_CARDINALITY_MISMATCH: 'Field cardinalities differ',
      ENUM_VALUE_UNKNOWN: `The consumer does not recognize enum value ${report.enum_value}`,
      TYPE_KIND_MISMATCH: 'The same type name describes different kinds of declaration',
      ROOT_UNKNOWN: 'The root type is missing from a schema input',
      INVALID_PUBLISHER_CATALOGUE: 'The publisher catalogue is invalid',
      INVALID_CONSUMER_CATALOGUE: 'The consumer catalogue is invalid',
    }
    return reject(`Incompatible SDL contract${report.path ? ' at ' + report.path : ''}: ${reasons[report.code] || report.message || report.code}.`)
  }
  return { status: 'compatible', message: `Compatible publisher → consumer SDL contract (${publisher.root}). The destination's shared reception settings will be inherited.` + executionNote }
}
