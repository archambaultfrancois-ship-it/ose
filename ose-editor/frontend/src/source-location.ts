import { jsonLanguage } from '@codemirror/lang-json'
import type { SyntaxNode } from '@lezer/common'

// Resolve against the text actually open in CodeMirror, including unapplied
// drafts. Lezer positions use the same UTF-16 offsets as the editor.
export function jsonItemStart(text: string, path: string[]): number | null {
  let node: SyntaxNode | null = jsonLanguage.parser.parse(text).topNode.firstChild
  if (!node) return null
  let start = node.from
  for (const segment of path) {
    if (node.name === 'Object') {
      const property: SyntaxNode | undefined = node.getChildren('Property').find(property => {
        const key = property.getChild('PropertyName')
        if (!key) return false
        try { return JSON.parse(text.slice(key.from, key.to)) === segment } catch { return false }
      })
      if (!property) return null
      start = property.from
      node = property.lastChild
    } else if (node.name === 'Array' && /^(0|[1-9]\d*)$/.test(segment)) {
      const index = Number(segment)
      let current = 0
      let item: SyntaxNode | null = node.firstChild
      while (item) {
        if (['Object', 'Array', 'String', 'Number', 'True', 'False', 'Null'].includes(item.name)) {
          if (current++ === index) break
        }
        item = item.nextSibling
      }
      if (!item) return null
      start = item.from
      node = item
    } else return null
    if (!node) return null
  }
  return start
}

export type SourceTarget = { file: string; path: string[]; request: number }
