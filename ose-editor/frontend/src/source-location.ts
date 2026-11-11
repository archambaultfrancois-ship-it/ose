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

export type SourceTarget = { file: string; path: string[]; request: number; sdl?: string; line?: number; column?: number }

/** Convert Python/code-point offsets into CodeMirror UTF-16 positions. */
export function codePointOffset(text: string, offset: number): number | null {
  if (!Number.isSafeInteger(offset) || offset < 0) return null
  let position = 0, count = 0
  for (const char of text) {
    if (count === offset) return position
    position += char.length; count++
  }
  return count === offset ? position : null
}

export function sourcePosition(text: string, baseline: string, format: string, target: SourceTarget, projection: any): { position: number | null; notice?: string } {
  if (format === 'json' && !target.line && !target.sdl) {
    const position = jsonItemStart(text, target.path)
    return { position, notice: position === null ? 'The selected path is absent from this source draft.' : undefined }
  }
  if (projection?.stale && !target.line && (target.sdl || target.path.length)) return { position: null, notice: 'This projection is stale. Repair the source before navigating to a declaration location.' }
  if (text !== baseline) return { position: null, notice: 'This location refers to the applied revision. The unfinished source draft is preserved; apply it and navigate again for an up-to-date location.' }
  if (target.line) {
    const lines = text.split('\n'), line = lines[target.line - 1]
    if (line === undefined || target.line < 1) return { position: null, notice: 'The reported line is outside this source.' }
    const column = codePointOffset(line, (target.column || 1) - 1)
    return { position: column === null ? null : lines.slice(0, target.line - 1).reduce((sum, line) => sum + line.length + 1, 0) + column }
  }
  let node: any
  if (target.sdl) {
    const find = (nodes: any[]): any => { for (const current of nodes) { if (current.path === target.sdl) return current; const child = find(current.children || []); if (child) return child } }
    node = find(projection?.nodes || [])
  } else {
    node = projection?.tree
    for (const segment of target.path) node = node?.children?.find((child: any) => child.name === segment)
  }
  return { position: node?.start !== undefined ? codePointOffset(text, node.start) : target.path.length || target.sdl ? null : 0 }
}
