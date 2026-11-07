import { useEffect, useRef, useState } from 'react'
import * as api from '../api'
import type { IndexStatus, Job } from '../types'

export interface IndexTotals {
  files: number
  chunks: number
  vectors: number
}

export interface IndexController {
  job: Job | null
  error: string | null
  checking: boolean
  totals: IndexTotals | null
}

const ACTIVE_POLL_MS = 900
const IDLE_POLL_MS = 5000

/** Observe backend-owned index work; the renderer never starts or restarts jobs. */
export function useIndexJob(projectId: number | null, onSettled?: (job: Job) => void): IndexController {
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)
  const [totals, setTotals] = useState<IndexTotals | null>(null)
  const onSettledRef = useRef(onSettled)
  onSettledRef.current = onSettled

  useEffect(() => {
    setJob(null)
    setError(null)
    setTotals(null)
    if (projectId === null) return

    let cancelled = false
    let timer: number | null = null
    let lastSettledId: number | null = null
    setChecking(true)

    const poll = async () => {
      try {
        const status: IndexStatus = await api.getIndexStatus(projectId)
        if (cancelled) return
        setError(null)
        setTotals({ files: status.files, chunks: status.chunks, vectors: status.vectors })
        const latest = status.last_job
        const busy = latest?.status === 'running' || latest?.status === 'pending'
        setJob(busy || latest?.status === 'error' ? latest : null)
        if (latest?.status === 'done' && latest.id !== lastSettledId) {
          lastSettledId = latest.id
          onSettledRef.current?.(latest)
        }
        timer = window.setTimeout(() => void poll(), busy ? ACTIVE_POLL_MS : IDLE_POLL_MS)
      } catch (err) {
        if (cancelled) return
        setError(err instanceof Error ? err.message : String(err))
        timer = window.setTimeout(() => void poll(), IDLE_POLL_MS)
      } finally {
        if (!cancelled) setChecking(false)
      }
    }

    void poll()
    return () => {
      cancelled = true
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [projectId])

  return { job, error, checking, totals }
}
