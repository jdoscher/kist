// F6: the projects list (/projects) and a project's page (/project/:id),
// in the library's frame like the boards list. Loaded on first use.

import { useQuery } from '@tanstack/react-query'
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  Archive,
  ArchiveRestore,
  Briefcase,
  ExternalLink,
  LayoutGrid,
  Pencil,
  Plus,
  Trash2,
  X,
} from 'lucide-react'
import { useId, useState } from 'react'
import { api, emptyQuery, thumbUrl, type Query } from '../../api'
import { useAfterUndo, useCaps, useFolders, useSmartFolders } from '../../data'
import { api45 } from '../../m45/api45'
import {
  FIELD_TYPE_LABELS,
  FIELD_TYPES,
  MEMBER_KIND_LABELS,
  groupMembers,
  projectSubtitle,
  projectsApi,
  type FieldDef,
  type FieldType,
  type Member,
  type Project,
  type ProjectPatch,
  type ProjectSummary,
} from '../../projects'
import { useAppState } from '../../state'
import { useWrite } from '../../useWrite'
import { describeError } from '../../writes'
import { Modal, useDialog } from '../Dialog'
import { useBoards } from '../m3/Boards'
import { useNotices } from '../Notices'
import { useProject, useProjects, useProjectsRefresh } from './hooks'
import { PinToggle } from './SidebarPinned'

const TEXT_MAX = 200

function when(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

function countsText(p: ProjectSummary): string {
  const parts: string[] = []
  const c = p.member_counts ?? {}
  const n = (k: string, one: string, many: string) => c[k] && parts.push(`${c[k]} ${c[k] === 1 ? one : many}`)
  n('folder', 'folder', 'folders')
  n('smart', 'smart folder', 'smart folders')
  n('board', 'board', 'boards')
  n('share', 'share link', 'share links')
  n('show', 'show', 'shows')
  if (p.fields?.length) parts.push(`${p.fields.length} field${p.fields.length === 1 ? '' : 's'}`)
  return parts.join(' · ') || 'Nothing in it yet'
}

/** Name, client, job number, phase and notes (new or existing). */
function ProjectDialog({ title, confirmLabel, initial, onDone }: {
  title: string
  confirmLabel: string
  initial?: Partial<ProjectSummary>
  onDone: (v: ProjectPatch & { name: string } | null) => void
}) {
  const id = useId()
  const [v, setV] = useState({
    name: initial?.name ?? '',
    client: initial?.client ?? '',
    job_number: initial?.job_number ?? '',
    phase: initial?.phase ?? '',
    notes: initial?.notes ?? '',
  })
  const set = (k: keyof typeof v) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setV({ ...v, [k]: e.target.value })
  const ok = v.name.trim().length > 0
  return (
    <Modal title={title} onClose={() => onDone(null)} className="dialog pj-dialog" headerClose={false}>
      <form
        className="dialog-form"
        onSubmit={(e) => {
          e.preventDefault()
          if (ok) onDone({ ...v, name: v.name.trim() })
        }}
      >
        <label htmlFor={`${id}-name`}>Name</label>
        <input id={`${id}-name`} data-autofocus maxLength={TEXT_MAX} value={v.name} onChange={set('name')} placeholder="Spring campaign" />
        <div className="pj-dialog-row">
          <div>
            <label htmlFor={`${id}-client`}>Client (optional)</label>
            <input id={`${id}-client`} maxLength={TEXT_MAX} value={v.client} onChange={set('client')} />
          </div>
          <div>
            <label htmlFor={`${id}-job`}>Job number (optional)</label>
            <input id={`${id}-job`} maxLength={TEXT_MAX} value={v.job_number} onChange={set('job_number')} />
          </div>
        </div>
        <label htmlFor={`${id}-phase`}>Phase (optional)</label>
        <input id={`${id}-phase`} maxLength={TEXT_MAX} value={v.phase} onChange={set('phase')} placeholder="Pitch, concept, production, delivered…" />
        <label htmlFor={`${id}-notes`}>Notes (optional)</label>
        <textarea id={`${id}-notes`} rows={4} value={v.notes} onChange={set('notes')} placeholder="The brief, contacts, dates…" />
        <div className="dialog-actions">
          <button type="button" onClick={() => onDone(null)}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={!ok}>
            {confirmLabel}
          </button>
        </div>
      </form>
    </Modal>
  )
}

function useProjectDialog() {
  const [req, setReq] = useState<{
    props: Omit<Parameters<typeof ProjectDialog>[0], 'onDone'>
    resolve: (v: (ProjectPatch & { name: string }) | null) => void
  } | null>(null)
  const ask = (props: Omit<Parameters<typeof ProjectDialog>[0], 'onDone'>) =>
    new Promise<(ProjectPatch & { name: string }) | null>((resolve) => setReq({ props, resolve }))
  const element = req ? (
    <ProjectDialog
      {...req.props}
      onDone={(v) => {
        req.resolve(v)
        setReq(null)
      }}
    />
  ) : null
  return { ask, element }
}

function BackToLibrary() {
  const { setQuery, mobile } = useAppState()
  if (!mobile) return null
  return (
    <button className="icon-btn" onClick={() => setQuery({ ...emptyQuery })} aria-label="Back to the library">
      <X size={16} aria-hidden="true" />
    </button>
  )
}

// ---------- the list ----------

export function ProjectsScreen({ archived = false }: { archived?: boolean }) {
  const { openScreen, mobile } = useAppState()
  const caps = useCaps()
  const projects = useProjects(archived ? 'archived' : 'active')
  const refresh = useProjectsRefresh()
  const dialog = useProjectDialog()
  const w = useWrite()

  const create = async () => {
    const v = await dialog.ask({ title: 'New project', confirmLabel: 'Create' })
    if (!v) return
    void w.run('create', () => projectsApi.create(v), {
      what: 'create the project',
      onDone: (p) => {
        refresh()
        openScreen({ kind: 'project', id: p.id })
      },
    })
  }

  return (
    <main className={`pj-screen${mobile ? ' pj-phone' : ''}`} aria-labelledby="pj-list-h">
      <div className="pj-head">
        <BackToLibrary />
        <h1 id="pj-list-h" className="view-title">
          {archived ? 'Archived projects' : 'Projects'}
        </h1>
        <span className="grow" />
        <button className="btn-icon" aria-pressed={archived} onClick={() => openScreen({ kind: 'projects', archived: !archived })}>
          <Archive size={14} aria-hidden="true" /> {archived ? 'Back to active' : 'Archived'}
        </button>
        {caps.canEdit && !archived && (
          <button className="btn-primary btn-icon" onClick={() => void create()}>
            <Plus size={16} aria-hidden="true" /> New project
          </button>
        )}
      </div>
      {dialog.element}
      {projects.isLoading && <p className="center">Loading…</p>}
      {projects.isError && (
        <p className="error" role="alert">
          Couldn’t load the projects: {describeError(projects.error)}
        </p>
      )}
      {projects.data?.length === 0 && (
        <div className="pj-empty">
          {archived ? (
            <p>No archived projects.</p>
          ) : (
            <>
              <h2>One place per job</h2>
              <p>
                A project gathers the folders, smart folders, boards and share links of one job, with its client, job
                number and phase. Give it custom fields (season, edition, paper…) and every item in its folders gets them.
              </p>
              {caps.canEdit && (
                <button className="btn-primary btn-icon" onClick={() => void create()}>
                  <Plus size={16} aria-hidden="true" /> New project
                </button>
              )}
            </>
          )}
        </div>
      )}
      <ul className="pj-cards">
        {(projects.data ?? []).map((p) => (
          <li key={p.id} className="pj-card">
            <button className="pj-card-open" onClick={() => openScreen({ kind: 'project', id: p.id })}>
              <span className="pj-card-icon" aria-hidden="true">
                <Briefcase size={20} />
              </span>
              <span className="pj-card-text">
                <span className="pj-card-name">{p.name}</span>
                {projectSubtitle(p) && <span className="pj-card-sub">{projectSubtitle(p)}</span>}
                <span className="pj-card-meta">
                  {countsText(p)} · {when(p.modified_at)}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </main>
  )
}

// ---------- one project ----------

export function ProjectPage({ id }: { id: string }) {
  const { openScreen, setQuery, applySmartFolder, selectOnly } = useAppState()
  const caps = useCaps()
  const project = useProject(id)
  const refresh = useProjectsRefresh()
  const afterUndo = useAfterUndo()
  const dialog = useDialog()
  const edit = useProjectDialog()
  const w = useWrite()
  const { notify } = useNotices()
  const recent = useQuery({
    queryKey: ['items', 'project-recent', id],
    queryFn: () => api.listItemsFrom({ ...emptyQuery, project: id, order: '-modified' } as Query, '', 12),
    enabled: !!project.data,
  })

  if (project.isLoading) return <main className="pj-screen center">Loading…</main>
  if (project.isError || !project.data)
    return (
      <main className="pj-screen">
        <div className="pj-head">
          <button className="icon-btn" onClick={() => openScreen({ kind: 'projects' })} aria-label="All projects">
            <ArrowLeft size={16} aria-hidden="true" />
          </button>
          <h1 className="view-title">Project</h1>
        </div>
        <p className="error" role="alert">
          {(project.error as { status?: number } | null)?.status === 404
            ? 'This project no longer exists.'
            : `Couldn’t load the project: ${describeError(project.error)}`}
        </p>
      </main>
    )
  const p = project.data
  const archived = p.status === 'archived'
  const showItems = () => setQuery({ ...emptyQuery, project: p.id })

  const save = (body: ProjectPatch, what: string) =>
    void w.run('save', () => projectsApi.patch(p.id, body), { what, onDone: refresh })
  const editDetails = async () => {
    const v = await edit.ask({ title: 'Edit project', confirmLabel: 'Save', initial: p })
    if (v) save(v, 'save the project')
  }
  const remove = async () => {
    const withValues = p.fields.length
      ? ' Its custom fields go too, and their values are removed from the items. To keep them, archive the project instead.'
      : ''
    const ok = await dialog.confirm({
      title: `Delete project “${p.name}”?`,
      message: `Its folders, boards and share links stay as they are.${withValues}`,
      confirmLabel: 'Delete',
      danger: true,
    })
    if (!ok) return
    void w.run('delete', () => projectsApi.remove(p.id), {
      what: 'delete the project',
      onDone: () => {
        refresh()
        afterUndo()
        openScreen({ kind: 'projects' })
      },
    })
  }
  const openMember = (m: Member) => {
    if (m.missing) return
    if (m.kind === 'folder') setQuery({ ...emptyQuery, folder: m.id })
    else if (m.kind === 'smart') applySmartFolder(m.id, (m.query as Partial<Query>) ?? {})
    else if (m.kind === 'board') openScreen({ kind: 'board', id: m.id })
    else if (m.kind === 'share' && m.url) window.open(m.url, '_blank', 'noopener')
  }
  const removeMember = (m: Member) =>
    void w.run(`m:${m.kind}:${m.id}`, () => projectsApi.removeMember(p.id, m.kind, m.id), {
      what: 'remove it from the project',
      onDone: refresh,
    })
  const sub = projectSubtitle(p)
  const items = recent.data?.items ?? []

  return (
    <main className="pj-screen" aria-labelledby="pj-page-h">
      <div className="pj-head">
        <button className="icon-btn" onClick={() => openScreen({ kind: 'projects', ...(archived ? { archived: true } : {}) })} aria-label="All projects" title="All projects">
          <ArrowLeft size={16} aria-hidden="true" />
        </button>
        <div className="pj-title">
          <span className="title-eyebrow">Project{archived ? ' · archived' : ''}</span>
          <h1 id="pj-page-h" className="page-title">
            {p.name}
          </h1>
          {sub && <span className="pj-sub">{sub}</span>}
        </div>
        <span className="grow" />
        {caps.canEdit && !archived && <PinToggle kind="project" refId={p.id} name={p.name} labelled />}
        <button className="btn-icon" onClick={showItems} title="The items in this project's folders, in the grid">
          <LayoutGrid size={14} aria-hidden="true" /> Items
        </button>
        {caps.canEdit && (
          <>
            <button className="btn-icon" onClick={() => void editDetails()}>
              <Pencil size={14} aria-hidden="true" /> Edit
            </button>
            <button
              className="btn-icon"
              onClick={() => save({ status: archived ? 'active' : 'archived' }, archived ? 'unarchive the project' : 'archive the project')}
              title={archived ? 'Back to the active projects (and its pin comes back)' : 'Hide it from the list and the pins; nothing is deleted'}
            >
              {archived ? <ArchiveRestore size={14} aria-hidden="true" /> : <Archive size={14} aria-hidden="true" />}
              {archived ? 'Unarchive' : 'Archive'}
            </button>
            <button className="btn-icon danger" onClick={() => void remove()}>
              <Trash2 size={14} aria-hidden="true" /> Delete
            </button>
          </>
        )}
      </div>
      {edit.element}
      <div className="pj-body">
        {p.notes && <p className="pj-notes">{p.notes}</p>}

        <section className="pj-section" aria-labelledby="pj-recent-h">
          <div className="pj-section-head">
            <h2 id="pj-recent-h" className="section-label">
              Recent items
            </h2>
            {(recent.data?.total ?? 0) > 0 && (
              <button className="link-btn" onClick={showItems}>
                All {recent.data?.total}
              </button>
            )}
          </div>
          {items.length > 0 ? (
            <ul className="pj-recent">
              {items.map((it) => (
                <li key={it.id}>
                  <button
                    className="pj-recent-tile"
                    title={it.filename}
                    aria-label={it.filename}
                    onClick={() => {
                      showItems()
                      selectOnly(it.id)
                    }}
                  >
                    {it.has_thumb ? <img src={thumbUrl(it) ?? undefined} alt="" loading="lazy" /> : <span className="pj-recent-ext">.{it.ext}</span>}
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="hint">
              {p.members.some((m) => m.kind === 'folder')
                ? 'Nothing filed in its folders yet.'
                : 'Add a folder below: the items filed in it (and its subfolders) are this project’s items.'}
            </p>
          )}
        </section>

        <section className="pj-section" aria-labelledby="pj-members-h">
          <div className="pj-section-head">
            <h2 id="pj-members-h" className="section-label">
              In this project
            </h2>
            {caps.canEdit && <AddMember project={p} onDone={refresh} />}
          </div>
          {p.members.length === 0 && <p className="hint">No folders, boards or share links yet.</p>}
          {groupMembers(p.members).map(([kind, members]) => (
            <div key={kind} className="pj-group">
              <h3 className="pj-group-h">{MEMBER_KIND_LABELS[kind] ?? kind}</h3>
              <ul className="pj-members">
                {members.map((m) => (
                  <li key={`${m.kind}:${m.id}`} className={`pj-member${m.missing ? ' pj-missing' : ''}`}>
                    <button className="pj-member-open" disabled={m.missing} onClick={() => openMember(m)}>
                      {m.missing ? `Deleted ${kind === 'smart' ? 'smart folder' : kind}` : m.name}
                      {m.kind === 'share' && !m.missing && (
                        <>
                          {m.live === false && <span className="pj-tag">expired or revoked</span>}
                          <ExternalLink size={14} aria-hidden="true" />
                          <span className="sr-only"> (opens in a new tab)</span>
                        </>
                      )}
                      {m.trashed && <span className="pj-tag">in the boards trash</span>}
                    </button>
                    {caps.canEdit && (
                      <button className="icon-btn" aria-label={`Remove ${m.name || 'it'} from the project`} title="Remove from the project (it isn't deleted)" onClick={() => removeMember(m)}>
                        <X size={14} aria-hidden="true" />
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </section>

        <FieldsEditor project={p} onDone={() => {
          refresh()
          afterUndo()
        }} notify={(m) => notify({ key: 'pj-fields', message: m })} />
      </div>
    </main>
  )
}

function AddMember({ project, onDone }: { project: Project; onDone: () => void }) {
  const { data: folders } = useFolders()
  const { data: smart } = useSmartFolders()
  const boards = useBoards()
  const shares = useQuery({ queryKey: ['shares'], queryFn: api45.listShares })
  const w = useWrite()
  const has = (kind: string, mid: string) => project.members.some((m) => m.kind === kind && m.id === mid)
  const add = (value: string) => {
    const [kind, mid] = value.split(':')
    if (!kind || !mid) return
    void w.run('add', () => projectsApi.addMember(project.id, kind, mid), { what: 'add it to the project', onDone })
  }
  return (
    <select className="pj-add" value="" aria-label="Add a folder, smart folder, board or share link to the project" onChange={(e) => add(e.target.value)}>
      <option value="">Add…</option>
      <optgroup label="Folders">
        {(folders ?? []).filter((f) => !has('folder', f.id)).map((f) => (
          <option key={f.id} value={`folder:${f.id}`}>
            {f.name}
          </option>
        ))}
      </optgroup>
      <optgroup label="Smart folders">
        {(smart ?? []).filter((s) => !has('smart', s.id)).map((s) => (
          <option key={s.id} value={`smart:${s.id}`}>
            {s.name}
          </option>
        ))}
      </optgroup>
      <optgroup label="Boards">
        {(boards.data ?? []).filter((b) => !has('board', b.id)).map((b) => (
          <option key={b.id} value={`board:${b.id}`}>
            {b.name}
          </option>
        ))}
      </optgroup>
      <optgroup label="Share links">
        {(shares.data ?? []).filter((s) => s.status === 'active' && !has('share', s.id)).map((s) => (
          <option key={s.id} value={`share:${s.id}`}>
            {s.title || `Share of a ${s.target_kind}`}
          </option>
        ))}
      </optgroup>
    </select>
  )
}

function FieldsEditor({ project, onDone, notify }: { project: Project; onDone: () => void; notify: (m: string) => void }) {
  const caps = useCaps()
  const dialog = useDialog()
  const w = useWrite()
  const id = useId()
  const [name, setName] = useState('')
  const [type, setType] = useState<FieldType>('text')
  const [choices, setChoices] = useState('')
  const choiceList = choices.split(',').map((c) => c.trim()).filter(Boolean)
  const ok = name.trim().length > 0 && (type !== 'choice' || choiceList.length > 0)

  const add = (e: React.FormEvent) => {
    e.preventDefault()
    if (!ok) return
    void w.run('field', () => projectsApi.addField(project.id, { name: name.trim(), type, ...(type === 'choice' ? { choices: choiceList } : {}) }), {
      what: 'add the field',
      onDone: () => {
        setName('')
        setChoices('')
        onDone()
      },
    })
  }
  const rename = async (f: FieldDef) => {
    const next = await dialog.prompt({ title: 'Rename field', label: 'Name', initial: f.name, confirmLabel: 'Rename' })
    if (!next || next === f.name) return
    void w.run(`f:${f.id}`, () => projectsApi.patchField(project.id, f.id, { name: next }), { what: 'rename the field', onDone })
  }
  const editChoices = async (f: FieldDef) => {
    const next = await dialog.prompt({
      title: `Choices for ${f.name}`,
      label: 'Choices, comma-separated',
      initial: (f.choices ?? []).join(', '),
      confirmLabel: 'Save',
    })
    if (next === null) return
    const list = next.split(',').map((c) => c.trim()).filter(Boolean)
    void w.run(`f:${f.id}`, () => projectsApi.patchField(project.id, f.id, { choices: list }), { what: 'save the choices', onDone })
  }
  const move = (f: FieldDef, position: number) =>
    void w.run(`f:${f.id}`, () => projectsApi.patchField(project.id, f.id, { position }), { what: 'move the field', onDone })
  const remove = async (f: FieldDef) => {
    const yes = await dialog.confirm({
      title: `Delete the field “${f.name}”?`,
      message: 'Its values are removed from every item that has one.',
      confirmLabel: 'Delete',
      danger: true,
    })
    if (!yes) return
    void w.run(`f:${f.id}`, () => projectsApi.removeField(project.id, f.id), {
      what: 'delete the field',
      onDone: () => {
        onDone()
        notify(`Deleted the field “${f.name}”.`)
      },
    })
  }

  return (
    <section className="pj-section" aria-labelledby={`${id}-h`}>
      <div className="pj-section-head">
        <h2 id={`${id}-h`} className="section-label">
          Custom fields
        </h2>
      </div>
      <p className="hint">
        Every item filed in this project’s folders gets these fields in its info panel. Filter by them in the grid, e.g.{' '}
        <span className="mono">field.season=SS27</span>.
      </p>
      {project.fields.length > 0 && (
        <ul className="pj-fields">
          {project.fields.map((f, i) => (
            <li key={f.id} className="pj-field">
              <span className="pj-field-name">{f.name}</span>
              <span className="pj-field-type">
                {FIELD_TYPE_LABELS[f.type]}
                {f.type === 'choice' && f.choices?.length ? `: ${f.choices.join(', ')}` : ''}
              </span>
              <span className="mono pj-field-key" title="Use it in a filter as field.key=value">
                field.{f.key}
              </span>
              {caps.canEdit && (
                <span className="pj-field-actions">
                  <button className="icon-btn" aria-label={`Move ${f.name} up`} title="Move up" disabled={i === 0} onClick={() => move(f, i - 1)}>
                    <ArrowUp size={14} aria-hidden="true" />
                  </button>
                  <button className="icon-btn" aria-label={`Move ${f.name} down`} title="Move down" disabled={i === project.fields.length - 1} onClick={() => move(f, i + 1)}>
                    <ArrowDown size={14} aria-hidden="true" />
                  </button>
                  <button className="icon-btn" aria-label={`Rename ${f.name}`} title="Rename" onClick={() => void rename(f)}>
                    <Pencil size={14} aria-hidden="true" />
                  </button>
                  {f.type === 'choice' && (
                    <button onClick={() => void editChoices(f)} title="Edit the choices">
                      Choices…
                    </button>
                  )}
                  <button className="icon-btn danger" aria-label={`Delete ${f.name}`} title="Delete the field" onClick={() => void remove(f)}>
                    <Trash2 size={14} aria-hidden="true" />
                  </button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {caps.canEdit && (
        <form className="pj-field-form" onSubmit={add}>
          <label htmlFor={`${id}-name`} className="sr-only">
            New field name
          </label>
          <input id={`${id}-name`} value={name} maxLength={TEXT_MAX} placeholder="New field: Season, Edition, Paper…" onChange={(e) => setName(e.target.value)} />
          <label htmlFor={`${id}-type`} className="sr-only">
            Field type
          </label>
          <select id={`${id}-type`} value={type} onChange={(e) => setType(e.target.value as FieldType)}>
            {FIELD_TYPES.map((t) => (
              <option key={t} value={t}>
                {FIELD_TYPE_LABELS[t]}
              </option>
            ))}
          </select>
          {type === 'choice' && (
            <>
              <label htmlFor={`${id}-choices`} className="sr-only">
                Choices, comma-separated
              </label>
              <input id={`${id}-choices`} value={choices} placeholder="Choices: SS27, AW27" onChange={(e) => setChoices(e.target.value)} />
            </>
          )}
          <button type="submit" className="btn-icon" disabled={!ok}>
            <Plus size={14} aria-hidden="true" /> Add field
          </button>
        </form>
      )}
    </section>
  )
}

export default function ProjectsScreens({ screen }: { screen: { kind: 'projects'; archived?: boolean } | { kind: 'project'; id: string } }) {
  return screen.kind === 'project' ? <ProjectPage key={screen.id} id={screen.id} /> : <ProjectsScreen archived={!!screen.archived} />
}
