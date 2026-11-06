import { useCallback, useEffect, useRef, useState } from 'react'
import * as api from '../api'
import type { IndexStatus, Job } from '../types'

export interface IndexTotals {
  files: number
  chunks: number
  vectors: number
}

export interface IndexController {
  job: Job | null
  starting: boolean
  error: string | null
  /** True when the project has never been indexed, so search cannot work yet. */
  firstRun: boolean
  /** Progress check of the stored index state is still in flight. */
  checking: boolean
  /** Cumulative size of the index (not just what the last job touched). */
  totals: IndexTotals | null
  start: () => void
  dismiss: () => void
}

const POLL_MS = 900

/**
 * Projects already refreshed in this window session.
 *
 * The sidebar is remounted whenever the workspace re-syncs, so without this guard
 * every remount would fire another incremental job. Bumping the same project
 * again after a manual index is harmless, hence "once per project, per session".
 */
const autoRefreshed = new Set<number>()

interface Options {
  /** Called once a job settles, so the tree and lists can reload. */
  onSettled?: (job: Job) => void
}

/**
 * Owns the index job for one project.
 *
 * Opening a project inspects `GET /projects/{id}/index` first and then indexes
 * on its own in both cases, because "index my code" is not something the user
 * should have to understand:
 *
 * - never indexed → a first, full job starts immediately (`firstRun` lets the
 *   panel say "正在建立索引" instead of pretending everything is ready);
 * - already indexed → one silent incremental job runs in the background so edits
 *   made outside the app are picked up.
 *
 * Neither blocks the UI, and a finished automatic job hides its card instead of
 * asking to be dismissed. `totals` always reports the cumulative index size, so
 * a warm index with nothing new to do no longer reads as "文件 0 片段 0 向量 0".
 */
export function useIndexJob(projectId: number | null, options: Options = {}): IndexController {
  const [jobId, setJobId] = useState<number | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [firstRun, setFirstRun] = useState(false)
  const [checking, setChecking] = useState(false)
  const [totals, setTotals] = useState<IndexTotals | null>(null)
  const timer = useRef<number | null>(null)
  /** Job ids started automatically; their card hides itself once finished. */
  const autoJobs = useRef<Set<number>>(new Set())
  const settledRef = useRef(options.onSettled)
  settledRef.current = options.onSettled

  const clearTimer = () => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current)
      timer.current = null
    }
  }

  const refreshTotals = useCallback(
    (signal?: AbortSignal) => {
      if (projectId === null) return
      api
        .getIndexStatus(projectId, signal)
        .then((status: IndexStatus) => {
          setTotals({ files: status.files, chunks: status.chunks, vectors: status.vectors })
        })
        .catch(() => {
          // Counts are cosmetic; a failure must not break the panel.
        })
    },
    [projectId],
  )

  const enqueue = useCallback(
    (auto: boolean) => {
      if (projectId === null) return
      if (!auto) {
        setStarting(true)
        setError(null)
      }
      setJob(null)
      setJobId(null)
      api
        .startIndex(projectId)
        .then((res) => {
          if (auto) autoJobs.current.add(res.job_id)
          setJobId(res.job_id)
        })
        .catch((err: unknown) => {
          // A failed background refresh must not shout at the user; the manual
          // button stays available and reports its own errors.
          if (!auto) setError(err instanceof Error ? err.message : String(err))
        })
        .finally(() => {
          if (!auto) setStarting(false)
        })
    },
    [projectId],
  )

  const start = useCallback(() => enqueue(false), [enqueue])

  useEffect(() => {
    // A project switch invalidates any running job view.
    setJob(null)
    setJobId(null)
    setError(null)
    setFirstRun(false)
    setTotals(null)
    clearTimer()
    if (projectId === null) return

    const controller = new AbortController()
    setChecking(true)
    api
      .getIndexStatus(projectId, controller.signal)
      .then((status) => {
        if (controller.signal.aborted) return
        setTotals({ files: status.files, chunks: status.chunks, vectors: status.vectors })
        const last = status.last_job
        if (last && (last.status === 'running' || last.status === 'pending')) {
          // Reattach to a job that survived an app restart.
          setJobId(last.id)
          return
        }
        const indexed = Boolean(status.indexed_at) || status.files > 0
        if (!indexed) {
          // First run: index without waiting for a click, but say so in the UI.
          setFirstRun(true)
          enqueue(true)
          return
        }
        if (autoRefreshed.has(projectId)) return
        autoRefreshed.add(projectId)
        enqueue(true)
      })
      .catch(() => {
        // Status is a nicety: when it fails the manual button still works.
      })
      .finally(() => {
        if (!controller.signal.aborted) setChecking(false)
      })

    return () => controller.abort()
  }, [projectId, enqueue])

  useEffect(() => {
    if (jobId === null) return
    let cancelled = false

    const tick = async () => {
      try {
        const next = await api.getJob(jobId)
        if (cancelled) return
        setJob(next)
        if (next.status === 'done' || next.status === 'error') {
          clearTimer()
          if (autoJobs.current.has(next.id)) {
            autoJobs.current.delete(next.id)
            if (next.status === 'done') setJob(null)
          }
          setFirstRun(false)
          refreshTotals()
          settledRef.current?.(next)
          return
        }
        timer.current = window.setTimeout(() => void tick(), POLL_MS)
      } catch (err) {
        if (cancelled) return
        setError(err instanceof Error ? err.message : String(err))
        clearTimer()
      }
    }

    void tick()
    return () => {
      cancelled = true
      clearTimer()
    }
  }, [jobId, refreshTotals])

  useEffect(() => clearTimer, [])

  const dismiss = useCallback(() => {
    setJob(null)
    setJobId(null)
    setError(null)
    setFirstRun(false)
  }, [])

  return { job, starting, error, firstRun, checking, totals, start, dismiss }
}
