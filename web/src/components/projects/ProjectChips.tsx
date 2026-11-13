// F6: chips in the top bar for the project= and field.<key>= filters, so
// nothing narrows the grid invisibly. Small: part of the first page.

import { X } from 'lucide-react'
import type { Query } from '../../api'
import { useCaps } from '../../data'
import { fieldChipLabel, projectFilters } from '../../projectFilters'
import { useFieldIndex, useProjects } from './hooks'

export function ProjectChips({ query, setQuery }: { query: Query; setQuery: (p: Partial<Query>) => void }) {
  const f = projectFilters(query)
  const { restricted } = useCaps()
  const any = !!f.project || !!f.custom_fields
  const projects = useProjects('all')
  const fields = useFieldIndex(any && !restricted)
  if (!any) return null
  const projectName = f.project ? projects.data?.find((p) => p.id === f.project)?.name ?? 'a project' : ''
  const fieldName = (key: string) => fields.data?.find((x) => x.key === key)?.name ?? key
  return (
    <>
      {f.project && (
        <span className="chip">
          Project: {projectName}
          <button aria-label={`Clear project ${projectName}`} title="Clear the project filter" onClick={() => setQuery({ project: null })}>
            <X size={14} aria-hidden="true" />
          </button>
        </span>
      )}
      {Object.entries(f.custom_fields ?? {}).map(([k, v]) => {
        const label = fieldChipLabel(fieldName(k), v)
        return (
          <span key={k} className="chip">
            {label}
            <button
              aria-label={`Clear ${label}`}
              title={`Clear ${label}`}
              onClick={() => {
                const next = { ...(query.custom_fields ?? {}) }
                delete next[k]
                setQuery({ custom_fields: Object.keys(next).length ? next : null })
              }}
            >
              <X size={14} aria-hidden="true" />
            </button>
          </span>
        )
      })}
    </>
  )
}
