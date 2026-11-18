import { useCallback, useEffect, useState } from 'react'
import { api, Any } from './api'

const LABELS: Record<string, string> = {
  queued: 'в очереди', running: 'выполняется', completed: 'готово', partial: 'частично', blocked: 'заблокировано',
  failed: 'ошибка', cancelled: 'отменено', pending: 'ожидает', 'n/a': '—', file: 'файл получен',
  unavailable: 'недоступно', login_wall: 'нужен вход', metadata_only: 'только метаданные', merged: 'объединено',
  awaiting_ai: 'ждёт анализа', signal_only: 'только сигнал', 'frames+ocr': 'кадры + OCR',
  frames_extracted: 'кадры', analyzed_sampled_frames: 'разобрано (выборка кадров)', events_estimated: 'звуковая карта (оценки)', no_speech: 'речи нет',
  no_audio: 'нет звука', technical_only: 'тех. данные', page_text: 'текст страницы',
}
export function label(s?: string | null) {
  if (!s) return '—'
  if (s.startsWith('completed (')) return 'разобрано ' + s.slice(9)
  if (s.startsWith('partial (')) return 'разобрано частично ' + s.slice(8)
  return LABELS[s] ?? s
}
export function tone(s?: string | null) {
  if (!s) return 'muted'
  if (/^(completed|file|frames\+ocr|analyzed|page_text)/.test(s) || /карточ/.test(s)) return 'ok'
  if (/(failed|unavailable|login_wall)/.test(s)) return 'bad'
  if (/(blocked|partial|awaiting|metadata|signal|frames_extracted|technical)/.test(s)) return 'warn'
  if (/(running|queued)/.test(s)) return 'run'
  return 'muted'
}
export function Badge({ s, title }: { s?: string | null; title?: string }) {
  return <span className={`badge ${tone(s)}`} title={title || s || ''}>{label(s)}</span>
}

export const EVIDENCE_SHORT: Record<string, string> = {
  author_explained: 'автор объяснил', observed: 'наблюдается', hypothesis: 'гипотеза',
  user_preference: 'предпочтение', project_confirmed: 'подтверждено проектом',
}
export function Evidence({ e }: { e: string }) {
  return <span className={`ev ev-${e}`}>{EVIDENCE_SHORT[e] ?? e}</span>
}

export function useLoad<T = Any>(url: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const reload = useCallback(() => {
    if (!url) return
    api.get(url).then((d) => { setData(d); setError(null) }).catch((e) => setError(String(e.message || e)))
  }, [url])
  useEffect(() => { reload() }, [reload, ...deps]) // eslint-disable-line react-hooks/exhaustive-deps
  return { data, error, reload, setData }
}

export function usePoll(fn: () => void, ms: number) {
  useEffect(() => { const t = setInterval(fn, ms); return () => clearInterval(t) }, [fn, ms])
}

export function Err({ e }: { e: string | null }) { return e ? <div className="error">{e}</div> : null }

export function Toast({ msg, onDone }: { msg: string | null; onDone: () => void }) {
  useEffect(() => { if (msg) { const t = setTimeout(onDone, 4000); return () => clearTimeout(t) } }, [msg, onDone])
  return msg ? <div className="toast">{msg}</div> : null
}
