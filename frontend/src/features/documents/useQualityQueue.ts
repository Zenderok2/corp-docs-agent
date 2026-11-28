import { useEffect, useRef, useState } from 'react'
import { api, describeError } from '../../shared/api'
import type { Page, PracticeQueueItem, QualityCase } from '../../shared/api'

export type QualityTab = 'pending' | 'reviewed' | 'withdrawn' | 'practices'
export type QualityNotice = { id: string; tab: QualityTab; title: string; isNew: boolean }

type Snapshot = { cases: QualityCase[]; practices: PracticeQueueItem[] }
const emptyQueue: Snapshot = { cases: [], practices: [] }

function changedItems(previous: Snapshot, next: Snapshot): QualityNotice[] {
  const cases = new Map(previous.cases.map((item) => [item.id, item]))
  const practices = new Map(previous.practices.map((item) => [item.id, item]))
  const changes: QualityNotice[] = []
  for (const item of next.cases) {
    const old = cases.get(item.id)
    const isChanged = old
      ? old.status !== item.status || old.reviewed_at !== item.reviewed_at || old.mail.at(-1)?.id !== item.mail.at(-1)?.id
      : item.status === 'pending'
    if (isChanged) changes.push({
      id: item.id, title: item.question, isNew: !old,
      tab: item.status === 'pending' ? 'pending' : item.status === 'withdrawn' ? 'withdrawn' : 'reviewed',
    })
  }
  for (const item of next.practices) {
    const old = practices.get(item.id)
    if (old ? old.status !== item.status : item.status === 'pending_review') {
      changes.push({ id: item.id, title: item.situation, tab: 'practices', isNew: !old })
    }
  }
  return changes
}

export function useQualityQueue(enabled: boolean) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [notices, setNotices] = useState<QualityNotice[]>([])
  const [failure, setFailure] = useState('')
  const [revision, setRevision] = useState(0)
  const previous = useRef<Snapshot | null>(null)
  const reviewedHere = useRef(new Set<string>())

  function dismiss(id: string) {
    setNotices((items) => items.filter((item) => item.id !== id))
  }

  function refresh(reviewedId?: string) {
    if (reviewedId) {
      reviewedHere.current.add(reviewedId)
      dismiss(reviewedId)
    }
    setRevision((value) => value + 1)
  }

  useEffect(() => {
    if (!enabled) {
      previous.current = null
      reviewedHere.current.clear()
      setSnapshot(null)
      setNotices([])
      setFailure('')
      return
    }
    const abort = new AbortController()
    let timer = 0
    let loading = false
    async function poll() {
      if (loading || abort.signal.aborted) return
      loading = true
      window.clearTimeout(timer)
      try {
        const [cases, practices] = await Promise.all([
          api<Page<QualityCase>>('/quality/cases', { signal: abort.signal }),
          api<Page<PracticeQueueItem>>('/quality/practices', { signal: abort.signal }),
        ])
        if (abort.signal.aborted) return
        const next = { cases: cases.items, practices: practices.items }
        const changes = previous.current ? changedItems(previous.current, next) : []
        previous.current = next
        const updates = changes.filter(({ id }) => !reviewedHere.current.has(id))
        reviewedHere.current.clear()
        const present = new Set([...next.cases, ...next.practices].map(({ id }) => id))
        const updated = new Set(updates.map(({ id }) => id))
        setNotices((items) => {
          const retained = items.filter(({ id }) => present.has(id) && !updated.has(id))
          return updates.length || retained.length !== items.length ? [...updates, ...retained] : items
        })
        setSnapshot(next)
        setFailure('')
      } catch (error) {
        if (!abort.signal.aborted) setFailure(describeError(error))
      } finally {
        loading = false
        if (!abort.signal.aborted) timer = window.setTimeout(poll, 4000)
      }
    }
    function resume() {
      if (document.visibilityState === 'visible') void poll()
    }
    void poll()
    document.addEventListener('visibilitychange', resume)
    return () => {
      abort.abort()
      window.clearTimeout(timer)
      document.removeEventListener('visibilitychange', resume)
    }
  }, [enabled, revision])

  return { ...(snapshot ?? emptyQueue), notices, failure, loading: !snapshot && !failure, dismiss, refresh }
}
