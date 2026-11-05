import { useCallback, useEffect, useRef, useState } from 'react'
import * as api from '../api'
import type { Job } from '../types'

export interface IndexController {
  job: Job | null
  starting: boolean
  error: string | null
  start: () => void
  dismiss: () => void
}

const POLL_MS = 900

/** Starts POST /index and polls GET /jobs/{id} until the job settles. */
export function useIndexJob(projectId: number | null): IndexController {
  const [jobId, setJobId] = useState<number | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | null>(null)

  const clearTimer = () => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current)
      timer.current = null
    }
  }

  const start = useCallback(() => {
    if (projectId === null) return
    setStarting(true)
    setError(null)
    setJob(null)
    setJobId(null)
    api
      .startIndex(projectId)
      .then((res) => {
        setJobId(res.job_id)
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => setStarting(false))
  }, [projectId])

  useEffect(() => {
    // A project switch invalidates any running job view.
    setJob(null)
    setJobId(null)
    setError(null)
    clearTimer()
  }, [projectId])

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

  return { job, starting, error, start, dismiss }
}
