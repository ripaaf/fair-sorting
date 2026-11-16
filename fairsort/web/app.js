// Fair Sorting v2 — shared UI for the desktop window and the web version.
// Plain ES module, no build step. Server API lives in fairsort/server.py.

/* ============================================================== utilities */

const $ = (sel, root = document) => root.querySelector(sel)
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)]
const enc = encodeURIComponent
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))
const sleep = ms => new Promise(r => setTimeout(r, ms))
const pad2 = n => String(n).padStart(2, '0')
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)')
const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

function esc(text) {
  return String(text ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
}

function h(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag)
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue
    if (key === 'class') node.className = value
    else if (key === 'html') node.innerHTML = value
    else if (key === 'text') node.textContent = value
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value)
    else if (key === 'style' && typeof value === 'object') {
      for (const [prop, val] of Object.entries(value)) {
        if (prop.startsWith('--')) node.style.setProperty(prop, val)
        else node.style[prop] = val
      }
    }
    else node.setAttribute(key, value === true ? '' : value)
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) node.append(kid instanceof Node ? kid : document.createTextNode(kid))
  return node
}

function icon(name, cls = 'ic') {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
  svg.setAttribute('class', cls)
  svg.setAttribute('aria-hidden', 'true')
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use')
  use.setAttribute('href', `#i-${name}`)
  svg.append(use)
  return svg
}

function setIcon(button, name) {
  const use = button.querySelector('use')
  if (use) use.setAttribute('href', `#i-${name}`)
}

function debounce(fn, ms) {
  let t
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms) }
}

function fmtBytes(n) {
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++ }
  return `${i === 0 ? n : n < 10 ? n.toFixed(2) : n < 100 ? n.toFixed(1) : Math.round(n)} ${units[i]}`
}

function fmtTime(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0))
  const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60
  return hh ? `${hh}:${pad2(mm)}:${pad2(ss)}` : `${pad2(mm)}:${pad2(ss)}`
}

const dateFmt = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
const fmtDate = value => {
  const d = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
  return isNaN(d) ? '' : dateFmt.format(d)
}

function ago(seconds) {
  const diff = Date.now() / 1000 - seconds
  if (diff < 45) return 'just now'
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`
  return fmtDate(seconds)
}

const extOf = name => { const i = name.lastIndexOf('.'); return i < 0 ? '' : name.slice(i + 1).toLowerCase() }
const baseName = path => path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || path

/* ================================================================ constants */

const WEB_IMAGE = new Set(['jpg', 'jpeg', 'jfif', 'png', 'gif', 'bmp', 'webp', 'avif', 'ico'])
const WEB_VIDEO = new Set(['mp4', 'm4v', 'mov', 'webm', 'ogv', 'mkv'])
const WEB_AUDIO = new Set(['mp3', 'wav', 'm4a', 'ogg', 'aac', 'opus', 'flac'])
const ZOOM_LADDER = [20, 25, 33, 50, 67, 80, 100, 125, 150, 200, 300, 400, 600, 800]
const ZOOM_MIN = 20, ZOOM_MAX = 800
const KIND_ICON = { image: 'image', video: 'video', audio: 'audio' }

const fileUrl = f => `/api/file?p=${enc(f.p)}&v=${f.m}`
const viewUrl = f => `/api/view?p=${enc(f.p)}&v=${f.m}`
const thumbUrl = (f, s = 128) => `/api/thumb?s=${s}&p=${enc(f.p)}&v=${f.m}`
const playUrl = f => `/api/play?p=${enc(f.p)}&v=${f.m}`
const imageUrl = f => (WEB_IMAGE.has(extOf(f.n)) ? fileUrl(f) : viewUrl(f))

/* ==================================================================== state */

const PREF_DEFAULTS = { theme: 'dark', dock: 'bottom', chrome: 'full', film: true, sortKey: 'name', sortDesc: false, volume: 1, muted: false }

function loadPrefs() {
  try { return { ...PREF_DEFAULTS, ...JSON.parse(localStorage.getItem('fs.prefs') || '{}') } } catch { return { ...PREF_DEFAULTS } }
}
function savePrefs() {
  try { localStorage.setItem('fs.prefs', JSON.stringify(S.prefs)) } catch { /* private mode */ }
}

const S = {
  caps: {}, settings: {}, boot: null,
  rev: 0, files: [], byPath: new Map(), meta: null,
  view: [], counts: { all: 0, image: 0, video: 0, audio: 0 },
  cur: -1, curPath: null,
  filter: 'all', query: '',
  prefs: loadPrefs(),
  info: new Map(),
  modals: [], menu: null,
  lan: null, fullscreen: false, gestured: false, started: false,
}

const V = { token: 0, file: null, kind: null, el: null, media: null, natW: 0, natH: 0, fit: 1, zoom: 100, rot: 0, panX: 0, panY: 0, abort: null, converted: false, seeking: false, dir: 0 }

const app = () => $('#app')
const current = () => (S.cur >= 0 ? S.view[S.cur] || null : null)

/* ====================================================================== api */

class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status }
}

async function api(path, body) {
  const post = body !== undefined
  let res
  try {
    res = await fetch(path, {
      method: post ? 'POST' : 'GET',
      headers: post ? { 'Content-Type': 'application/json', 'X-Fair': '1' } : {},
      body: post ? JSON.stringify(body) : undefined,
      cache: 'no-store',
    })
  } catch {
    throw new ApiError('Lost connection to Fair Sorting.', 0)
  }
  let data = null
  try { data = await res.json() } catch { /* empty */ }
  if (!res.ok) {
    if (res.status === 401 && S.started) showGate()
    throw new ApiError(data?.error || `Request failed (${res.status})`, res.status)
  }
  return data
}
const GET = (path, query) => api(query ? `${path}?${new URLSearchParams(query)}` : path)
const POST = (path, body = {}) => api(path, body)

/* =================================================================== toasts */

function toast(html, { kind = 'info', action = null, timeout = 4200, key = null } = {}) {
  const box = $('#toasts')
  // Toasts with the same key replace each other (e.g. repeated theme/dock toggles).
  if (key) $$(`.toast[data-key="${key}"]`, box).forEach(n => n.remove())
  const node = h('div', { class: `toast ${kind}`, 'data-key': key }, h('span', { class: 'dot' }), h('span', { class: 'msg', html }))
  const dismiss = () => { node.classList.add('out'); setTimeout(() => node.remove(), 170) }
  if (action) node.append(h('button', { type: 'button', onclick: () => { dismiss(); action.run() } }, action.label))
  box.append(node)
  while (box.children.length > 3) box.firstElementChild.remove()
  setTimeout(dismiss, timeout)
  return node
}
const fail = err => toast(esc(err?.message || String(err)), { kind: 'err', timeout: 6000 })

/* ================================================================= tooltip */

let tipTimer = 0
function setupTips() {
  const tip = $('#tip')
  const hide = () => { clearTimeout(tipTimer); tip.hidden = true }
  document.addEventListener('pointerover', e => {
    const target = e.target.closest?.('[data-tip]')
    if (!target || e.pointerType === 'touch') return
    clearTimeout(tipTimer)
    tipTimer = setTimeout(() => {
      if (!target.isConnected || !target.matches(':hover')) return
      tip.textContent = target.dataset.tip
      tip.hidden = false
      const r = target.getBoundingClientRect(), t = tip.getBoundingClientRect()
      let top = r.bottom + 8
      if (top + t.height > innerHeight - 8) top = r.top - t.height - 8
      tip.style.top = `${top}px`
      tip.style.left = `${clamp(r.left + r.width / 2 - t.width / 2, 8, innerWidth - t.width - 8)}px`
    }, 380)
  })
  document.addEventListener('pointerout', e => { if (e.target.closest?.('[data-tip]')) hide() })
  document.addEventListener('pointerdown', hide, true)
  addEventListener('blur', hide)
}

/* ================================================================== theming */

function applyTheme() {
  const pref = S.prefs.theme
  const dark = pref === 'dark' || (pref === 'system' && matchMedia('(prefers-color-scheme: dark)').matches)
  const theme = dark ? 'dark' : 'light'
  document.documentElement.dataset.theme = theme
  $('meta[name="theme-color"]').setAttribute('content', dark ? '#121114' : '#f3f0ea')
  if (S.caps.desktop) POST('/api/window/theme', { theme }).catch(() => {})
}

function applyLayout() {
  const a = app()
  a.dataset.dock = S.prefs.dock
  // With no folder open the details panel has nothing to say; give the welcome the room.
  a.dataset.chrome = S.meta && !S.meta.source ? 'min' : S.prefs.chrome
  a.toggleAttribute('data-nofilm', !S.prefs.film)
  requestAnimationFrame(() => { layoutMedia(); renderFilm(true) })
}

function toggleTheme() {
  const now = document.documentElement.dataset.theme
  S.prefs.theme = now === 'dark' ? 'light' : 'dark'
  savePrefs(); applyTheme()
  toast(`${S.prefs.theme === 'dark' ? 'Night' : 'Day'} mode`, { key: 'theme', timeout: 1800 })
}

function cycleDock() {
  const order = ['bottom', 'left', 'right']
  S.prefs.dock = order[(order.indexOf(S.prefs.dock) + 1) % order.length]
  savePrefs(); applyLayout(); renderDeck()
  toast(`Sort keys docked <b>${S.prefs.dock}</b>`, { key: 'dock', timeout: 1800 })
}

function toggleChrome() {
  S.prefs.chrome = S.prefs.chrome === 'full' ? 'min' : 'full'
  savePrefs(); applyLayout()
}

/* ============================================================ list & view */

function ingest(f) {
  f._lc = `${f.f ? f.f + '/' : ''}${f.n}`.toLowerCase()
  return f
}

const SORTERS = {
  name: (a, b) => collator.compare(a.f, b.f) || collator.compare(a.n, b.n),
  date: (a, b) => a.m - b.m || collator.compare(a.n, b.n),
  size: (a, b) => a.s - b.s || collator.compare(a.n, b.n),
  type: (a, b) => a.k.localeCompare(b.k) || extOf(a.n).localeCompare(extOf(b.n)) || collator.compare(a.n, b.n),
}

function rebuildView() {
  const q = S.query.trim().toLowerCase()
  const counts = { all: 0, image: 0, video: 0, audio: 0 }
  const out = []
  for (const f of S.files) {
    if (q && !f._lc.includes(q)) continue
    counts.all++
    counts[f.k]++
    if (S.filter === 'all' || f.k === S.filter) out.push(f)
  }
  out.sort(SORTERS[S.prefs.sortKey] || SORTERS.name)
  if (S.prefs.sortDesc) out.reverse()
  S.view = out
  S.counts = counts
  let idx = S.curPath ? out.findIndex(f => f.p === S.curPath) : -1
  if (idx < 0) idx = clamp(S.cur, 0, out.length - 1)
  S.cur = out.length ? idx : -1
  S.curPath = out[S.cur]?.p ?? null
}

function removeLocal(f) {
  S.files = S.files.filter(x => x !== f)
  S.byPath.delete(f.p)
}

function addLocal(f) {
  if (S.byPath.has(f.p)) return S.byPath.get(f.p)
  ingest(f)
  S.files.push(f)
  S.byPath.set(f.p, f)
  return f
}

async function loadState() {
  const st = await GET('/api/state')
  S.files = st.files.map(ingest)
  S.byPath = new Map(S.files.map(f => [f.p, f]))
  delete st.files
  S.meta = st
  S.rev = st.rev
  if (!S.curPath) {
    // Pick up where this folder was left last time.
    try {
      const last = JSON.parse(localStorage.getItem('fs.last') || 'null')
      if (last && last.src === st.source && S.byPath.has(last.p)) S.curPath = last.p
    } catch { /* ignore */ }
  }
  rebuildView()
  renderAll()
}

const rememberPosition = debounce(f => {
  try { localStorage.setItem('fs.last', JSON.stringify({ src: S.meta?.source, p: f.p })) } catch { /* ignore */ }
}, 400)

// Syncs are serialized: callers that await sync() always see the latest state.
let syncRunning = null, syncQueued = null
function sync() {
  if (!S.started) return Promise.resolve()
  if (syncQueued) return syncQueued
  if (syncRunning) {
    syncQueued = syncRunning.then(() => { syncQueued = null; return sync() })
    return syncQueued
  }
  syncRunning = doSync().finally(() => { syncRunning = null })
  return syncRunning
}

async function doSync() {
  try {
    const ch = await GET('/api/changes', { since: S.rev })
    if (ch.reset) { await loadState(); return }
    if (ch.ops?.length) {
      for (const op of ch.ops) {
        if (op.op === 'remove') { const f = S.byPath.get(op.p); if (f) removeLocal(f) }
        else if (op.op === 'add') addLocal(op.e)
      }
      const before = S.curPath
      rebuildView()
      renderTop(); renderFilm(true)
      if (S.curPath !== before || !V.file || V.file.p !== S.curPath) show()
      else renderCounter()
    }
    if (ch.meta) { S.meta = ch.meta; renderTop(); renderDeck(); renderSession(); if (!$('#drawer').hidden && D.tab === 'session') renderDrawer(false) }
    S.rev = ch.rev
  } catch { /* offline; next tick retries */ }
}

/* ============================================================== navigation */

function go(index, dir = 0) {
  const n = S.view.length
  if (!n) return
  S.cur = ((index % n) + n) % n
  S.curPath = S.view[S.cur].p
  V.dir = dir
  show()
}
const next = () => go(S.cur + 1, 1)
const prev = () => go(S.cur - 1, -1)
function goToPath(path) {
  const idx = S.view.findIndex(f => f.p === path)
  if (idx >= 0) { go(idx); return true }
  return false
}

/* ================================================================ rendering */

function renderAll() {
  applyLayout()
  renderTop(); renderDeck(); renderSession(); renderFilm(true); show()
}

function renderTop() {
  const m = S.meta || {}
  const src = m.source || ''
  $('#sourceName').textContent = src ? baseName(src) : 'Choose a folder'
  $('#sourcePath').textContent = src
  for (const [kind, count] of Object.entries(S.counts)) {
    const el = $(`[data-count="${kind}"]`)
    if (el) el.textContent = count
  }
  for (const b of $$('#filters button')) b.setAttribute('aria-selected', String(b.dataset.kind === S.filter))
  const undo = m.undo || { count: 0 }
  const btn = $('#undoBtn')
  btn.disabled = !undo.count
  btn.dataset.tip = undo.count ? `Undo ${undo.label}  ·  Ctrl+Z` : 'Nothing to undo'
  $('#undoCount').textContent = undo.count > 1 ? undo.count : ''
  $('#shareBtn').hidden = !S.caps.share
  $('#shareDot').hidden = !S.lan?.running
  if (S.boot?.mode === 'web' || !S.caps.desktop) document.title = src ? `${baseName(src)} · Fair Sorting` : 'Fair Sorting'
}

function renderCounter() {
  const n = S.view.length
  const width = Math.max(2, String(n).length)
  $('#ctrCur').textContent = n ? String(S.cur + 1).padStart(width, '0') : '0'.repeat(2)
  $('#ctrTot').textContent = `/${n}`
}

function renderSession() {
  const st = S.meta?.stats || { total: 0, moved: 0, deleted: 0, renamed: 0, started: Date.now() / 1000 }
  const done = st.moved + st.deleted
  const pct = st.total ? done / st.total : 0
  $('#sessionPct').textContent = `${Math.round(pct * 100)}%`
  $('#sessionBar i').style.setProperty('--p', pct)
  const minutes = (Date.now() / 1000 - st.started) / 60
  const parts = []
  if (st.moved) parts.push(`${st.moved} moved`)
  if (st.deleted) parts.push(`${st.deleted} binned`)
  if (st.renamed) parts.push(`${st.renamed} renamed`)
  if (done >= 3 && minutes >= 1) parts.push(`${(done / minutes).toFixed(1)}/min`)
  $('#sessionLine').textContent = parts.length ? `${parts.join(' · ')} — ${done} of ${st.total}` : st.total ? `${st.total} files to go` : 'nothing sorted yet'
}

function metaRows(f, info) {
  const rows = []
  const add = (k, v) => { if (v !== undefined && v !== null && v !== '') rows.push([k, v]) }
  add('size', fmtBytes(info?.size ?? f.s))
  if (info?.width && info?.height) {
    const mp = (info.width * info.height) / 1e6
    add('pixels', `${info.width} × ${info.height}${f.k === 'image' && mp >= 1 ? `  ·  ${mp.toFixed(1)} MP` : ''}`)
  }
  if (info?.duration) add('length', fmtTime(info.duration))
  if (info?.fps) add('frames', `${Math.round(info.fps * 100) / 100} fps`)
  if (info?.frames) add('frames', `${info.frames} (animated)`)
  if (f.k === 'video' && (info?.vcodec || info?.acodec)) add('codec', [info.vcodec, info.acodec].filter(Boolean).join(' / '))
  if (f.k === 'audio' && info?.acodec) add('codec', [info.acodec, info.rate ? `${(info.rate / 1000).toFixed(1)} kHz` : '', info.channels ? `${info.channels} ch` : ''].filter(Boolean).join(' · '))
  if (info?.bitrate && f.k !== 'image') add('bitrate', `${Math.round(info.bitrate / 1000)} kbps`)
  if (info?.tags?.artist) add('artist', info.tags.artist)
  if (info?.tags?.album) add('album', info.tags.album)
  if (info?.taken) add('taken', fmtDate(info.taken))
  if (info?.camera) add('camera', info.camera)
  const exposure = [info?.aperture, info?.shutter, info?.iso ? `ISO ${info.iso}` : '', info?.focal].filter(Boolean).join(' · ')
  if (exposure) add('exposure', exposure)
  add('modified', fmtDate(f.m))
  return rows
}

function renderInspector(f) {
  renderCounter()
  const kindEl = $('#fileKind'), nameEl = $('#fileName'), folderEl = $('#fileFolder'), meta = $('#meta')
  const actions = $$('#fileActions button')
  if (!f) {
    kindEl.textContent = '—'
    nameEl.textContent = S.meta?.source ? 'Nothing selected' : 'No folder open'
    folderEl.textContent = ''
    meta.replaceChildren()
    actions.forEach(b => { b.disabled = true })
    return
  }
  kindEl.textContent = `${f.k} · ${extOf(f.n) || 'file'}`
  nameEl.textContent = f.n
  folderEl.textContent = f.f ? `in ${f.f}/` : ''
  actions.forEach(b => { b.disabled = false })
  $('#openBtn').hidden = !S.caps.local
  $('#revealBtn').hidden = !S.caps.local
  const info = S.info.get(`${f.p}|${f.m}`)
  const rows = metaRows(f, info)
  meta.replaceChildren(...rows.flatMap(([k, v]) => [h('dt', {}, k), h('dd', {}, v)]))
  if (!info) meta.append(h('dt', {}, ''), h('dd', {}, h('span', { class: 'skel', style: { width: '62%' } })))
}

const fetchInfo = debounce(async f => {
  const key = `${f.p}|${f.m}`
  if (S.info.has(key)) { checkCodecs(f, S.info.get(key)); return }
  try {
    const info = await GET('/api/info', { p: f.p })
    S.info.set(key, info)
    if (S.info.size > 400) S.info.delete(S.info.keys().next().value)
    if (current() === f) {
      renderInspector(f)
      if (f.k === 'audio') updateAudioCard(f, info)
      checkCodecs(f, info)
    }
  } catch { /* file vanished; sync will catch up */ }
}, 90)

// Containers like MKV/MOV often "play" with a silent AC3/DTS track or no picture.
// ffprobe tells us up front, so convert before the user notices.
const PLAYABLE_VIDEO = new Set(['h264', 'vp8', 'vp9', 'av1', 'theora', 'hevc'])
const PLAYABLE_AUDIO = new Set(['aac', 'mp3', 'opus', 'vorbis', 'flac'])
function checkCodecs(f, info) {
  if (!info || V.file !== f || V.converted || !S.caps.ffmpeg || f.k === 'image') return
  const badAudio = info.acodec && !PLAYABLE_AUDIO.has(info.acodec) && !info.acodec.startsWith('pcm_')
  const badVideo = f.k === 'video' && info.vcodec && !PLAYABLE_VIDEO.has(info.vcodec)
  if (badAudio || badVideo) convert(f, V.token)
}

/* ---------------------------------------------------------------- filmstrip */

const filmNodes = new Map()
let filmRaf = 0
const filmItemWidth = () => (matchMedia('(max-width: 860px)').matches ? 52 : 64)

function renderFilm(center = false) {
  const film = $('#film'), track = $('#filmTrack')
  if (!film || film.offsetParent === null) { filmNodes.forEach(n => n.remove()); filmNodes.clear(); return }
  const W = filmItemWidth(), n = S.view.length
  track.style.width = `${n * W}px`
  if (center && S.cur >= 0) {
    const target = S.cur * W - (film.clientWidth - W) / 2
    const far = Math.abs(target - film.scrollLeft) > film.clientWidth * 1.5
    film.scrollTo({ left: Math.max(0, target), behavior: far || reduceMotion.matches ? 'auto' : 'smooth' })
  }
  const left = center && S.cur >= 0 ? Math.max(0, S.cur * W - (film.clientWidth - W) / 2) : film.scrollLeft
  const lo = Math.min(left, film.scrollLeft)
  const hi = Math.max(left, film.scrollLeft) + film.clientWidth
  const start = Math.max(0, Math.floor(lo / W) - 4)
  const end = Math.min(n, Math.ceil(hi / W) + 4)
  const keep = new Set()
  for (let i = start; i < end; i++) {
    const f = S.view[i]
    keep.add(f.p)
    let node = filmNodes.get(f.p)
    if (!node) {
      node = h('button', { class: 'thumb', type: 'button', title: f.n, 'aria-label': f.n })
      if (f.k === 'audio') node.append(icon('audio'))
      else {
        const img = h('img', { loading: 'lazy', decoding: 'async', alt: '', src: thumbUrl(f, 128) })
        img.onerror = () => img.replaceWith(icon(KIND_ICON[f.k]))
        node.append(img)
        if (f.k === 'video') node.append(icon('play', 'tag'))
      }
      node.addEventListener('click', () => { const idx = S.view.indexOf(f); if (idx >= 0) go(idx, Math.sign(idx - S.cur)) })
      filmNodes.set(f.p, node)
      track.append(node)
    }
    node.style.left = `${i * W + 4}px`
    node.classList.toggle('cur', i === S.cur)
  }
  for (const [path, node] of filmNodes) if (!keep.has(path)) { node.remove(); filmNodes.delete(path) }
}

/* --------------------------------------------------------------------- deck */

// Destinations come in banks of ten. Keys 1–0 act on the active bank, Shift+1–0
// on the next one, [ and ] switch banks, and G opens a picker for any of them.
const BANK = 10
S.bank = 0
const bankCount = () => Math.max(1, Math.ceil((S.meta?.destinations?.length || 0) / BANK))
const bankLabel = b => `${b * BANK + 1}–${Math.min((b + 1) * BANK, S.meta?.destinations?.length || 0)}`

function destKey(i) {
  const bank = Math.floor(i / BANK)
  const digit = String(((i % BANK) + 1) % 10)
  if (bank === S.bank) return digit
  if (bank === S.bank + 1) return `⇧${digit}`
  return ''
}

function switchBank(delta) {
  const n = bankCount()
  if (n < 2) { toast('Banks kick in once you have more than 10 destinations.', { key: 'bank', timeout: 2200 }); return }
  S.bank = (S.bank + delta + n) % n
  renderDeck()
  toast(`Number keys now move to <b>${bankLabel(S.bank)}</b>`, { key: 'bank', timeout: 1600 })
}

let deckSig = null
function renderDeck() {
  const tiles = $('#deckTiles')
  const dests = S.meta?.destinations || []
  const banks = bankCount()
  S.bank = clamp(S.bank, 0, banks - 1)
  const first = dests.length > BANK ? S.bank * BANK : 0
  const shown = dests.slice(first, dests.length > BANK ? first + BANK : dests.length)
  const sig = `${S.bank}\n` + dests.map(d => `${d.path}|${d.label}|${d.exists}`).join('\n')
  if (sig === deckSig && tiles.childElementCount) {
    // Same tiles: only refresh the per-session counters so animations keep running.
    shown.forEach((d, k) => {
      const count = tiles.querySelector(`.tile[data-i="${first + k}"] .tile-count`)
      if (count) { count.textContent = d.count ? `${d.count}` : ''; count.classList.toggle('has', !!d.count) }
    })
    return
  }
  deckSig = sig
  const bankBox = $('#deckBanks')
  bankBox.hidden = banks < 2
  bankBox.replaceChildren(...(banks < 2 ? [] : Array.from({ length: banks }, (_, b) => h('button', {
    type: 'button', role: 'tab', 'aria-selected': String(b === S.bank), title: `Keys for destinations ${bankLabel(b)}`,
    onclick: () => { S.bank = b; renderDeck() },
  }, bankLabel(b)))))
  $('#deckFind').hidden = dests.length < 2
  const nodes = shown.map((d, k) => {
    const i = first + k
    const key = destKey(i)
    const label = d.label || d.name
    const hit = h('button', {
      class: 'tile-hit', type: 'button',
      'aria-label': `Move to ${label}${key ? ` (key ${key})` : ''}`,
      onclick: () => moveTo(i),
    })
    const more = h('button', { class: 'tile-more', type: 'button', 'aria-label': `Options for ${label}`, onclick: e => { e.stopPropagation(); tileMenu(e.currentTarget, i) } }, icon('dots', 'ic sm'))
    const tile = h('div', { class: `tile${d.exists ? '' : ' missing'}`, 'data-i': i, title: d.path },
      hit,
      h('div', { class: 'tile-top' }, key ? h('kbd', { class: 'key' }, key) : null, h('span', { class: `tile-count${d.count ? ' has' : ''}` }, d.count ? `${d.count}` : '')),
      h('div', { class: 'tile-name' }, label),
      h('div', { class: 'tile-path' }, d.exists ? d.path : 'folder not found'),
      more,
    )
    tile.addEventListener('contextmenu', e => { e.preventDefault(); tileMenu({ x: e.clientX, y: e.clientY }, i) })
    return tile
  })
  if (!dests.length) {
    nodes.push(h('button', { class: 'tile add first', type: 'button', onclick: addDestinations },
      icon('folder-plus', 'ic lg'),
      h('span', {}, h('b', {}, 'Add destination folders'), h('small', {}, 'Each one gets a number key. Press A anytime.')),
    ))
  } else {
    nodes.push(h('button', { class: 'tile add', type: 'button', onclick: addDestinations, 'data-tip': 'Add destination folders  A' }, icon('plus', 'ic lg'), h('span', { class: 'mono', style: { fontSize: '11px' } }, 'add folder')))
  }
  tiles.replaceChildren(...nodes)
  const hint = $('#deckHint')
  hint.hidden = !dests.length
  hint.replaceChildren('press ', h('kbd', { class: 'key' }, '1'), '–', h('kbd', { class: 'key' }, '0'),
    ...(banks > 1 ? ['  ·  ', h('kbd', { class: 'key' }, '['), h('kbd', { class: 'key' }, ']'), ' bank'] : []))
}

function openDestPicker() {
  const dests = S.meta?.destinations || []
  if (!dests.length) { addDestinations(); return }
  const f = current()
  let sel = 0
  let list = []
  const input = h('input', { class: 'input', placeholder: 'Type a name or a number', autofocus: true, spellcheck: 'false', 'aria-label': 'Filter destinations' })
  const box = h('div', { class: 'picker-list hist', role: 'listbox' })
  const m = modal({
    title: 'move to',
    body: [h('h2', {}, f ? 'Move to…' : 'Destinations'), f ? h('p', { class: 'mono', style: { fontSize: '12px', overflowWrap: 'anywhere' } }, f.n) : null, input, box],
    foot: [h('div', { class: 'left mono' }, '↑ ↓ choose · Enter move · Esc close')],
  })
  const pick = k => {
    const item = list[k]
    if (!item) return
    m.close()
    if (f) moveTo(item.i)
    else { S.bank = Math.floor(item.i / BANK); renderDeck() }
  }
  const draw = () => {
    const q = input.value.trim().toLowerCase()
    const numeric = /^\d+$/.test(q)
    const byPath = /[\\/:]/.test(q)
    list = dests.map((d, i) => ({ d, i })).filter(({ d, i }) =>
      !q || (numeric ? String(i + 1).startsWith(q) : (byPath ? d.path : `${d.label} ${d.name}`).toLowerCase().includes(q)))
    sel = clamp(sel, 0, Math.max(0, list.length - 1))
    box.replaceChildren(...(list.length ? list.map(({ d, i }, k) => {
      const key = destKey(i)
      const row = h('button', { class: `hist-item${k === sel ? ' open' : ''}${d.exists ? '' : ' missing'}`, type: 'button', role: 'option', 'aria-selected': String(k === sel) },
        h('span', { class: 'hist-ico mono', style: { fontSize: '11px' } }, String(i + 1)),
        h('span', { class: 'hist-main' }, h('span', { class: 'hist-name' }, d.label || d.name), h('span', { class: 'hist-sub' }, d.exists ? d.path : 'folder not found')),
        h('span', { class: 'hist-meta' }, key ? h('kbd', { class: 'key' }, key) : d.count ? `${d.count} moved` : ''),
      )
      row.onclick = () => pick(k)
      row.onpointerenter = () => { sel = k; $$('.hist-item', box).forEach((n, x) => n.classList.toggle('open', x === k)) }
      return row
    }) : [h('div', { class: 'drawer-empty' }, 'No destination matches.')]))
    box.children[sel]?.scrollIntoView({ block: 'nearest' })
  }
  input.addEventListener('input', () => { sel = 0; draw() })
  input.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(sel + 1, list.length - 1); draw() }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(sel - 1, 0); draw() }
    else if (e.key === 'Enter') { e.preventDefault(); pick(sel) }
  })
  draw()
}

function flashTile(i) {
  const tile = $(`#deckTiles .tile[data-i="${i}"]`)
  if (!tile) return
  tile.classList.remove('hit')
  void tile.offsetWidth
  tile.classList.add('hit')
  tile.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: reduceMotion.matches ? 'auto' : 'smooth' })
}

function tileMenu(anchor, i) {
  const d = S.meta?.destinations?.[i]
  if (!d) return
  const dests = S.meta.destinations
  const reorder = to => {
    const paths = dests.map(x => x.path)
    const [item] = paths.splice(i, 1)
    paths.splice(to, 0, item)
    POST('/api/dest/order', { paths }).then(sync).catch(fail)
  }
  openMenu(anchor, [
    { head: d.label || d.name, sub: d.path },
    { icon: 'pen', label: 'Rename label…', run: () => labelDestination(i) },
    S.caps.local && d.exists && { icon: 'reveal', label: 'Show in Explorer', run: () => POST('/api/reveal', { path: d.path }).catch(fail) },
    i > 0 && { icon: 'left', label: 'Move earlier', run: () => reorder(i - 1) },
    i < dests.length - 1 && { icon: 'right', label: 'Move later', run: () => reorder(i + 1) },
    '-',
    { icon: 'x', label: 'Remove from list', danger: true, run: () => removeDestination(i) },
  ])
}

async function labelDestination(i) {
  const d = S.meta.destinations[i]
  const value = await prompt_({ title: 'Destination label', heading: 'Rename label', text: `Shown on the key. The folder itself stays "${d.name}".`, value: d.label || d.name, ok: 'Save' })
  if (value == null) return
  POST('/api/dest/label', { path: d.path, label: value === d.name ? '' : value }).then(sync).catch(fail)
}

async function removeDestination(i) {
  const d = S.meta.destinations[i]
  try {
    await POST('/api/dest/remove', { path: d.path })
    await sync()
    toast(`Removed <b>${esc(d.label || d.name)}</b> from the list. The folder is untouched.`, {
      action: { label: 'Undo', run: () => POST('/api/dest/add', { paths: [d.path] }).then(() => POST('/api/dest/order', { paths: [...S.meta.destinations.map(x => x.path).filter(p => p !== d.path).slice(0, i), d.path] })).then(sync).catch(fail) },
    })
  } catch (e) { fail(e) }
}

/* =================================================================== stage */

function stageBox() {
  const r = $('#stage').getBoundingClientRect()
  const small = innerWidth <= 860
  const pad = small ? 10 : 22
  const bottom = V.kind === 'image' && !small ? 74 : small ? 56 : pad
  return { w: Math.max(40, r.width - pad * 2), h: Math.max(40, r.height - pad - bottom), oy: (pad - bottom) / 2 }
}

function layoutMedia() {
  const el = V.el
  if (!el || !V.natW || !V.natH || V.kind === 'audio') return
  const box = stageBox()
  const swap = Math.abs(V.rot % 180) === 90
  const w = swap ? V.natH : V.natW
  const hgt = swap ? V.natW : V.natH
  let fit = Math.min(box.w / w, box.h / hgt)
  if (V.kind === 'image' && !el.classList.contains('placeholder')) fit = Math.min(fit, 1)
  V.fit = fit
  el.style.width = `${V.natW * fit}px`
  el.style.height = `${V.natH * fit}px`
  clampPan()
  applyTransform()
}

function applyTransform() {
  const el = V.el
  if (!el) return
  const box = stageBox()
  el.style.transform = `translate(-50%, -50%) translate(${V.panX}px, ${V.panY + box.oy}px) scale(${V.zoom / 100}) rotate(${V.rot}deg)`
  el.classList.toggle('pixel', V.kind === 'image' && V.fit * (V.zoom / 100) > 2.5)
  const wrap = $('#mediaWrap')
  wrap.classList.toggle('grab', V.zoom > 100 && V.kind !== 'audio')
  $('#zoomRead').textContent = `${Math.round(V.zoom)}%`
}

function clampPan() {
  if (V.zoom <= 100) { V.panX = 0; V.panY = 0; return }
  const box = stageBox()
  const swap = Math.abs(V.rot % 180) === 90
  const w = (swap ? V.natH : V.natW) * V.fit * (V.zoom / 100)
  const hgt = (swap ? V.natW : V.natH) * V.fit * (V.zoom / 100)
  const mx = Math.max(0, (w - box.w) / 2 + 48)
  const my = Math.max(0, (hgt - box.h) / 2 + 48)
  V.panX = clamp(V.panX, -mx, mx)
  V.panY = clamp(V.panY, -my, my)
}

function setZoom(z, cx = 0, cy = 0) {
  if (!V.el || V.kind === 'audio') return
  z = clamp(z, ZOOM_MIN, ZOOM_MAX)
  const ratio = z / V.zoom
  V.panX = cx - (cx - V.panX) * ratio
  V.panY = cy - (cy - V.panY) * ratio
  V.zoom = z
  clampPan()
  applyTransform()
}

function zoomStep(dir) {
  const z = V.zoom
  const target = dir > 0 ? ZOOM_LADDER.find(v => v > z + 0.5) ?? ZOOM_MAX : [...ZOOM_LADDER].reverse().find(v => v < z - 0.5) ?? ZOOM_MIN
  setZoom(target)
}

function resetZoom() {
  V.zoom = 100; V.panX = 0; V.panY = 0
  applyTransform()
}

function rotate() {
  if (!V.el || V.kind === 'audio') return
  V.rot = (V.rot + 90) % 360
  V.panX = V.panY = 0
  layoutMedia()
}

function setState(node) {
  const box = $('#stageState')
  if (!node) { box.hidden = true; box.replaceChildren(); return }
  box.replaceChildren(node)
  box.hidden = false
}

function stateBox({ title, text, progress, actions = [] }) {
  const kids = [h('h3', {}, title)]
  if (text) kids.push(h('p', {}, text))
  if (progress !== undefined) kids.push(h('div', { class: 'blocks' }, h('i', { style: { '--p': progress } })))
  if (actions.length) kids.push(h('div', { class: 'row' }, actions))
  return h('div', { class: 'state-box' }, ...kids)
}

function stopMedia() {
  V.abort?.abort()
  V.abort = null
  const m = V.media
  if (m) {
    try { m.pause() } catch { /* ignore */ }
    if (m !== A.el) { m.removeAttribute('src'); m.load() }
  }
  V.media = null
  stopViz()
}

function ghostOut() {
  const el = V.el
  if (!el || reduceMotion.matches || V.kind === 'audio') return
  const stage = $('#stage')
  el.classList.add('ghost')
  stage.append(el)
  const anim = el.animate([{ opacity: 1, translate: '0 0' }, { opacity: 0, translate: '0 34px' }], { duration: 170, easing: 'cubic-bezier(.4,0,1,1)' })
  anim.onfinish = () => el.remove()
  setTimeout(() => el.remove(), 400)
}

function show() {
  const f = current()
  renderInspector(f)
  renderFilm(true)
  const a = app()
  a.toggleAttribute('data-empty', !f)
  if (!f) {
    stopMedia()
    V.file = null; V.el = null
    $('#mediaWrap').replaceChildren()
    $('#controls').hidden = true
    renderEmpty()
    return
  }
  if (V.file === f) return
  rememberPosition(f)
  loadMedia(f)
}

function configureControls(kind) {
  $('#controls').hidden = false
  $('#playGroup').hidden = kind === 'image'
  $('#zoomGroup').hidden = kind === 'audio'
  $('#fixBtn').hidden = true
  updatePlayButton()
  setSeek(0, 0)
}

function loadMedia(f) {
  const token = ++V.token
  stopMedia()
  V.file = f; V.kind = f.k; V.el = null; V.natW = V.natH = 0
  V.zoom = 100; V.rot = 0; V.panX = V.panY = 0; V.converted = false
  V.abort = new AbortController()
  setState(null)
  configureControls(f.k)
  const wrap = $('#mediaWrap')
  wrap.replaceChildren()
  if (f.k === 'image') loadImage(f, token, wrap)
  else if (f.k === 'video') loadVideo(f, token, wrap)
  else loadAudio(f, token, wrap)
  fetchInfo(f)
}

function enterAnim(el) {
  if (reduceMotion.matches) return
  const dx = V.dir * 14
  el.animate([{ opacity: 0, translate: `${dx}px 0` }, { opacity: 1, translate: '0 0' }], { duration: 160, easing: 'cubic-bezier(.22,1,.36,1)' })
}

function loadImage(f, token, wrap) {
  const img = new Image()
  img.className = 'media-el'
  img.alt = f.n
  img.decoding = 'async'
  img.draggable = false
  let settled = false
  let usedView = !WEB_IMAGE.has(extOf(f.n))
  const slow = setTimeout(() => {
    if (token !== V.token || settled) return
    const ph = new Image()
    ph.className = 'media-el placeholder'
    ph.alt = ''
    ph.onload = () => {
      if (token !== V.token || settled) return
      V.el = ph; V.natW = ph.naturalWidth; V.natH = ph.naturalHeight
      wrap.replaceChildren(ph); layoutMedia()
    }
    ph.src = thumbUrl(f, 512)
    if (usedView) setState(stateBox({ title: 'Converting preview…', text: `${extOf(f.n).toUpperCase()} isn't shown by browsers directly, so it's being converted once and cached.` }))
  }, 140)
  img.onload = async () => {
    if (token !== V.token) return
    settled = true
    clearTimeout(slow)
    try { await img.decode() } catch { /* fine */ }
    if (token !== V.token) return
    setState(null)
    V.el = img; V.natW = img.naturalWidth; V.natH = img.naturalHeight
    wrap.replaceChildren(img)
    layoutMedia()
    enterAnim(img)
    preloadNeighbors()
  }
  img.onerror = () => {
    if (token !== V.token) return
    if (!usedView) { usedView = true; img.src = viewUrl(f); return }
    settled = true
    clearTimeout(slow)
    wrap.replaceChildren()
    V.el = null
    setState(stateBox({
      title: "Can't preview this one",
      text: 'The file may be damaged or in a format we can\'t decode. You can still sort it.',
      actions: S.caps.local ? [h('button', { class: 'btn', type: 'button', onclick: openExternal }, icon('external'), 'Open in default app')] : [],
    }))
  }
  img.src = imageUrl(f)
}

const preloaded = new Map()
function preloadNeighbors() {
  const n = S.view.length
  if (n < 2) return
  const keep = new Set()
  for (const d of [1, 2, -1]) {
    const f = S.view[(S.cur + d + n) % n]
    if (!f || f.k !== 'image') continue
    const url = imageUrl(f)
    keep.add(url)
    if (!preloaded.has(url)) { const im = new Image(); im.decoding = 'async'; im.src = url; preloaded.set(url, im) }
  }
  for (const key of preloaded.keys()) if (!keep.has(key)) preloaded.delete(key)
}

function bindMediaEvents(m, token) {
  const signal = V.abort.signal
  const on = (type, fn) => m.addEventListener(type, e => { if (token === V.token) fn(e) }, { signal })
  on('timeupdate', () => { if (!V.seeking) setSeek(m.currentTime, m.duration) })
  on('durationchange', () => setSeek(m.currentTime, m.duration))
  on('play', () => { updatePlayButton(); if (m === A.el) startViz() ; armIdle() })
  on('pause', () => { updatePlayButton(); app().removeAttribute('data-idle') })
  on('ended', () => updatePlayButton())
  on('volumechange', () => { S.prefs.volume = m.volume; S.prefs.muted = m.muted; savePrefs(); updateVolumeUi() })
  m.volume = S.prefs.volume
  m.muted = S.prefs.muted
  updateVolumeUi()
}

function maybeAutoplay(m, token) {
  if (!S.settings.autoplay) return
  const p = m.play()
  if (p) p.catch(() => { if (token === V.token) updatePlayButton() })
}

function loadVideo(f, token, wrap) {
  const v = document.createElement('video')
  v.className = 'media-el'
  v.playsInline = true
  v.preload = 'auto'
  v.poster = thumbUrl(f, 512)
  V.media = v
  bindMediaEvents(v, token)
  const signal = V.abort.signal
  v.addEventListener('loadedmetadata', () => {
    if (token !== V.token) return
    if (!v.videoWidth) { convert(f, token); return }
    V.el = v; V.natW = v.videoWidth; V.natH = v.videoHeight
    setState(null)
    layoutMedia()
    enterAnim(v)
  }, { signal })
  v.addEventListener('error', () => {
    if (token !== V.token) return
    if (!V.converted) convert(f, token)
    else setState(stateBox({ title: "This video won't play", text: 'Even the converted copy failed. Try opening it in your usual player.', actions: openActions() }))
  }, { signal })
  wrap.replaceChildren(v)
  const native = WEB_VIDEO.has(extOf(f.n)) && !S.settings.always_convert
  if (native) {
    v.src = fileUrl(f)
    $('#fixBtn').hidden = !S.caps.ffmpeg
    maybeAutoplay(v, token)
  } else convert(f, token)
}

function openActions() {
  return S.caps.local ? [h('button', { class: 'btn', type: 'button', onclick: openExternal }, icon('external'), 'Open in default app')] : []
}

async function convert(f, token, retry = false) {
  const m = V.media
  if (!m) return
  V.converted = true
  $('#fixBtn').hidden = true
  try { m.pause() } catch { /* ignore */ }
  const label = f.k === 'audio' ? 'audio' : 'video'
  setState(stateBox({ title: 'Getting it ready…', text: `Making a browser-friendly copy of this ${label}. It's cached, so next time is instant.`, progress: 0 }))
  while (token === V.token) {
    let r
    try { r = await POST('/api/prepare', { path: f.p, retry }) } catch (e) {
      if (token === V.token) setState(stateBox({ title: "Couldn't prepare it", text: e.message, actions: openActions() }))
      return
    }
    retry = false
    if (token !== V.token) return
    if (r.ready) {
      setState(null)
      m.src = playUrl(f)
      m.load()
      maybeAutoplay(m, token)
      return
    }
    if (r.status === 'error' || r.status === 'cancelled') {
      setState(stateBox({
        title: 'Conversion failed',
        text: r.error || 'FFmpeg could not read this file.',
        actions: [h('button', { class: 'btn', type: 'button', onclick: () => convert(f, token, true) }, icon('refresh'), 'Try again'), ...openActions()],
      }))
      return
    }
    const bar = $('#stageState .blocks i')
    if (bar) bar.style.setProperty('--p', r.progress || 0)
    await sleep(450)
  }
}

/* ------------------------------------------------------------------- audio */

const A = { el: null, ctx: null, analyser: null, data: null, raf: 0, canvas: null }

function audioEl() {
  if (!A.el) { A.el = new Audio(); A.el.preload = 'auto' }
  return A.el
}

function ensureAnalyser() {
  if (A.ctx) { if (A.ctx.state === 'suspended' && S.gestured) A.ctx.resume(); return }
  if (!S.gestured) return
  try {
    A.ctx = new AudioContext()
    const src = A.ctx.createMediaElementSource(A.el)
    A.analyser = A.ctx.createAnalyser()
    A.analyser.fftSize = 128
    A.analyser.smoothingTimeConstant = 0.8
    src.connect(A.analyser)
    A.analyser.connect(A.ctx.destination)
    A.data = new Uint8Array(A.analyser.frequencyBinCount)
  } catch { A.ctx = null }
}

function loadAudio(f, token, wrap) {
  const a = audioEl()
  a.pause()
  V.media = a
  const cover = h('div', { class: 'audio-cover glyph' }, icon('audio'))
  const coverImg = h('img', { class: 'audio-cover', alt: '' })
  coverImg.onload = () => { if (token === V.token) cover.replaceWith(coverImg) }
  coverImg.src = thumbUrl(f, 512)
  const title = h('h2', { class: 'audio-title display' }, f.n.replace(/\.[^.]+$/, ''))
  const sub = h('p', { class: 'audio-sub mono' }, `${extOf(f.n)} · ${fmtBytes(f.s)}`)
  const canvas = h('canvas', { class: 'viz', 'aria-hidden': 'true' })
  A.canvas = canvas
  wrap.replaceChildren(h('div', { class: 'audio-card' }, h('div', { class: 'audio-inner' }, cover, title, sub, canvas)))
  enterAnim(wrap.firstElementChild)
  bindMediaEvents(a, token)
  a.addEventListener('error', () => {
    if (token !== V.token) return
    if (!V.converted) convert(f, token)
    else setState(stateBox({ title: "This audio won't play", text: 'Try opening it in your usual player.', actions: openActions() }))
  }, { signal: V.abort.signal })
  drawViz(0)
  const native = WEB_AUDIO.has(extOf(f.n)) && !S.settings.always_convert
  if (native) { a.src = fileUrl(f); maybeAutoplay(a, token) } else convert(f, token)
  const info = S.info.get(`${f.p}|${f.m}`)
  if (info) updateAudioCard(f, info)
}

function updateAudioCard(f, info) {
  if (V.file !== f) return
  const t = info?.tags || {}
  if (t.title) $('.audio-title')?.replaceChildren(t.title)
  const bits = [t.artist, t.album].filter(Boolean)
  if (bits.length) $('.audio-sub')?.replaceChildren(bits.join(' · '))
}

function startViz() {
  ensureAnalyser()
  cancelAnimationFrame(A.raf)
  const loop = t => {
    drawViz(t)
    if (A.el && !A.el.paused && V.kind === 'audio') A.raf = requestAnimationFrame(loop)
  }
  A.raf = requestAnimationFrame(loop)
}
function stopViz() { cancelAnimationFrame(A.raf) }

function drawViz(t) {
  const c = A.canvas
  if (!c || !c.isConnected) return
  const dpr = devicePixelRatio || 1
  const w = c.clientWidth, hgt = c.clientHeight
  if (!w || !hgt) return
  if (c.width !== Math.round(w * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(hgt * dpr) }
  const ctx = c.getContext('2d')
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, w, hgt)
  const bars = 40, gap = 4
  const bw = Math.max(2, (w - gap * (bars - 1)) / bars)
  const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#ff8fab'
  const idle = getComputedStyle(document.documentElement).getPropertyValue('--line-2').trim() || '#444'
  const playing = A.el && !A.el.paused
  if (playing && A.analyser) A.analyser.getByteFrequencyData(A.data)
  for (let i = 0; i < bars; i++) {
    let v
    if (playing && A.analyser) {
      const bin = A.data[Math.min(A.data.length - 1, Math.floor((i / bars) ** 1.4 * A.data.length * 0.85))]
      v = bin / 255
    } else if (playing) {
      v = 0.25 + 0.2 * Math.sin(t / 260 + i * 0.6) + 0.15 * Math.sin(t / 130 + i * 1.7)
    } else v = 0.06
    const bh = Math.max(3, v * hgt)
    ctx.fillStyle = playing ? accent : idle
    ctx.globalAlpha = playing ? 0.35 + 0.65 * v : 1
    const x = i * (bw + gap)
    ctx.beginPath()
    ctx.roundRect ? ctx.roundRect(x, (hgt - bh) / 2, bw, bh, 2) : ctx.rect(x, (hgt - bh) / 2, bw, bh)
    ctx.fill()
  }
  ctx.globalAlpha = 1
}

/* ---------------------------------------------------------- media controls */

function updatePlayButton() {
  const m = V.media
  const playing = m && !m.paused && !m.ended
  setIcon($('#playBtn'), playing ? 'pause' : 'play')
  $('#playBtn').setAttribute('aria-label', playing ? 'Pause' : 'Play')
}

function setSeek(time, duration) {
  const seek = $('#seek')
  const d = Number.isFinite(duration) ? duration : 0
  seek.value = d ? Math.round((time / d) * 1000) : 0
  seek.style.setProperty('--fill', `${d ? (time / d) * 100 : 0}%`)
  $('#timeNow').textContent = fmtTime(time)
  $('#timeTotal').textContent = fmtTime(d)
}

function updateVolumeUi() {
  const m = V.media
  const vol = $('#volume')
  const muted = m ? m.muted : S.prefs.muted
  const value = Math.round((m ? m.volume : S.prefs.volume) * 100)
  vol.value = muted ? 0 : value
  vol.style.setProperty('--fill', `${muted ? 0 : value}%`)
  setIcon($('#muteBtn'), muted || value === 0 ? 'mute' : 'volume')
}

function togglePlay() {
  const m = V.media
  if (!m || !m.src) return
  if (m.paused || m.ended) m.play().catch(() => toast("Playback was blocked. Click play once to allow it."))
  else m.pause()
}

function stopPlayback() {
  const m = V.media
  if (!m) return
  m.pause()
  try { m.currentTime = 0 } catch { /* not seekable yet */ }
  setSeek(0, m.duration)
}

function seekBy(seconds) {
  const m = V.media
  if (!m || !Number.isFinite(m.duration)) return
  m.currentTime = clamp(m.currentTime + seconds, 0, m.duration)
}

function toggleMute() {
  const m = V.media
  if (m) m.muted = !m.muted
  else { S.prefs.muted = !S.prefs.muted; savePrefs(); updateVolumeUi() }
}

/* ============================================================ empty states */

function renderEmpty() {
  const m = S.meta || {}
  if (!m.source) { setState(welcome()); return }
  const st = m.stats || {}
  if (!S.files.length) {
    const sorted = (st.moved || 0) + (st.deleted || 0)
    setState(stateBox(sorted ? {
      title: 'All sorted.',
      text: `${st.moved || 0} moved${st.deleted ? `, ${st.deleted} sent to the Recycle Bin` : ''} this session. Nice.`,
      actions: [h('button', { class: 'btn primary', type: 'button', onclick: pickSource }, icon('folder'), 'Open another folder'), h('button', { class: 'btn', type: 'button', onclick: rescan }, icon('refresh'), 'Rescan')],
    } : {
      title: 'No media here.',
      text: m.recursive ? 'No photos, videos or audio in this folder or its subfolders.' : 'No photos, videos or audio directly in this folder. Subfolders are skipped unless you include them.',
      actions: [
        h('button', { class: 'btn primary', type: 'button', onclick: pickSource }, icon('folder'), 'Choose another folder'),
        !m.recursive && h('button', { class: 'btn', type: 'button', onclick: () => setServerSetting('include_subfolders', true) }, 'Include subfolders'),
      ].filter(Boolean),
    }))
    return
  }
  const what = { all: 'files', image: 'photos', video: 'videos', audio: 'audio files' }[S.filter]
  setState(stateBox({
    title: 'No matches.',
    text: S.query ? `No ${what} with "${S.query}" in the name.` : `No ${what} in this folder.`,
    actions: [h('button', { class: 'btn', type: 'button', onclick: clearFilters }, icon('x'), 'Clear filters')],
  }))
}

function welcome() {
  const recent = (S.meta?.recent || []).slice(0, 5)
  const keys = [['←', '→', 'browse'], ['1', '0', 'move'], ['Ctrl', 'Z', 'undo'], ['?', '', 'all shortcuts']]
  return h('div', { class: 'welcome' }, h('div', { class: 'welcome-inner' },
    h('img', { class: 'welcome-mascot', src: '/mascot.png', alt: '' }),
    h('h1', { html: 'pick a folder.<br>sort it <em>fast.</em>' }),
    h('p', { class: 'lede' }, 'Open a folder of photos, videos or audio, add the folders you sort into, then press a number key to move each file. Every move is logged and can be undone.'),
    h('div', { class: 'row' },
      h('button', { class: 'btn primary lg', type: 'button', onclick: pickSource }, icon('folder'), 'Choose folder', h('kbd', { class: 'key' }, 'L')),
      S.caps.native && h('button', { class: 'btn ghost lg', type: 'button', onclick: () => pickSource(true) }, 'Browse in app'),
    ),
    recent.length ? h('div', { class: 'recent' },
      h('p', { class: 'label mono recent-title' }, 'recent folders'),
      ...recent.map(p => h('button', { type: 'button', onclick: () => openSource(p), title: p }, icon('folder', 'ic sm'), h('span', { class: 'rn' }, baseName(p)), h('span', { class: 'rp' }, p))),
    ) : null,
    h('div', { class: 'keys-strip' }, ...keys.map(([a, b, label]) => h('span', {}, h('kbd', { class: 'key' }, a), b ? h('kbd', { class: 'key' }, b) : null, label))),
  ))
}

function clearFilters(render = true) {
  S.filter = 'all'
  S.query = ''
  $('#search').value = ''
  if (render) { rebuildView(); renderTop(); renderFilm(true); show() }
}

/* ================================================================= actions */

async function pickFolders({ multi, title, inApp = false }) {
  if (S.caps.native && !inApp) {
    try {
      const r = await POST('/api/native/folders', { multi, title, initial: S.meta?.source || '' })
      return r.paths
    } catch (e) {
      if (e.status === 409) return null
      if (e.status !== 501) { fail(e); return null }
    }
  }
  return browseFolders({ title, multi })
}

async function pickSource(inApp = false) {
  const paths = await pickFolders({ multi: false, title: 'Choose the folder to sort', inApp: inApp === true })
  if (paths?.length) openSource(paths[0])
}

async function openSource(path) {
  try {
    const r = await POST('/api/source', { path })
    S.curPath = null; S.cur = 0
    clearFilters(false)
    await loadState()
    toast(`Opened <b>${esc(baseName(path))}</b> · ${r.count} file${r.count === 1 ? '' : 's'}`)
  } catch (e) { fail(e) }
}

async function rescan() {
  if (!S.meta?.source) return
  try {
    const r = await POST('/api/rescan')
    await sync()
    toast(`Rescanned · ${r.count} files`)
  } catch (e) { fail(e) }
}

async function addDestinations(inApp = false) {
  const paths = await pickFolders({ multi: true, title: 'Add destination folders (Ctrl/Shift to pick several)', inApp: inApp === true })
  if (!paths?.length) return
  try {
    const r = await POST('/api/dest/add', { paths })
    await sync()
    if (r.added.length) toast(`Added ${r.added.length === 1 ? `<b>${esc(baseName(r.added[0]))}</b>` : `${r.added.length} folders`}`, { kind: 'ok' })
    for (const s of r.skipped) toast(`${esc(baseName(s.path))}: ${esc(s.reason)}`, { kind: 'err' })
  } catch (e) { fail(e) }
}

async function setServerSetting(key, value) {
  try {
    const r = await POST('/api/settings', { [key]: value })
    S.settings = r.settings
    if (key === 'include_subfolders') await sync()
  } catch (e) { fail(e) }
}

function optimisticRemove(f) {
  const idx = S.cur
  removeLocal(f)
  S.curPath = null
  S.cur = idx
  rebuildView()
  if (S.cur >= S.view.length) S.cur = S.view.length - 1
  S.curPath = S.view[S.cur]?.p ?? null
  V.dir = 1
  renderTop(); renderCounter()
  ghostOut()
  V.el = null
  show()
}

function restoreLocal(f) {
  addLocal(f)
  S.curPath = f.p
  rebuildView()
  renderTop()
  V.file = null
  show()
}

async function moveTo(i) {
  const d = S.meta?.destinations?.[i]
  const f = current()
  if (!d) { if (S.meta) toast(`No destination on that key yet. Press <b>A</b> to add one, or <b>G</b> to pick from all.`, { key: 'sort' }); return }
  if (!f) return
  if (!d.exists) { toast(`<b>${esc(d.label || d.name)}</b> can't be found. Is the drive connected?`, { kind: 'err' }); return }
  flashTile(i)
  optimisticRemove(f)
  try {
    const r = await POST('/api/move', { path: f.p, dest: d.path })
    const label = esc(d.label || d.name)
    toast(`Moved <b>${esc(f.n)}</b> → ${label}${r.renamed ? ` as <b>${esc(baseName(r.to))}</b>` : ''}`, { key: 'sort', action: { label: 'Undo', run: undo } })
    if (r.warning) toast(esc(r.warning), { kind: 'err' })
  } catch (e) {
    restoreLocal(f)
    fail(e)
  }
  sync()
}

async function undo() {
  try {
    const r = await POST('/api/undo')
    await sync()
    if (!goToPath(r.restored)) {
      if (S.byPath.has(r.restored)) { clearFilters(); goToPath(r.restored) }
    }
    toast(`Restored <b>${esc(r.name)}</b>`, { kind: 'ok', key: 'sort' })
  } catch (e) {
    toast(esc(e.message), { kind: e.status === 409 ? 'info' : 'err' })
  }
}

async function deleteCurrent() {
  const f = current()
  if (!f) return
  if (S.settings.confirm_delete) {
    const answer = await confirm_({
      title: 'recycle bin',
      heading: 'Move to Recycle Bin?',
      text: `"${f.n}" goes to the Recycle Bin on ${S.caps.local ? 'this computer' : 'the computer running Fair Sorting'}. You can restore it from there; Fair Sorting can't undo this.`,
      ok: 'Move to Recycle Bin',
      danger: true,
      check: "Don't ask again",
    })
    if (!answer) return
    if (answer.checked) setServerSetting('confirm_delete', false)
  }
  optimisticRemove(f)
  try {
    await POST('/api/delete', { path: f.p })
    toast(`<b>${esc(f.n)}</b> is in the Recycle Bin`, { key: 'sort' })
  } catch (e) {
    restoreLocal(f)
    fail(e)
  }
  sync()
}

async function renameCurrent() {
  const f = current()
  if (!f) return
  const dot = f.n.lastIndexOf('.')
  const value = await prompt_({ title: 'rename', heading: 'Rename file', value: f.n, select: [0, dot > 0 ? dot : f.n.length], ok: 'Rename', text: 'The extension is kept if you leave it out. Ctrl+Z undoes a rename too.' })
  if (value == null || value === f.n) return
  try {
    const r = await POST('/api/rename', { path: f.p, name: value })
    await sync()
    goToPath(r.path)
    toast(`Renamed to <b>${esc(r.name)}</b>`, { kind: 'ok', action: { label: 'Undo', run: undo } })
  } catch (e) { fail(e) }
}

function openExternal() {
  const f = current()
  if (f && S.caps.local) POST('/api/open', { path: f.p }).catch(fail)
}
function revealCurrent() {
  const f = current()
  if (f && S.caps.local) POST('/api/reveal', { path: f.p }).catch(fail)
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text) } catch {
    const ta = h('textarea', { style: { position: 'fixed', opacity: '0' } }, text)
    document.body.append(ta); ta.select(); document.execCommand('copy'); ta.remove()
  }
  toast('Copied')
}

function toggleImmersive(force) {
  const a = app()
  const on = force ?? !a.hasAttribute('data-immersive')
  if (on === a.hasAttribute('data-immersive')) return
  a.toggleAttribute('data-immersive', on)
  if (S.caps.desktop) {
    if (on !== S.fullscreen) { S.fullscreen = on; POST('/api/window/fullscreen').catch(() => {}) }
  } else if (on && document.fullscreenEnabled && !document.fullscreenElement) {
    document.documentElement.requestFullscreen().catch(() => {})
  } else if (!on && document.fullscreenElement) {
    document.exitFullscreen().catch(() => {})
  }
  armIdle()
  setTimeout(() => { layoutMedia(); renderFilm(true) }, 60)
  setTimeout(layoutMedia, 320)
}

let idleTimer = 0
function armIdle() {
  const a = app()
  a.removeAttribute('data-idle')
  clearTimeout(idleTimer)
  idleTimer = setTimeout(() => {
    const playing = V.media && !V.media.paused && V.kind === 'video'
    const hovering = $('#controls').matches(':hover') || (a.hasAttribute('data-immersive') && $('#deck').matches(':hover'))
    if ((a.hasAttribute('data-immersive') || playing) && !hovering && !S.modals.length) a.setAttribute('data-idle', '')
  }, 2200)
}

/* =================================================================== menus */

function closeMenu() {
  if (!S.menu) return
  S.menu.node.remove()
  S.menu.cleanup()
  S.menu.anchor?.focus?.({ preventScroll: true })
  S.menu = null
}

function openMenu(anchor, items) {
  closeMenu()
  const menu = h('div', { class: 'menu', role: 'menu' })
  for (const item of items) {
    if (!item) continue
    if (item === '-') { menu.append(h('div', { class: 'menu-sep' })); continue }
    if (item.head) { menu.append(h('div', { class: 'menu-head' }, h('b', {}, item.head), item.sub ? h('span', {}, item.sub) : null)); continue }
    if (item.section) { menu.append(h('div', { class: 'menu-label label mono' }, item.section)); continue }
    const row = h('button', { class: `menu-item${item.danger ? ' danger' : ''}${item.mono ? ' mono' : ''}`, type: 'button', role: 'menuitem', title: item.title || null },
      item.check !== undefined ? h('span', { class: 'check' }, item.check ? icon('check', 'ic sm') : null) : item.icon ? icon(item.icon) : null,
      h('span', { class: 'grow' }, item.label),
      item.hint ? h('kbd', { class: 'key' }, item.hint) : null,
    )
    if (item.remove) {
      const x = h('span', { class: 'x', role: 'button', 'aria-label': 'Remove', title: 'Remove from list' }, icon('x', 'ic sm'))
      x.addEventListener('click', e => { e.stopPropagation(); item.remove(); row.remove() })
      row.append(x)
    }
    row.addEventListener('click', () => { closeMenu(); item.run?.() })
    menu.append(row)
  }
  document.body.append(menu)
  const mr = menu.getBoundingClientRect()
  let x, y
  if (anchor instanceof Element) {
    const r = anchor.getBoundingClientRect()
    x = r.left; y = r.bottom + 6
    if (y + mr.height > innerHeight - 8) y = r.top - mr.height - 6
    if (x + mr.width > innerWidth - 8) x = r.right - mr.width
  } else {
    x = anchor.x; y = anchor.y
    if (y + mr.height > innerHeight - 8) y = anchor.y - mr.height
    if (x + mr.width > innerWidth - 8) x = anchor.x - mr.width
  }
  menu.style.left = `${clamp(x, 8, innerWidth - mr.width - 8)}px`
  menu.style.top = `${clamp(y, 8, innerHeight - mr.height - 8)}px`
  const outside = e => { if (!menu.contains(e.target)) closeMenu() }
  const keys = e => {
    const rows = $$('.menu-item', menu)
    const at = rows.indexOf(document.activeElement)
    if (e.key === 'ArrowDown') { e.preventDefault(); rows[(at + 1) % rows.length]?.focus() }
    else if (e.key === 'ArrowUp') { e.preventDefault(); rows[(at - 1 + rows.length) % rows.length]?.focus() }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeMenu() }
    else if (e.key === 'Tab') closeMenu()
  }
  setTimeout(() => document.addEventListener('pointerdown', outside, true))
  menu.addEventListener('keydown', keys)
  addEventListener('resize', closeMenu, { once: true })
  S.menu = { node: menu, anchor: anchor instanceof Element ? anchor : null, cleanup: () => document.removeEventListener('pointerdown', outside, true) }
  $('.menu-item', menu)?.focus({ preventScroll: true })
}

function sourceMenu() {
  const m = S.meta || {}
  const recent = (m.recent || []).filter(p => p !== m.source)
  openMenu($('#sourceBtn'), [
    m.source ? { head: baseName(m.source), sub: m.source } : null,
    { icon: 'folder', label: m.source ? 'Choose another folder…' : 'Choose folder…', hint: 'L', run: pickSource },
    S.caps.native && { icon: 'search', label: 'Browse in app…', run: () => pickSource(true) },
    m.source && { icon: 'refresh', label: 'Rescan folder', hint: 'F5', run: rescan },
    m.source && { check: !!m.recursive, label: 'Include subfolders', run: () => setServerSetting('include_subfolders', !m.recursive) },
    m.source && S.caps.local && { icon: 'reveal', label: 'Show in Explorer', run: () => POST('/api/reveal', { path: m.source }).catch(fail) },
    recent.length ? '-' : null,
    recent.length ? { section: 'recent' } : null,
    ...recent.map(p => ({ icon: 'folder', label: p, mono: true, title: p, run: () => openSource(p), remove: () => POST('/api/recent/forget', { path: p }).then(sync).catch(fail) })),
  ])
}

function sortMenu() {
  const p = S.prefs
  const setKey = key => () => { p.sortKey = key; savePrefs(); rebuildView(); renderFilm(true); show() }
  const setDir = desc => () => { p.sortDesc = desc; savePrefs(); rebuildView(); renderFilm(true); show() }
  openMenu($('#sortBtn'), [
    { section: 'sort by' },
    { check: p.sortKey === 'name', label: 'Name', run: setKey('name') },
    { check: p.sortKey === 'date', label: 'Date modified', run: setKey('date') },
    { check: p.sortKey === 'size', label: 'Size', run: setKey('size') },
    { check: p.sortKey === 'type', label: 'Type', run: setKey('type') },
    '-',
    { check: !p.sortDesc, label: p.sortKey === 'date' ? 'Oldest first' : p.sortKey === 'size' ? 'Smallest first' : 'A → Z', run: setDir(false) },
    { check: p.sortDesc, label: p.sortKey === 'date' ? 'Newest first' : p.sortKey === 'size' ? 'Largest first' : 'Z → A', run: setDir(true) },
  ])
}

function stageMenu(e) {
  const f = current()
  if (!f) return
  e.preventDefault()
  openMenu({ x: e.clientX, y: e.clientY }, [
    { head: f.n, sub: f.p },
    S.caps.local && { icon: 'external', label: 'Open in default app', hint: 'O', run: openExternal },
    S.caps.local && { icon: 'reveal', label: 'Show in folder', hint: 'E', run: revealCurrent },
    { icon: 'pen', label: 'Rename…', hint: 'F2', run: renameCurrent },
    { icon: 'copy', label: 'Copy path', run: () => copyText(f.p) },
    f.k !== 'audio' && { icon: 'rotate', label: 'Rotate view', hint: 'R', run: rotate },
    '-',
    { icon: 'trash', label: 'Move to Recycle Bin', hint: 'Del', danger: true, run: deleteCurrent },
  ])
}

/* ================================================================== modals */

function modal({ title, body, foot = [], wide = false, onClose = null }) {
  const layer = $('#layer')
  const scrim = h('div', { class: 'scrim' })
  const closeBtn = h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Close' }, icon('x'))
  const bodyEl = h('div', { class: 'modal-body' }, body)
  const footEl = h('div', { class: 'modal-foot' }, foot)
  const dlg = h('div', { class: `modal${wide ? ' wide' : ''}`, role: 'dialog', 'aria-modal': 'true', 'aria-label': title, tabindex: '-1' },
    h('div', { class: 'modal-bar' }, icon('spark', 'ic spark'), h('span', {}, title), closeBtn), bodyEl, footEl)
  const previous = document.activeElement
  const entry = {
    dlg, body: bodyEl, foot: footEl,
    close: value => {
      scrim.remove(); dlg.remove()
      S.modals = S.modals.filter(x => x !== entry)
      onClose?.(value)
      previous?.focus?.({ preventScroll: true })
    },
  }
  closeBtn.onclick = () => entry.close(null)
  scrim.onclick = () => entry.close(null)
  dlg.addEventListener('keydown', e => {
    if (e.key !== 'Tab') return
    const focusable = $$('button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])', dlg).filter(x => x.offsetParent !== null)
    if (!focusable.length) return
    const first = focusable[0], last = focusable[focusable.length - 1]
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
  })
  layer.append(scrim, dlg)
  S.modals.push(entry)
  closeMenu()
  requestAnimationFrame(() => ($('[autofocus]', dlg) || $('.modal-foot .btn.primary', dlg) || dlg).focus())
  return entry
}

function prompt_({ title, heading, text, value = '', ok = 'OK', select = null }) {
  return new Promise(resolve => {
    const input = h('input', { class: 'input', value, autofocus: true, spellcheck: 'false' })
    const okBtn = h('button', { class: 'btn primary', type: 'submit' }, ok)
    const form = h('form', { class: 'field' }, input)
    const m = modal({
      title,
      body: [h('h2', {}, heading), text ? h('p', {}, text) : null, form],
      foot: [h('button', { class: 'btn ghost', type: 'button', onclick: () => m.close(null) }, 'Cancel'), okBtn],
      onClose: v => resolve(v ?? null),
    })
    okBtn.onclick = () => m.close(input.value.trim())
    form.onsubmit = e => { e.preventDefault(); m.close(input.value.trim()) }
    requestAnimationFrame(() => { input.focus(); if (select) input.setSelectionRange(...select); else input.select() })
  })
}

function confirm_({ title, heading, text, ok = 'OK', danger = false, check = null }) {
  return new Promise(resolve => {
    const box = check ? h('input', { type: 'checkbox' }) : null
    const okBtn = h('button', { class: `btn ${danger ? 'danger solid' : 'primary'}`, type: 'button' }, ok)
    const m = modal({
      title,
      body: [h('h2', {}, heading), h('p', {}, text)],
      foot: [
        check ? h('label', { class: 'left' }, box, check) : null,
        h('button', { class: 'btn ghost', type: 'button', onclick: () => m.close(null) }, 'Cancel'),
        okBtn,
      ],
      onClose: v => resolve(v),
    })
    okBtn.onclick = () => m.close({ checked: !!box?.checked })
    requestAnimationFrame(() => okBtn.focus())
  })
}

function browseFolders({ title = 'Choose a folder', multi = false }) {
  return new Promise(resolve => {
    let path = S.meta?.source || ''
    const picked = new Set()
    const pathInput = h('input', { class: 'input mono', placeholder: 'Type or paste a folder path', spellcheck: 'false' })
    const roots = h('div', { class: 'fb-roots' })
    const info = h('div', { class: 'fb-info' })
    const list = h('div', { class: 'fb-list', role: 'list' })
    const primary = h('button', { class: 'btn primary', type: 'button' }, multi ? 'Add this folder' : 'Use this folder')
    const newBtn = h('button', { class: 'btn ghost', type: 'button' }, icon('folder-plus'), 'New folder')
    const m = modal({
      title: multi ? 'add destinations' : 'choose folder',
      wide: true,
      body: [
        h('h2', {}, title),
        h('form', { class: 'fb-path', onsubmit: e => { e.preventDefault(); load(pathInput.value.trim()) } }, pathInput, h('button', { class: 'btn', type: 'submit' }, 'Go')),
        roots, info, list,
      ],
      foot: [h('div', { class: 'left' }, newBtn), h('button', { class: 'btn ghost', type: 'button', onclick: () => m.close(null) }, 'Cancel'), primary],
      onClose: v => resolve(v),
    })
    const updatePrimary = () => {
      if (multi && picked.size) primary.textContent = `Add ${picked.size} folder${picked.size > 1 ? 's' : ''}`
      else primary.textContent = multi ? 'Add this folder' : 'Use this folder'
      primary.disabled = !path && !picked.size
    }
    primary.onclick = () => m.close(multi && picked.size ? [...picked] : path ? [path] : null)
    newBtn.onclick = async () => {
      if (!path) { toast('Open a folder first, then create the new one inside it.'); return }
      const name = await prompt_({ title: 'new folder', heading: 'New folder', text: `Created inside ${path}`, ok: 'Create' })
      if (!name) return
      try {
        const r = await POST('/api/mkdir', { parent: path, name })
        if (multi) picked.add(r.path)
        await load(path)
      } catch (e) { fail(e) }
    }
    async function load(target) {
      let r
      try { r = await GET('/api/browse', { path: target || '' }) } catch (e) { fail(e); return }
      path = r.path
      pathInput.value = r.path
      roots.replaceChildren(...[
        r.parent ? h('button', { class: 'btn', type: 'button', onclick: () => load(r.parent) }, icon('up', 'ic sm'), 'Up') : null,
        ...(r.places || []).map(p => h('button', { class: 'btn', type: 'button', onclick: () => load(p.path) }, icon('folder', 'ic sm'), p.name)),
        ...r.drives.map(d => h('button', { class: 'btn', type: 'button', onclick: () => load(d) }, icon('drive', 'ic sm'), d.replace(/\\$/, ''))),
      ].filter(Boolean))
      info.textContent = r.path ? `${r.dirs.length} folder${r.dirs.length === 1 ? '' : 's'} · ${r.media} media file${r.media === 1 ? '' : 's'} here` : 'Pick a place to start'
      if (!r.dirs.length) list.replaceChildren(h('div', { class: 'fb-empty' }, r.path ? 'No subfolders here.' : 'Choose a drive or place above.'))
      else list.replaceChildren(...r.dirs.map(d => {
        const row = h('div', { class: 'fb-row', role: 'listitem' })
        if (multi) {
          const cb = h('input', { type: 'checkbox', 'aria-label': `Select ${d.name}` })
          cb.checked = picked.has(d.path)
          cb.onchange = () => { cb.checked ? picked.add(d.path) : picked.delete(d.path); updatePrimary() }
          row.append(cb)
        }
        row.append(h('button', { class: 'open', type: 'button', onclick: () => load(d.path), title: d.path }, icon('folder', 'ic sm'), h('span', {}, d.name)))
        return row
      }))
      list.scrollTop = 0
      updatePrimary()
    }
    load(path)
  })
}

function settingRow(title, sub, control) {
  return h('div', { class: 'set-row' }, h('div', { class: 'txt' }, h('b', {}, title), sub ? h('small', {}, sub) : null), control)
}

function switchCtl(checked, onchange, label) {
  const input = h('input', { type: 'checkbox', 'aria-label': label })
  input.checked = checked
  input.onchange = () => onchange(input.checked)
  return h('label', { class: 'switch' }, input, h('i'))
}

function segCtl(options, value, onpick) {
  const seg = h('div', { class: 'seg', role: 'radiogroup' })
  for (const [val, text] of options) {
    const b = h('button', { type: 'button', role: 'radio', 'aria-checked': String(val === value), class: val === value ? 'on' : '' }, text)
    b.onclick = () => { $$('button', seg).forEach(x => { x.classList.remove('on'); x.setAttribute('aria-checked', 'false') }); b.classList.add('on'); b.setAttribute('aria-checked', 'true'); onpick(val) }
    seg.append(b)
  }
  return seg
}

async function openSettings() {
  const caps = S.caps, st = S.settings, p = S.prefs
  const cacheText = h('small', {}, 'Thumbnails and converted previews')
  const ffmpegRow = settingRow('FFmpeg', caps.ffmpeg ? 'Found. Videos, thumbnails and odd formats are covered.' : 'Not found. AVI/WMV playback and video thumbnails need it.',
    caps.ffmpeg ? h('span', { class: 'status-ok mono' }, 'ready')
      : caps.can_install_ffmpeg ? h('button', { class: 'btn', type: 'button', onclick: installFfmpeg }, 'Install') : h('span', { class: 'status-bad mono' }, 'missing'))
  const body = [
    h('section', { class: 'set-section' }, h('p', { class: 'label mono' }, 'look'),
      settingRow('Theme', null, segCtl([['dark', 'Night'], ['light', 'Day'], ['system', 'System']], p.theme, v => { p.theme = v; savePrefs(); applyTheme() })),
      settingRow('Sort keys position', 'Press C to cycle', segCtl([['bottom', 'Bottom'], ['left', 'Left'], ['right', 'Right']], p.dock, v => { p.dock = v; savePrefs(); applyLayout(); renderDeck() })),
      settingRow('Filmstrip', 'Thumbnails of every file under the viewer', switchCtl(p.film, v => { p.film = v; savePrefs(); applyLayout() }, 'Filmstrip')),
      settingRow('Details panel', 'Press H to hide panels while sorting', switchCtl(p.chrome === 'full', v => { p.chrome = v ? 'full' : 'min'; savePrefs(); applyLayout() }, 'Details panel')),
    ),
    h('section', { class: 'set-section' }, h('p', { class: 'label mono' }, 'sorting'),
      settingRow('Include subfolders', 'Destination folders inside the source are skipped', switchCtl(!!st.include_subfolders, v => setServerSetting('include_subfolders', v), 'Include subfolders')),
      settingRow('Ask before deleting', 'Confirm before sending a file to the Recycle Bin', switchCtl(!!st.confirm_delete, v => setServerSetting('confirm_delete', v), 'Ask before deleting')),
      settingRow('Autoplay', 'Start videos and audio when you land on them', switchCtl(!!st.autoplay, v => setServerSetting('autoplay', v), 'Autoplay')),
      settingRow('Always convert video & audio', 'Use when files play without picture or sound. Slower to start.', switchCtl(!!st.always_convert, v => { setServerSetting('always_convert', v); V.file = null; setTimeout(show, 100) }, 'Always convert')),
    ),
    h('section', { class: 'set-section' }, h('p', { class: 'label mono' }, 'media engine'),
      ffmpegRow,
      settingRow('HEIC / HEIF photos', caps.heif ? 'iPhone and Samsung photos open directly' : 'Install pillow-heif to open them', h('span', { class: `${caps.heif ? 'status-ok' : 'status-bad'} mono` }, caps.heif ? 'on' : 'off')),
      settingRow('Preview cache', null, caps.local ? h('button', { class: 'btn', type: 'button', onclick: async () => { try { const r = await POST('/api/cache/clear'); cacheText.textContent = `${fmtBytes(r.size)} used`; toast('Cache cleared') } catch (e) { fail(e) } } }, 'Clear') : null),
    ),
  ]
  body[2].lastChild.querySelector('.txt').append(cacheText)
  modal({
    title: 'settings',
    wide: true,
    body,
    foot: [h('div', { class: 'left mono' }, `fair sorting ${S.boot?.version || ''} · ${S.boot?.mode === 'web' ? 'web' : caps.desktop ? 'desktop' : 'browser'}`), h('button', { class: 'btn', type: 'button', onclick: () => { S.modals.at(-1)?.close(); showShortcuts() } }, icon('keyboard'), 'Keyboard shortcuts')],
  })
  GET('/api/cache').then(r => { cacheText.textContent = `${fmtBytes(r.size)} of thumbnails and converted previews` }).catch(() => {})
}

async function installFfmpeg() {
  try {
    await POST('/api/ffmpeg/install')
    toast('Downloading FFmpeg (about 90 MB)…', { timeout: 8000 })
    for (;;) {
      await sleep(1500)
      const s = await GET('/api/ffmpeg/status')
      if (s.status === 'done') { S.caps.ffmpeg = true; toast(esc(s.message), { kind: 'ok' }); break }
      if (s.status === 'error') { toast(esc(s.message), { kind: 'err', timeout: 8000 }); break }
    }
  } catch (e) { fail(e) }
}

const SHORTCUTS = [
  ['sorting', [['1 – 0', 'Move to a destination (active bank)'], ['Shift + 1 – 0', 'Destinations in the next bank'], ['[ ]', 'Switch key bank (11–20, 21–30…)'], ['G', 'Move to any destination by name or number'], ['Ctrl + Z', 'Undo (repeat to go further back)'], ['Del', 'Move to Recycle Bin'], ['F2', 'Rename']]],
  ['browse', [['← →', 'Previous / next'], ['Home End', 'First / last'], ['PgUp PgDn', 'Jump 10 files'], ['/', 'Search names'], ['F5', 'Rescan folder']]],
  ['view', [['+ −', 'Zoom in / out'], ['Ctrl + 0', 'Reset zoom'], ['R', 'Rotate view'], ['F', 'Focus mode'], ['H', 'Hide side panels'], ['T', 'Night / day'], ['C', 'Move the sort keys']]],
  ['playback', [['Space  P', 'Play / pause'], ['S', 'Stop'], ['J K', 'Back / forward 5 s'], ['M', 'Mute'], ['O', 'Open in default app'], ['E', 'Show in folder']]],
  ['workspace', [['L', 'Choose source folder'], ['A', 'Add destination folders'], ['?', 'This list'], ['Esc', 'Close / leave focus mode']]],
]

function showShortcuts() {
  modal({
    title: 'keyboard',
    wide: true,
    body: [h('div', { class: 'keys-grid' }, ...SHORTCUTS.map(([section, rows]) => h('section', {},
      h('h4', { class: 'label mono' }, section),
      h('ul', {}, ...rows.map(([keys, what]) => h('li', {}, h('span', {}, what), h('span', {}, ...keys.split(/\s+/).filter(k => k !== '+').map(k => h('kbd', { class: 'key' }, k)))))),
    )))],
  })
}

async function openShare() {
  if (!S.caps.share) return
  const body = h('div', { style: { display: 'grid', gap: '16px' } })
  const m = modal({ title: 'share on wi-fi', wide: true, body })
  const render = st => {
    S.lan = st
    renderTop()
    const portInput = h('input', { class: 'input mono', value: String(st.port), inputmode: 'numeric', style: { width: '96px' }, 'aria-label': 'Port' })
    if (!st.running) {
      body.replaceChildren(
        h('h2', {}, 'Sort from your phone'),
        h('p', {}, 'Open the same workspace in any browser on your Wi-Fi: a phone on the couch, a laptop, a tablet. They see the same folders, keys and undo history.'),
        h('div', { class: 'row', style: { display: 'flex', gap: '8px', alignItems: 'center' } },
          h('label', { class: 'mono dim', style: { fontSize: '12px' } }, 'port'), portInput,
          h('button', { class: 'btn primary', type: 'button', onclick: async () => { try { render(await POST('/api/lan/start', { port: Number(portInput.value) })) } catch (e) { fail(e) } } }, icon('wifi'), 'Start sharing')),
        h('p', { class: 'note' }, 'Windows may ask whether Fair Sorting can use the network the first time. Allow it on private networks.'),
      )
      return
    }
    const url = st.urls[0] || ''
    body.replaceChildren(
      h('div', { class: 'share-grid' },
        h('div', { style: { display: 'grid', gap: '14px' } },
          h('h2', {}, 'Sharing is on'),
          h('p', {}, 'Scan the code, or open this address on a device on the same Wi-Fi:'),
          url ? h('div', { class: 'url-row' }, h('code', {}, url), h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Copy address', onclick: () => copyText(url) }, icon('copy'))) : null,
          st.urls.length > 1 ? h('details', { class: 'note' }, h('summary', {}, `Doesn't open? Try another address (${st.urls.length - 1})`),
            h('div', { class: 'mono', style: { display: 'grid', gap: '4px', marginTop: '8px', userSelect: 'all' } }, ...st.urls.slice(1).map(u => h('span', {}, u)))) : null,
          !st.urls.length ? h('p', { class: 'note' }, 'No network address found. Are you connected to Wi-Fi?') : null,
          h('div', {}, h('p', { class: 'label mono' }, 'pin'), h('div', { class: 'pin' }, st.pin)),
        ),
        st.qr ? h('div', { class: 'qr', html: st.qr, 'aria-label': `QR code for ${url}` }) : null,
      ),
      h('p', { class: 'note warn' }, 'Anyone with the PIN can move, rename and delete files in the folders you sort. Turn sharing off when you are done.'),
      h('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } },
        !st.fixed ? h('button', { class: 'btn danger', type: 'button', onclick: async () => { try { render(await POST('/api/lan/stop')) } catch (e) { fail(e) } } }, 'Stop sharing') : null,
        h('button', { class: 'btn ghost', type: 'button', onclick: async () => { try { render(await POST('/api/lan/pin')); toast('New PIN. Other devices need to enter it again.') } catch (e) { fail(e) } } }, icon('refresh'), 'New PIN'),
      ),
    )
  }
  try { render(await GET('/api/lan')) } catch (e) { fail(e); m.close() }
}

/* ================================================================== drawer */

const D = { tab: 'session', log: null, open: null, q: '' }

function openDrawer(tab) {
  const drawer = $('#drawer')
  if (tab) D.tab = tab
  drawer.hidden = false
  renderDrawer(true)
}
function closeDrawer() { $('#drawer').hidden = true }

async function renderDrawer(fetchLog = true) {
  for (const b of $$('#drawer [role="tab"]')) b.setAttribute('aria-selected', String(b.dataset.tab === D.tab))
  const body = $('#drawerBody')
  if (D.tab === 'session') {
    const items = S.meta?.history || []
    const undo = S.meta?.undo || { count: 0 }
    body.replaceChildren(
      h('div', { class: 'drawer-tools' }, h('div', { class: 'row' },
        h('button', { class: 'btn', type: 'button', disabled: !undo.count, onclick: undo_ }, icon('undo'), undo.count ? `Undo ${undo.label}` : 'Nothing to undo'))),
      items.length ? h('div', { class: 'hist' }, ...items.map(histRow)) : h('div', { class: 'drawer-empty' }, h('b', {}, 'nothing yet'), 'Moves, renames and deletes from this session show up here.'),
    )
    return
  }
  if (fetchLog || !D.log) await loadLog(0)
  else drawLog()
}
const undo_ = () => undo()

function histRow(item) {
  const iconName = { move: 'right', delete: 'trash', rename: 'pen' }[item.type] || 'history'
  const sub = item.type === 'move' ? h('span', {}, '→ ', h('span', { class: 'to' }, item.dest)) : item.type === 'delete' ? 'recycle bin' : `renamed from ${baseName(item.src)}`
  return h('div', { class: `hist-item${item.undone ? ' undone' : ''}` },
    h('span', { class: 'hist-ico' }, icon(iconName, 'ic sm')),
    h('span', { class: 'hist-main' }, h('span', { class: 'hist-name', title: item.name }, item.name), h('span', { class: 'hist-sub' }, item.undone ? 'undone' : sub)),
    h('span', { class: 'hist-meta' }, ago(item.time)),
  )
}

async function loadLog(offset) {
  try {
    const r = await GET('/api/log', { offset, limit: 60, q: D.q })
    if (offset && D.log) { D.log.items.push(...r.items); D.log.matches = r.matches } else D.log = r
    drawLog()
  } catch (e) { fail(e) }
}

function drawLog() {
  const body = $('#drawerBody')
  const r = D.log
  if (!r) return
  const search = h('input', { class: 'input', type: 'search', placeholder: 'Filter this log', value: D.q, spellcheck: 'false' })
  search.addEventListener('input', debounce(() => { D.q = search.value.trim(); loadLog(0).then(() => { const s = $('#drawerBody input[type="search"]'); s?.focus(); s?.setSelectionRange(s.value.length, s.value.length) }) }, 220))
  const tools = h('div', { class: 'drawer-tools' },
    h('div', { class: 'row' }, h('span', { class: 'logfile-name', title: r.logfile }, r.logfile ? baseName(r.logfile) : 'no log yet'), h('span', { class: 'mono dim', style: { fontSize: '11px' } }, `${r.total} moves`)),
    h('div', { class: 'row' },
      h('button', { class: 'btn', type: 'button', onclick: chooseLog }, icon('folder', 'ic sm'), 'Choose…'),
      h('button', { class: 'btn ghost', type: 'button', onclick: () => POST('/api/log/latest').then(() => { sync(); loadLog(0); toast('Using the newest log') }).catch(fail) }, 'Latest'),
      h('button', { class: 'btn ghost', type: 'button', onclick: () => POST('/api/log/new').then(() => { sync(); loadLog(0); toast('New log started') }).catch(fail) }, icon('plus', 'ic sm'), 'New'),
    ),
    r.total > 8 ? search : null,
  )
  const rows = r.items.map(item => {
    const open = D.open === item.i
    const row = h('button', { class: `hist-item${item.exists ? '' : ' missing'}${open ? ' open' : ''}`, type: 'button', 'aria-expanded': String(open) },
      h('span', { class: 'hist-ico mono', style: { fontSize: '10px' } }, `#${item.n}`),
      h('span', { class: 'hist-main' }, h('span', { class: 'hist-name', title: item.name }, item.name), h('span', { class: 'hist-sub' }, item.exists ? h('span', {}, '→ ', h('span', { class: 'to' }, item.folder)) : 'moved file is missing')),
      h('span', { class: 'hist-meta' }, icon(open ? 'down' : 'right', 'ic sm')),
    )
    row.onclick = () => { D.open = open ? null : item.i; drawLog() }
    if (!open) return row
    const thumb = item.exists && /\.(jpe?g|png|gif|bmp|webp|heic|heif|avif|tiff?|mp4|mov|mkv|webm|avi|m4v|3gp|wmv)$/i.test(item.dst)
      ? h('img', { alt: '', src: `/api/thumb?s=512&p=${enc(item.dst)}`, onerror: e => e.target.remove() }) : null
    const detail = h('div', { class: 'hist-detail' }, thumb,
      h('dl', {}, h('dt', {}, 'from'), h('dd', {}, item.src), h('dt', {}, 'to'), h('dd', {}, item.dst)),
      h('div', { class: 'row' },
        h('button', { class: 'btn', type: 'button', disabled: !item.exists, onclick: () => undoLog(item) }, icon('undo', 'ic sm'), 'Move it back'),
        S.caps.local && item.exists ? h('button', { class: 'btn ghost', type: 'button', onclick: () => POST('/api/reveal', { path: item.dst }).catch(fail) }, icon('reveal', 'ic sm'), 'Show') : null,
        S.caps.local && item.exists ? h('button', { class: 'btn ghost', type: 'button', onclick: () => POST('/api/open', { path: item.dst }).catch(fail) }, icon('external', 'ic sm'), 'Open') : null,
      ))
    return h('div', {}, row, detail)
  })
  const list = rows.length ? h('div', { class: 'hist' }, ...rows)
    : h('div', { class: 'drawer-empty' }, h('b', {}, D.q ? 'no matches' : 'empty log'), D.q ? 'Nothing in this log matches.' : 'Moves are written here as you sort, one line per file.')
  const more = r.items.length < r.matches ? h('button', { class: 'btn ghost wide', type: 'button', onclick: () => loadLog(r.items.length) }, `Show more (${r.matches - r.items.length})`) : null
  const hadFocus = document.activeElement?.matches?.('#drawerBody input[type="search"]')
  body.replaceChildren(tools, list, more || '')
  if (hadFocus) search.focus()
}

async function undoLog(item) {
  try {
    const r = await POST('/api/log/undo', { index: item.i, dst: item.dst })
    D.open = null
    await sync()
    await loadLog(0)
    if (!goToPath(r.restored) && S.byPath.has(r.restored)) { clearFilters(); goToPath(r.restored) }
    toast(`Moved <b>${esc(r.name)}</b> back`, { kind: 'ok' })
  } catch (e) { fail(e) }
}

async function chooseLog() {
  if (S.caps.native) {
    try {
      const r = await POST('/api/native/file')
      if (r.path) { await POST('/api/log/select', { path: r.path }); await sync(); await loadLog(0); toast(`Using <b>${esc(baseName(r.path))}</b>`) }
      return
    } catch (e) { if (e.status !== 501) { fail(e); return } }
  }
  try {
    const { logs } = await GET('/api/logs')
    if (!logs.length) { toast('No move_log files found near this folder.'); return }
    const m = modal({
      title: 'move logs',
      body: [h('h2', {}, 'Choose a log'), h('div', { class: 'hist' }, ...logs.map(l => h('button', { class: `hist-item${l.current ? ' open' : ''}`, type: 'button', onclick: async () => { m.close(); try { await POST('/api/log/select', { path: l.path }); await sync(); await loadLog(0) } catch (e) { fail(e) } } },
        h('span', { class: 'hist-ico' }, icon('history', 'ic sm')),
        h('span', { class: 'hist-main' }, h('span', { class: 'hist-name' }, l.name), h('span', { class: 'hist-sub' }, l.path)),
        h('span', { class: 'hist-meta' }, fmtBytes(l.size)))))],
    })
  } catch (e) { fail(e) }
}

/* ===================================================================== gate */

function showGate() {
  S.started = false
  $('#app').hidden = true
  $('#gate').hidden = false
  if (new URLSearchParams(location.search).has('wrong')) $('#gateError').textContent = "That PIN didn't work. Check the number on the computer."
  history.replaceState(null, '', '/')
  setTimeout(() => $('#gatePin').focus(), 50)
}

function setupGate() {
  const form = $('#gateForm'), input = $('#gatePin'), error = $('#gateError')
  input.addEventListener('input', () => { input.value = input.value.replace(/\D/g, '').slice(0, 6); error.textContent = ''; if (input.value.length === 6) form.requestSubmit() })
  form.addEventListener('submit', async e => {
    e.preventDefault()
    try {
      await POST('/api/auth', { pin: input.value })
      $('#gate').hidden = true
      await start()
    } catch (err) {
      error.textContent = err.message
      input.select()
    }
  })
}

/* ================================================================ keyboard */

function handleEscape(e) {
  if (!$('#tip').hidden) $('#tip').hidden = true
  if (S.menu) { closeMenu(); return }
  if (S.modals.length) { S.modals.at(-1).close(null); return }
  if (!$('#drawer').hidden) { closeDrawer(); return }
  if (app().hasAttribute('data-sheet')) { app().removeAttribute('data-sheet'); return }
  if (app().hasAttribute('data-immersive')) { toggleImmersive(false); return }
  if (document.activeElement?.id === 'search') { document.activeElement.blur(); return }
  if (S.prefs.chrome === 'min') { toggleChrome(); return }
  e.preventDefault()
}

function onKey(e) {
  if (!S.started) return
  if (e.key === 'Escape') { handleEscape(e); return }
  if (S.modals.length || S.menu) return
  const t = e.target
  const typing = t instanceof HTMLElement && (t.isContentEditable || (t.matches('input, textarea, select') && !['range', 'checkbox', 'radio', 'button'].includes(t.type)))
  if (typing) {
    if (e.key === 'Enter' && t.id === 'search') { t.blur(); $('#stage').focus({ preventScroll: true }) }
    if (e.key === 'ArrowDown' && t.id === 'search') { t.blur() }
    return
  }
  const k = e.key
  const ctrl = e.ctrlKey || e.metaKey
  const lower = k.length === 1 ? k.toLowerCase() : k
  if (ctrl && lower === 'z') { e.preventDefault(); undo(); return }
  if (ctrl && k === '0') { e.preventDefault(); resetZoom(); return }
  if (ctrl && lower === 'f') { e.preventDefault(); $('#search').focus(); return }
  if ((ctrl && lower === 'r') || k === 'F5') { e.preventDefault(); rescan(); return }
  if (ctrl || e.altKey) return
  // e.code keeps Shift+digit usable on every layout; e.key covers virtual keyboards without scan codes.
  const digit = /^(?:Digit|Numpad)(\d)$/.exec(e.code) || (/^\d$/.test(e.key) ? [e.key, e.key] : null)
  if (digit) {
    const d = Number(digit[1])
    e.preventDefault()
    moveTo((S.bank + (e.shiftKey ? 1 : 0)) * BANK + (d === 0 ? 9 : d - 1))
    return
  }
  const actions = {
    ArrowRight: next, ArrowLeft: prev,
    Home: () => go(0, -1), End: () => go(S.view.length - 1, 1),
    PageDown: () => go(Math.min(S.cur + 10, S.view.length - 1), 1), PageUp: () => go(Math.max(S.cur - 10, 0), -1),
    ' ': togglePlay, p: togglePlay, s: stopPlayback, j: () => seekBy(-5), k: () => seekBy(5), m: toggleMute,
    o: openExternal, e: revealCurrent, l: () => pickSource(), a: () => addDestinations(),
    '+': () => zoomStep(1), '=': () => zoomStep(1), '-': () => zoomStep(-1), _: () => zoomStep(-1),
    r: rotate, h: toggleChrome, f: () => toggleImmersive(), t: toggleTheme, c: cycleDock,
    Delete: deleteCurrent, F2: renameCurrent, '/': () => $('#search').focus(), '?': showShortcuts,
    i: () => app().toggleAttribute('data-sheet'),
    g: openDestPicker, '[': () => switchBank(-1), ']': () => switchBank(1),
  }
  const run = actions[lower] || actions[k]
  if (!run) return
  e.preventDefault()
  armIdle()
  run()
}

/* ============================================================ stage input */

function setupStageInput() {
  const stage = $('#stage'), wrap = $('#mediaWrap')
  const pointers = new Map()
  let pan = null, pinch = null, swipe = null
  const center = e => { const r = stage.getBoundingClientRect(); return { x: e.clientX - r.left - r.width / 2, y: e.clientY - r.top - r.height / 2 } }

  stage.addEventListener('wheel', e => {
    if (!V.el || V.kind === 'audio') return
    e.preventDefault()
    const c = center(e)
    const factor = Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0018))
    setZoom(V.zoom * factor, c.x, c.y)
  }, { passive: false })

  wrap.addEventListener('dblclick', e => {
    if (!V.el || V.kind === 'audio') return
    const c = center(e)
    if (V.zoom > 100) resetZoom()
    else setZoom(200, c.x, c.y)
  })

  wrap.addEventListener('pointerdown', e => {
    if (e.button !== 0 || !V.file) return
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
    wrap.setPointerCapture(e.pointerId)
    if (pointers.size === 2 && V.el && V.kind !== 'audio') {
      const [a, b] = [...pointers.values()]
      pinch = { dist: Math.hypot(a.x - b.x, a.y - b.y), zoom: V.zoom }
      pan = null; swipe = null
      return
    }
    if (V.zoom > 100) { pan = { x: e.clientX, y: e.clientY, px: V.panX, py: V.panY }; wrap.classList.add('grabbing') }
    else swipe = { x: e.clientX, y: e.clientY, t: performance.now(), type: e.pointerType }
  })
  wrap.addEventListener('pointermove', e => {
    if (!pointers.has(e.pointerId)) return
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
    if (pinch && pointers.size === 2) {
      const [a, b] = [...pointers.values()]
      setZoom(pinch.zoom * (Math.hypot(a.x - b.x, a.y - b.y) / pinch.dist))
      return
    }
    if (pan) {
      V.panX = pan.px + (e.clientX - pan.x)
      V.panY = pan.py + (e.clientY - pan.y)
      clampPan(); applyTransform()
    }
  })
  const end = e => {
    pointers.delete(e.pointerId)
    wrap.classList.remove('grabbing')
    if (pointers.size < 2) pinch = null
    if (swipe && pointers.size === 0) {
      const dx = e.clientX - swipe.x, dy = e.clientY - swipe.y, dt = performance.now() - swipe.t
      if (swipe.type !== 'mouse' && Math.abs(dx) > 55 && Math.abs(dx) > Math.abs(dy) * 1.4 && dt < 700) (dx < 0 ? next : prev)()
      else if (swipe.type !== 'mouse' && Math.abs(dx) < 8 && Math.abs(dy) < 8 && V.kind !== 'image' && e.target.closest('.media-el, .audio-card')) togglePlay()
      else if (swipe.type === 'mouse' && Math.abs(dx) < 4 && Math.abs(dy) < 4 && V.kind === 'video') togglePlay()
    }
    if (!pointers.size) { pan = null; swipe = null }
  }
  wrap.addEventListener('pointerup', end)
  wrap.addEventListener('pointercancel', end)
  stage.addEventListener('contextmenu', stageMenu)
  stage.addEventListener('pointerdown', () => app().removeAttribute('data-sheet'))

  new ResizeObserver(() => requestAnimationFrame(() => { layoutMedia(); if (V.kind === 'audio') drawViz(performance.now()) })).observe(stage)
}

/* ==================================================================== wiring */

let wired = false
function wire() {
  if (wired) return
  wired = true
  setupTips()
  setupStageInput()
  document.addEventListener('keydown', onKey)
  const gesture = () => { S.gestured = true; if (A.ctx?.state === 'suspended') A.ctx.resume() }
  document.addEventListener('pointerdown', gesture, true)
  document.addEventListener('keydown', gesture, true)
  for (const type of ['pointermove', 'pointerdown', 'keydown']) document.addEventListener(type, armIdle, { passive: true })
  document.addEventListener('click', e => {
    const b = e.target.closest?.('button')
    if (b && e.detail > 0 && !b.closest('.modal, .menu, #drawer, .gate')) b.blur()
  })

  $('#sourceBtn').onclick = sourceMenu
  $('#sortBtn').onclick = sortMenu
  $('#undoBtn').onclick = () => undo()
  $('#historyBtn').onclick = () => ($('#drawer').hidden ? openDrawer() : closeDrawer())
  $('#drawerClose').onclick = closeDrawer
  $('#shareBtn').onclick = openShare
  $('#settingsBtn').onclick = openSettings
  $('#infoBtn').onclick = () => app().toggleAttribute('data-sheet')
  $('#deckFind').onclick = openDestPicker
  for (const b of $$('#drawer [role="tab"]')) b.onclick = () => { D.tab = b.dataset.tab; renderDrawer(true) }
  for (const b of $$('#filters button')) b.onclick = () => { S.filter = b.dataset.kind; rebuildView(); renderTop(); renderFilm(true); show() }
  const search = $('#search')
  search.addEventListener('input', debounce(() => { S.query = search.value; rebuildView(); renderTop(); renderFilm(true); show() }, 120))

  $('#prevBtn').onclick = prev
  $('#nextBtn').onclick = next
  $('#playBtn').onclick = togglePlay
  $('#muteBtn').onclick = toggleMute
  $('#zoomInBtn').onclick = () => zoomStep(1)
  $('#zoomOutBtn').onclick = () => zoomStep(-1)
  $('#zoomRead').onclick = resetZoom
  $('#rotateBtn').onclick = rotate
  $('#immersiveBtn').onclick = () => toggleImmersive()
  $('#fixBtn').onclick = () => { if (V.file) convert(V.file, V.token, true) }
  $('#renameBtn').onclick = renameCurrent
  $('#deleteBtn').onclick = deleteCurrent
  $('#openBtn').onclick = openExternal
  $('#revealBtn').onclick = revealCurrent

  const seek = $('#seek')
  seek.addEventListener('input', () => {
    const m = V.media
    V.seeking = true
    if (!m || !Number.isFinite(m.duration)) return
    const t = (seek.value / 1000) * m.duration
    seek.style.setProperty('--fill', `${seek.value / 10}%`)
    $('#timeNow').textContent = fmtTime(t)
    m.currentTime = t
  })
  seek.addEventListener('change', () => { V.seeking = false })
  const vol = $('#volume')
  vol.addEventListener('input', () => {
    const m = V.media
    const v = vol.value / 100
    if (m) { m.volume = v; m.muted = v === 0 } else { S.prefs.volume = v; savePrefs() }
    vol.style.setProperty('--fill', `${vol.value}%`)
  })

  const film = $('#film')
  film.addEventListener('scroll', () => { cancelAnimationFrame(filmRaf); filmRaf = requestAnimationFrame(() => renderFilm(false)) }, { passive: true })
  film.addEventListener('wheel', e => { if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) { e.preventDefault(); film.scrollLeft += e.deltaY } }, { passive: false })
  addEventListener('resize', debounce(() => renderFilm(true), 120))

  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (S.prefs.theme === 'system') applyTheme() })
  document.addEventListener('fullscreenchange', () => { if (!document.fullscreenElement && app().hasAttribute('data-immersive') && !S.caps.desktop) toggleImmersive(false) })
  document.addEventListener('visibilitychange', () => { if (!document.hidden) sync() })

  setInterval(() => { if (!document.hidden) sync() }, 2500)
  const ping = () => POST('/api/ping').catch(() => {})
  ping()
  setInterval(ping, 15000)
  setInterval(renderSession, 30000)
}

async function start() {
  const boot = await GET('/api/boot')
  S.boot = boot
  S.caps = boot.caps
  S.settings = boot.settings
  S.started = true
  history.replaceState(null, '', '/')
  $('#gate').hidden = true
  $('#app').hidden = false
  applyTheme()
  applyLayout()
  wire()
  await loadState()
  if (S.caps.share) GET('/api/lan').then(st => { S.lan = st; renderTop() }).catch(() => {})
}

async function boot() {
  document.documentElement.dataset.theme = S.prefs.theme === 'light' ? 'light' : S.prefs.theme === 'system' && !matchMedia('(prefers-color-scheme: dark)').matches ? 'light' : 'dark'
  setupGate()
  let hello
  try { hello = await GET('/api/hello') } catch {
    document.body.append(h('div', { class: 'gate' }, h('div', { class: 'gate-card' }, h('h1', { class: 'gate-title display' }, 'offline'), h('p', { class: 'gate-copy' }, "Fair Sorting isn't running anymore. Start it again on the computer, then reload this page."))))
    return
  }
  if (!hello.authed) { showGate(); return }
  try { await start() } catch (e) { if (e.status !== 401) fail(e) }
}

boot()
