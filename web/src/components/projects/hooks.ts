// F6 data hooks: projects, one project, pins and the field index.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback } from 'react'
import { useCaps } from '../../data'
import { projectsApi, type ProjectStatus } from '../../projects'

export function useProjects(status: ProjectStatus | 'all' = 'active') {
  const { ready, restricted } = useCaps()
  return useQuery({
    queryKey: ['projects', status],
    queryFn: () => projectsApi.list(status),
    enabled: ready && !restricted,
    staleTime: 15_000,
  })
}

export function useProject(id: string | null) {
  const { ready, restricted } = useCaps()
  return useQuery({
    queryKey: ['project', id],
    queryFn: () => projectsApi.get(id as string),
    enabled: !!id && ready && !restricted,
  })
}

export function usePins() {
  const { ready } = useCaps()
  return useQuery({ queryKey: ['pins'], queryFn: projectsApi.pins, enabled: ready, staleTime: 15_000 })
}

export function useFieldIndex(enabled = true) {
  const { ready, restricted } = useCaps()
  return useQuery({
    queryKey: ['fieldIndex'],
    queryFn: projectsApi.fields,
    enabled: enabled && ready && !restricted,
    staleTime: 30_000,
  })
}

/** After a project write: the lists, the page, pins and the field index. */
export function useProjectsRefresh() {
  const qc = useQueryClient()
  return useCallback(() => {
    void qc.invalidateQueries({ queryKey: ['projects'] })
    void qc.invalidateQueries({ queryKey: ['project'] })
    void qc.invalidateQueries({ queryKey: ['pins'] })
    void qc.invalidateQueries({ queryKey: ['fieldIndex'] })
    void qc.invalidateQueries({ queryKey: ['itemFields'] })
  }, [qc])
}
