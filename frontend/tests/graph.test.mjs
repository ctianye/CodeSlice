import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

// Exercise the actual pure projection without adding a second TS test runtime.
const source = await readFile(new URL('../src/components/graph/communityGraph.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText
const { communityGraph } = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'))
const payload = {
  nodes: [
    { id: 'a', community: 0, kind: 'function', label: 'A', degree: 2 },
    { id: 'b', community: 0, kind: 'rationale', label: 'B', degree: 1 },
    { id: 'c', community: 1, kind: 'function', label: 'C', degree: 1 },
  ],
  edges: [
    { source: 'a', target: 'b', relation: 'rationale_for', confidence: 'EXTRACTED' },
    { source: 'a', target: 'c', relation: 'calls', confidence: 'INFERRED' },
  ],
  communities: [{ id: 0, label: 'One', size: 2 }, { id: 1, label: 'Two', size: 1 }],
}
const project = (expanded = [], hidden = [], kinds = []) => communityGraph(payload, new Set(expanded), new Set(hidden), new Set(kinds))
test('collapsed communities retain cross-community confidence and hide ordinary members', () => {
  const graph = project()
  assert.deepEqual(graph.nodes.map(n => n.id), ['community:0', 'community:1'])
  assert.deepEqual(graph.edges, [{ source: 'community:0', target: 'community:1', relation: 'calls', confidence: 'INFERRED' }])
})
test('opening one cluster leaves other cluster collapsed and stationary', () => {
  const before = project()
  const after = project([0])
  assert.deepEqual(after.nodes.map(n => n.id), ['community:0', 'a', 'b', 'community:1'])
  assert.deepEqual(after.nodes.find(n => n.id === 'community:1'), before.nodes[1])
  assert(after.edges.some(e => e.source === 'a' && e.target === 'community:1'))
  assert.equal(payload.edges.length, 2, 'presentation membership edges must not mutate source evidence')
})
test('filters exclude incident links as well as members', () => {
  const graph = project([0, 1], [1], ['rationale'])
  assert.deepEqual(graph.nodes.map(n => n.id), ['community:0', 'a'])
  const ids = new Set(graph.nodes.map(n => n.id))
  assert(graph.edges.every(e => ids.has(e.source) && ids.has(e.target)))
  assert.equal(graph.nodes[0].degree, 1)
})
test('notes use their own ids and the exact same projection', () => {
  const graph = communityGraph({
    nodes: [{ id: 'note://1', kind: 'note', label: 'A', community: 0, degree: 0 }], edges: [],
    communities: [{ id: 0, label: 'Notes', size: 1 }],
  }, new Set([0]), new Set(), new Set())
  assert.deepEqual(graph.nodes.map(n => n.id), ['community:0', 'note://1'])
  assert(!graph.nodes.some(n => n.id === 'a'))
})
