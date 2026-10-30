import { useEffect, useRef, useState } from 'react'
import {
  canReadClipboard,
  jobsFromClipboardApi,
  jobsFromDataTransfer,
  jobsFromText,
  type AddJob,
} from '../clipboard'
import { useUploads } from './UploadManager'

/** Paste a screenshot, a copied image, or image/page URLs into the library. */
export function PasteButton() {
  const { addJobs } = useUploads()
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const [hint, setHint] = useState('')
  const boxRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!open) return
    inputRef.current?.focus()
    const onDown = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false)
    }
    window.addEventListener('mousedown', onDown)
    return () => window.removeEventListener('mousedown', onDown)
  }, [open])

  const submit = (jobs: AddJob[], empty: string) => {
    if (!jobs.length) {
      setHint(empty)
      return
    }
    addJobs(jobs)
    setOpen(false)
  }

  const onButton = async () => {
    if (open) {
      setOpen(false)
      return
    }
    setText('')
    setHint('')
    // over https the clipboard can be read directly; plain-http LAN
    // installs need a real paste (⌘V) into the popover instead
    if (canReadClipboard()) {
      try {
        const jobs = await jobsFromClipboardApi()
        if (jobs.length) {
          addJobs(jobs)
          return
        }
      } catch {
        /* permission denied — fall through to the popover */
      }
    }
    setOpen(true)
  }

  return (
    <div className="paste-wrap" ref={boxRef}>
      <button onClick={onButton} title="Add a screenshot, copied image, or image URL from the clipboard">
        Paste
      </button>
      {open && (
        <div className="paste-pop">
          <div className="paste-title">Add from clipboard</div>
          <input
            ref={inputRef}
            value={text}
            placeholder="Press ⌘V — or type an image / page URL"
            onChange={(e) => {
              setText(e.target.value)
              setHint('')
            }}
            onPaste={(e) => {
              const jobs = jobsFromDataTransfer(e.clipboardData)
              if (jobs.length) {
                e.preventDefault()
                submit(jobs, '')
              }
              // otherwise let plain text land in the field
            }}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setOpen(false)
              else if (e.key === 'Enter') {
                e.preventDefault()
                submit(jobsFromText(text), 'Enter a URL starting with http:// or https://')
              }
            }}
          />
          <div className="paste-hint">
            {hint ||
              'Screenshots and copied images upload directly. Image URLs are downloaded; page URLs use the page’s preview image.'}
          </div>
        </div>
      )}
    </div>
  )
}
