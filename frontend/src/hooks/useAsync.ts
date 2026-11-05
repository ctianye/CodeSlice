import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Minimal persisted state hook backed by localStorage.
 * Used for layout sizes and the last opened project so a restart feels stable.
 */
export function usePersistedState<T>(key: string, initial: T): [T, (next: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key)
      if (raw != null) return JSON.parse(raw) as T
    } catch {
      /* ignore malformed storage */
    }
    return initial
  })

  const set = useCallback(
    (next: T | ((prev: T) => T)) => {
      setValue((prev) => {
        const resolved = typeof next === 'function' ? (next as (p: T) => T)(prev) : next
        try {
          localStorage.setItem(key, JSON.stringify(resolved))
        } catch {
          /* ignore quota errors */
        }
        return resolved
      })
    },
    [key],
  )

  return [value, set]
}

/** Runs an async task on mount and whenever its dependencies change. */
export function useAsync<T>(
  task: (signal: AbortSignal) => Promise<T>,
  deps: unknown[],
  options: { enabled?: boolean } = {},
): { data: T | null; error: string | null; loading: boolean; reload: () => void } {
  const { enabled = true } = options
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(enabled)
  const [nonce, setNonce] = useState(0)
  const taskRef = useRef(task)
  taskRef.current = task

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    if (!enabled) {
      setLoading(false)
      return
    }
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    taskRef
      .current(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setData(result)
          setLoading(false)
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        setError(err instanceof Error ? err.message : String(err))
        setLoading(false)
      })
    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled, nonce])

  return { data, error, loading, reload }
}

/** Returns a stable callback ref that always sees the latest closure. */
export function useLatest<T>(value: T): { current: T } {
  const ref = useRef(value)
  ref.current = value
  return ref
}
