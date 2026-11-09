import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

// Exercise the real projection modules without adding a TS test runtime. The
// filter imports relationFamily from graphMeta, so that module is compiled to a
// data URL first and the specifier rewritten.
const compile = async (path) => {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  return ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText
}
const toUrl = (code) => 'data:text/javascript;base64,' + Buffer.from(code).toString('base64')

const metaUrl = toUrl(await compile('../src/components/graph/graphMeta.ts'))
const filterUrl = toUrl(
  (await compile('../src/components/graph/graphFilter.ts'))
    .replace(/from ['"]\.\/graphMeta['"]/g, `from '${metaUrl}'`),
)
const { filterGraph, defaultFilters } = await import(filterUrl)
const relationsUrl = toUrl(await compile('../src/components/graph/graphRelations.ts'))
const { groupConnections } = await import(relationsUrl)

const payload = {
  nodes: [
    { id: 'file', community: 0, kind: 'file', label: 'a.py', degree: 3 },
    { id: 'fn', community: 0, kind: 'function', label: 'fn', degree: 3, path: 'a.py', start_line: 2, end_line: 4 },
    { id: 'why', community: 0, kind: 'rationale', label: 'why', degree: 1 },
    { id: 'ext', community: 0, kind: 'external', label: 'typing', degree: 1, external: true },
    { id: 'other', community: 1, kind: 'function', label: 'other', degree: 1 },
  ],
  edges: [
    { source: 'file', target: 'fn', relation: 'contains', confidence: 'EXTRACTED' },
    { source: 'fn', target: 'other', relation: 'calls', confidence: 'EXTRACTED' },
    { source: 'why', target: 'fn', relation: 'rationale_for', confidence: 'EXTRACTED' },
    { source: 'file', target: 'ext', relation: 'uses', confidence: 'EXTRACTED' },
  ],
  communities: [{ id: 0, label: 'One', size: 4 }, { id: 1, label: 'Two', size: 1 }],
}

const hiddenIds = (graph) => graph.nodes.filter(node => node.hidden).map(node => node.id)

test('default filters hide commentary but keep dependencies', () => {
  const graph = filterGraph(payload, defaultFilters())
  assert.deepEqual(hiddenIds(graph), ['why'])
  assert.equal(graph.edges.find(edge => edge.relation === 'rationale_for').hidden, true)
  assert.equal(graph.edges.find(edge => edge.relation === 'uses').hidden, false)
})

test('turning a family off hides its edges and annotation nodes', () => {
  const filters = defaultFilters()
  filters.families.delete('dependency')
  const graph = filterGraph(payload, filters)
  assert.deepEqual(hiddenIds(graph).sort(), ['ext', 'why'])
  assert.equal(graph.edges.find(edge => edge.relation === 'uses').hidden, true)
})

test('hiding a community hides both its nodes and incident edges', () => {
  const filters = defaultFilters()
  filters.hiddenCommunities.add(1)
  const graph = filterGraph(payload, filters)
  assert.equal(graph.nodes.find(node => node.id === 'other').hidden, true)
  assert.equal(graph.edges.find(edge => edge.relation === 'calls').hidden, true)
  assert.equal(graph.edges.find(edge => edge.relation === 'contains').hidden, false)
})

test('inspector splits callers from callees and groups scaffolding', () => {
  const byId = new Map(payload.nodes.map(node => [node.id, node]))
  const groups = groupConnections('fn', payload.edges, byId, 'code')
  const byKey = Object.fromEntries(groups.map(group => [group.key, group.items]))
  assert.deepEqual((byKey.callers ?? []).map(item => item.other.label), [])
  assert.deepEqual(byKey.calls.map(item => item.other.label), ['other'])
  assert.deepEqual(byKey.structure.map(item => item.other.label), ['a.py'])
  assert.deepEqual(byKey.meaning.map(item => item.other.label), ['why'])
})

test('notes use one relation bucket', () => {
  const byId = new Map([
    ['a', { id: 'a', label: 'A', kind: 'note' }],
    ['b', { id: 'b', label: 'B', kind: 'note' }],
  ])
  const groups = groupConnections('a', [
    { source: 'a', target: 'b', relation: 'same_folder', confidence: 'EXTRACTED' },
  ], byId, 'notes')
  assert.deepEqual(groups.map(group => group.key), ['related'])
  assert.equal(groups[0].items[0].other.label, 'B')
})
