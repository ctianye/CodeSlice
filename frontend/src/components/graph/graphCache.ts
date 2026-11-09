/**
 * In-memory graph payload cache (stale-while-revalidate).
 *
 * Switching between 代码图谱 and 笔记图谱 remounts GraphView (App keys each tab by
 * scope), so without a cache every scope switch blanks the canvas and re-runs a
 * multi-second projection over the whole project. The cache keeps the last
 * payload per project+scope and paints it immediately, then refreshes in the
 * background. It is deliberately memory-only: a reload should re-read the
 * backend, because the index may have moved on since the tab was last open.
 */

import type { GraphPayload } from '../../types'

export interface CachedGraph {
  payload: GraphPayload
  /** `performance.now()`-based epoch millis of the write. */
  at: number
}

const store = new Map<string, CachedGraph>()

/** A payload this old is repainted but always revalidated before it is trusted. */
export const STALE_AFTER_MS = 15_000

export function graphCacheKey(projectId: number | null, scope: 'code' | 'notes'): string {
  return `${projectId ?? 'none'}:${scope}`
}

export function readGraphCache(key: string): CachedGraph | null {
  return store.get(key) ?? null
}

export function writeGraphCache(key: string, payload: GraphPayload): void {
  store.set(key, { payload, at: Date.now() })
}

/**
 * Drop one entry, or the whole cache when no key is given.
 *
 * Called after a rebuild: the rebuilt payload is what the next read must see,
 * and a stale entry would win the race for the tab that asked for it.
 */
export function clearGraphCache(key?: string): void {
  if (key === undefined) store.clear()
  else store.delete(key)
}

export function isFresh(entry: CachedGraph): boolean {
  return Date.now() - entry.at < STALE_AFTER_MS
}
