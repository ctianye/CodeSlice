import { useCallback, useEffect, useRef, useState } from 'react'
import * as api from '../api'
import type { Job } from '../types'

export interface IndexController {
  job: Job | null
  starting: boolean
  error: string | null
  /** True when the project has never been indexed, so search cannot work yet. */
  firstRun: boolean
  /** Progress check of the stored index state is still in flight. */
  checking: boolean
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
 * Opening a project inspects `GET /projects/{id}/index` first:
 *
 * - never indexed → `firstRun` is set and the panel offers a one-click first index;
 * - already indexed → one silent incremental job runs in the background so edits
 *   made outside the app are picked up. It never blocks the UI, and its card
 *   disappears on completion instead of asking to be dismissed.
 */
export function useIndexJob(projectId: number | null, options: Options = {}): IndexController {
  const [jobId, setJobId] = useState<number | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [firstRun, setFirstRun] = useState(false)
  const [checking, setChecking] = useState(false)
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
    clearTimer()
    if (projectId === null) return

    let cancelled = false
    setChecking(true)
    api
      .getIndexStatus(projectId)
      .then((status) => {
        if (cancelled) return
        const last = status.last_job
        if (last && (last.status === 'running' || last.status === 'pending')) {
          // Reattach to a job that survived an app restart.
          setJobId(last.id)
          return
        }
        const indexed = Boolean(status.indexed_at) || status.files > 0
        if (!indexed) {
          setFirstRun(true)
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
        if (!cancelled) setChecking(false)
      })

    return () => {
      cancelled = true
    }
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
  }, [jobId])

  useEffect(() => clearTimer, [])

  const dismiss = useCallback(() => {
    setJob(null)
    setJobId(null)
    setError(null)
  }, [])

  return { job, starting, error, firstRun, checking, start, dismiss }
}
