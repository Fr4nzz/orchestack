// In-page UI metrics (ui-loop). Load with page.addScriptTag({ path }), then page.evaluate(o => window.__uiMetrics(o), opts).
// opts: { phone: bool, skip: 'css selector of widgets to ignore for taps and clipping, e.g. data grids' }.
// Returns contrast, tap, overflow, font, label and clipping findings. window.__uiFocus() reports the focused element's ring.
// Contrast: computed text colour against the effective background (ancestors' background-colors composited
// with alpha, up to an opaque one; an ancestor with a background-image makes the pair "unsure").
// eslint-disable-next-line no-unused-vars
window.__uiMetrics = function (opts) {
  const phone = !!opts.phone
  // Any CSS colour (rgb, oklch, color()) to 8-bit sRGB + alpha, by painting one pixel.
  const cache = window.__uiColorCache || (window.__uiColorCache = new Map())
  const cv = window.__uiCv || (window.__uiCv = Object.assign(document.createElement('canvas'), { width: 1, height: 1 }).getContext('2d', { willReadFrequently: true }))
  const parse = s => {
    if (!s || s === 'transparent' || s === 'rgba(0, 0, 0, 0)') return [0, 0, 0, 0]
    if (cache.has(s)) return cache.get(s)
    const m = s.match(/^rgba?\(([^)]+)\)$/)
    let out
    if (m) { const q = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); out = [q[0], q[1], q[2], q.length > 3 ? q[3] : 1] }
    else {
      cv.clearRect(0, 0, 1, 1); cv.fillStyle = '#000'; cv.fillStyle = s; cv.fillRect(0, 0, 1, 1)
      const d = cv.getImageData(0, 0, 1, 1).data
      out = d[3] === 0 ? [0, 0, 0, 0] : [d[0], d[1], d[2], d[3] / 255] // getImageData is not premultiplied
    }
    cache.set(s, out); return out
  }
  const over = (top, bot) => { // composite top (rgba) over opaque bot
    const a = top[3]
    return [top[0] * a + bot[0] * (1 - a), top[1] * a + bot[1] * (1 - a), top[2] * a + bot[2] * (1 - a), 1]
  }
  const lum = c => {
    const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4) }
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])
  }
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05) }
  const hex = c => '#' + c.slice(0, 3).map(v => Math.round(v).toString(16).padStart(2, '0')).join('')
  const canvasBg = (() => {
    const h = parse(getComputedStyle(document.documentElement).backgroundColor)
    const b = parse(getComputedStyle(document.body).backgroundColor)
    // the browser paints a dark canvas only when the page opts into dark (color-scheme), not just because the OS asks
    const dark = /dark/.test(getComputedStyle(document.documentElement).colorScheme || '') && window.matchMedia('(prefers-color-scheme: dark)').matches
    let base = dark && h[3] === 0 && b[3] === 0 ? [18, 18, 18, 1] : [255, 255, 255, 1]
    if (h[3] > 0) base = over(h, base)
    return base
  })()
  const bgOf = el => {
    const stack = []; let unsure = false
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const cs = getComputedStyle(e)
      if (cs.backgroundImage && cs.backgroundImage !== 'none' && !/^url\(.*\.svg/.test(cs.backgroundImage)) unsure = true
      const c = parse(cs.backgroundColor)
      const op = Number(cs.opacity)
      if (c[3] > 0) { stack.push(c); if (c[3] >= 0.999) break }
      if (e.tagName === 'IMG' || e.tagName === 'VIDEO' || e.tagName === 'CANVAS') unsure = true
    }
    let bg = canvasBg
    for (let i = stack.length - 1; i >= 0; i--) bg = over(stack[i], bg)
    return { bg, unsure }
  }
  const opacityChain = el => { let o = 1; for (let e = el; e && e.nodeType === 1; e = e.parentElement) o *= Number(getComputedStyle(e).opacity); return o }
  const visible = el => {
    const r = el.getBoundingClientRect()
    if (r.width < 1 || r.height < 1) return false
    const cs = getComputedStyle(el)
    if (cs.visibility === 'hidden' || cs.display === 'none') return false
    if (r.bottom < 0 || r.right < 0 || r.left > innerWidth + 1) return false
    return true
  }
  const desc = el => {
    let s = el.tagName.toLowerCase()
    if (el.id) s += '#' + el.id
    const cls = (typeof el.className === 'string' ? el.className : '').trim().split(/\s+/).filter(Boolean).slice(0, 4)
    if (cls.length) s += '.' + cls.join('.')
    const t = (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').trim().replace(/\s+/g, ' ').slice(0, 40)
    return t ? `${s} «${t}»` : s
  }
  const disabledOf = el => !!el.closest('[disabled],[aria-disabled="true"]') || (el.matches && el.matches(':disabled'))
  const docH = document.documentElement.scrollHeight

  // 1. Text contrast and font size
  const text = []; const small = []
  const seen = new Set()
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, { acceptNode: n => n.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT })
  let n
  while ((n = walker.nextNode())) {
    const el = n.parentElement
    if (!el || seen.has(el)) continue
    seen.add(el)
    if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'OPTION'].includes(el.tagName)) continue
    if (!visible(el)) continue
    if (el.closest('[aria-hidden="true"]') && !el.closest('button')) continue
    const cs = getComputedStyle(el)
    const fg = parse(cs.color); const { bg, unsure } = bgOf(el)
    const op = opacityChain(el)
    const fgc = over([fg[0], fg[1], fg[2], fg[3] * op], bg)
    const r = ratio(fgc, bg)
    const size = parseFloat(cs.fontSize); const weight = Number(cs.fontWeight) || 400
    const large = size >= 24 || (size >= 18.66 && weight >= 700)
    const dis = disabledOf(el)
    const need = large ? 3 : 4.5
    const rec = { el: desc(el), text: n.nodeValue.trim().slice(0, 40), fg: hex(fgc), bg: hex(bg), ratio: Math.round(r * 100) / 100, size, weight, large, disabled: dis, unsure, y: Math.round(el.getBoundingClientRect().top + scrollY) }
    if (r < need) text.push({ ...rec, need })
    if (size < 12) small.push({ el: rec.el, text: rec.text, size })
  }
  const textCount = seen.size

  // 2. Control boundaries (text fields, checkboxes, selects): border or fill vs surface >= 3
  const bounds = []
  for (const el of document.querySelectorAll('input:not([type=hidden]):not([type=button]):not([type=submit]),select,textarea')) {
    if (!visible(el)) continue
    const cs = getComputedStyle(el)
    const { bg: under } = bgOf(el.parentElement || el)
    const own = parse(cs.backgroundColor); const fill = own[3] > 0 ? over(own, under) : under
    const bw = parseFloat(cs.borderBottomWidth) || 0
    const bc = bw > 0 && cs.borderBottomStyle !== 'none' ? over(parse(cs.borderBottomColor), under) : null
    const t = el.type
    let r = Math.max(bc ? ratio(bc, under) : 1, ratio(fill, under))
    if ((t === 'checkbox' || t === 'radio') && cs.appearance !== 'none') r = 99 // native control: browser draws its own
    if (r < 3) bounds.push({ el: desc(el), border: bc ? hex(bc) : null, surface: hex(under), ratio: Math.round(r * 100) / 100, disabled: disabledOf(el) })
  }

  // 3. Interactive elements: tap size, names
  const inter = [...document.querySelectorAll('a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],[role=tab],[role=menuitem],[role=checkbox],[role=switch],[role=option],[tabindex]:not([tabindex="-1"])')].filter(visible)
  const taps = []; const names = []
  const nameOf = el => {
    const al = el.getAttribute('aria-label'); if (al && al.trim()) return al
    const lb = el.getAttribute('aria-labelledby')
    if (lb) { const t = lb.split(/\s+/).map(i => document.getElementById(i)?.innerText || '').join(' ').trim(); if (t) return t }
    if (el.labels && el.labels.length) { const t = [...el.labels].map(l => l.innerText).join(' ').trim(); if (t) return t }
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName)) { if (el.type === 'submit' || el.type === 'button') return el.value; return el.getAttribute('title') || '' }
    const t = (el.innerText || '').trim(); if (t) return t
    const img = el.querySelector('img[alt]'); if (img && img.alt.trim()) return img.alt
    const svgT = el.querySelector('svg title'); if (svgT) return svgT.textContent
    return el.getAttribute('title') || ''
  }
  for (const el of inter) {
    const r = el.getBoundingClientRect()
    const inline = el.tagName === 'A' && getComputedStyle(el).display === 'inline' && el.parentElement && (el.parentElement.innerText || '').trim().length > (el.innerText || '').trim().length + 10
    if (phone && !inline && !(opts.skip && el.closest(opts.skip))) {
      const w = Math.round(r.width), h = Math.round(r.height)
      if (w < 44 || h < 44) taps.push({ el: desc(el), w, h, under24: w < 24 || h < 24 })
    }
    const nm = nameOf(el)
    if (!nm.trim()) names.push({ el: desc(el), tag: el.tagName.toLowerCase(), type: el.type || el.getAttribute('role') || '' })
    else if (['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName) && !['submit', 'button', 'checkbox', 'radio'].includes(el.type) && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby') && !(el.labels && el.labels.length) && el.getAttribute('title')) { /* title only: ok-ish */ }
  }
  // inputs with only a placeholder
  for (const el of document.querySelectorAll('input:not([type=hidden]),select,textarea')) {
    if (!visible(el)) continue
    if (['submit', 'button'].includes(el.type)) continue
    if (!nameOf(el).trim() && el.getAttribute('placeholder')) names.push({ el: desc(el), tag: el.tagName.toLowerCase(), type: 'placeholder-only' })
  }

  // 4. Overflow and clipped text
  const sw = document.scrollingElement.scrollWidth
  const pageOverflow = sw > innerWidth + 1 ? sw - innerWidth : 0
  const wide = []
  if (pageOverflow) {
    for (const el of document.body.querySelectorAll('*')) {
      const r = el.getBoundingClientRect()
      if (r.right > innerWidth + 2 && r.width > 0 && visible(el)) {
        let inScroller = false
        for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden' || o === 'clip') { inScroller = true; break } }
        if (!inScroller) wide.push({ el: desc(el), right: Math.round(r.right) })
      }
      if (wide.length > 15) break
    }
  }
  const clipped = []
  for (const el of document.body.querySelectorAll('*')) {
    if (el.children.length > 2) continue
    if (!(el.innerText || '').trim()) continue
    if (opts.skip && el.closest(opts.skip)) continue
    const cs = getComputedStyle(el)
    if (!/(hidden|clip|ellipsis)/.test(cs.overflowX + cs.textOverflow)) continue
    if (el.scrollWidth > el.clientWidth + 1 && visible(el)) clipped.push({ el: desc(el), scroll: el.scrollWidth, client: el.clientWidth })
    if (clipped.length > 40) break
  }

  return { hash: location.hash, w: innerWidth, h: innerHeight, docH, textCount, interactive: inter.length, text, small, bounds, taps, names, pageOverflow, wide, clipped }
}

// Focus. __uiSnapFocusables() records every focusable element's unfocused style. __uiFocus() then reads the
// element that real keyboard focus is on (no synthetic blur or refocus, so no app handlers fire) and reports an
// indicator when its settled focused style differs from its recorded unfocused one.
const __uiSnap = c => [c.outlineStyle, c.outlineWidth, c.outlineColor, c.boxShadow, c.borderTopColor, c.borderBottomColor, c.backgroundColor, c.color, c.textDecorationLine].join('|')
window.__uiSnapFocusables = function () {
  const m = new WeakMap()
  for (const el of document.querySelectorAll('a[href],button,input,select,textarea,summary,[tabindex],[contenteditable]:not([contenteditable=false]),iframe,[role=button],[role=tab],[role=menuitem],[role=checkbox],[role=switch],[role=option]')) { if (el !== document.activeElement) m.set(el, __uiSnap(getComputedStyle(el))) } // focused on load: no unfocused baseline
  window.__uiUnfocused = m
}
window.__uiFocus = function () {
  const el = document.activeElement
  if (!el || el === document.body) return null
  const r = el.getBoundingClientRect()
  const cs = getComputedStyle(el)
  const ow = parseFloat(cs.outlineWidth) || 0
  const hasOutline = cs.outlineStyle !== 'none' && ow > 0
  const hasShadow = !!cs.boxShadow && cs.boxShadow !== 'none'
  const t = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 40)
  const cls = (typeof el.className === 'string' ? el.className : '').trim().split(/\s+/).slice(0, 3).join('.')
  const before = window.__uiUnfocused && window.__uiUnfocused.get(el)
  const indicator = before ? before !== __uiSnap(cs) : (hasOutline || hasShadow)
  // ring colour vs the surface behind it
  let ratio = null
  const pick = hasOutline ? cs.outlineColor : hasShadow ? (cs.boxShadow.match(/(rgba?\([^)]+\)|oklch\([^)]+\)|color\([^)]+\)|#[0-9a-f]+)/i) || [])[0] : null
  if (pick) {
    const cv = Object.assign(document.createElement('canvas'), { width: 1, height: 1 }).getContext('2d', { willReadFrequently: true })
    const toRgb = s => { if (!s || s === 'transparent') return [0, 0, 0, 0]; cv.clearRect(0, 0, 1, 1); cv.fillStyle = '#000'; cv.fillStyle = s; cv.fillRect(0, 0, 1, 1); const d = cv.getImageData(0, 0, 1, 1).data; return d[3] === 0 ? [0, 0, 0, 0] : [d[0], d[1], d[2], d[3] / 255] }
    const dark = /dark/.test(getComputedStyle(document.documentElement).colorScheme || '') && window.matchMedia('(prefers-color-scheme: dark)').matches
    let bg = dark ? [18, 18, 18, 1] : [255, 255, 255, 1]
    const stack = []
    for (let e = el.parentElement; e; e = e.parentElement) { const c = toRgb(getComputedStyle(e).backgroundColor); if (c[3] > 0) { stack.push(c); if (c[3] >= 1) break } }
    for (let i = stack.length - 1; i >= 0; i--) { const c = stack[i]; bg = [c[0] * c[3] + bg[0] * (1 - c[3]), c[1] * c[3] + bg[1] * (1 - c[3]), c[2] * c[3] + bg[2] * (1 - c[3]), 1] }
    const rc = toRgb(pick); const ring = [rc[0] * rc[3] + bg[0] * (1 - rc[3]), rc[1] * rc[3] + bg[1] * (1 - rc[3]), rc[2] * rc[3] + bg[2] * (1 - rc[3])]
    const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4) }
    const L = c => 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])
    const a = L(ring), b = L(bg); ratio = Math.round(((Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)) * 100) / 100
  }
  return { el: el.tagName.toLowerCase() + (cls ? '.' + cls : '') + (t ? ` «${t}»` : ''), inViewport: r.width > 0 && r.bottom > 0 && r.top < innerHeight, outline: hasOutline ? `${cs.outlineStyle} ${ow}px ${cs.outlineColor}` : null, shadow: hasShadow ? cs.boxShadow.slice(0, 80) : null, ratio, indicator }
}
