// All writes carry X-Studio: 1 — the API refuses mutating requests without it (cross-site protection).
export type Any = any // eslint-disable-line @typescript-eslint/no-explicit-any

async function handle(r: Response) {
  const text = await r.text()
  let data: Any = null
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!r.ok) throw new Error((data && (data.detail || data.error)) || `HTTP ${r.status}`)
  return data
}

export const api = {
  get: (url: string) => fetch(url).then(handle),
  send: (method: string, url: string, body?: unknown) =>
    fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json', 'X-Studio': '1' },
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then(handle),
  upload: (url: string, form: FormData) =>
    fetch(url, { method: 'POST', headers: { 'X-Studio': '1' }, body: form }).then(handle),
}

export const fileUrl = (rel: string, download = false) =>
  `/api/file?path=${encodeURIComponent(rel)}${download ? '&download=1' : ''}`

export function fmtT(t?: number | null) {
  if (t === null || t === undefined) return '—'
  const m = Math.floor(t / 60)
  const s = t - m * 60
  return `${String(m).padStart(2, '0')}:${s.toFixed(1).padStart(4, '0')}`
}

export function go(hash: string) { window.location.hash = hash }
