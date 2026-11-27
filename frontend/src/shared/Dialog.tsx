import { useEffect, useId, useRef } from 'react'
import type { ReactNode } from 'react'
import { X } from 'lucide-react'

export function Dialog({ title, onClose, children, className }: {
  title: string; onClose: () => void; children: ReactNode; className?: string
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  useEffect(() => {
    const dialog = ref.current
    dialog?.showModal()
    return () => dialog?.close()
  }, [])
  return (
    <dialog ref={ref} className={className ? 'dialog ' + className : 'dialog'} aria-labelledby={titleId} onCancel={(event) => {
      event.preventDefault()
      onClose()
    }} onClick={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <div className="dialog-body">
        <header className="dialog-heading">
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="icon-button" aria-label="Закрыть" onClick={onClose}><X size={21} /></button>
        </header>
        {children}
      </div>
    </dialog>
  )
}
