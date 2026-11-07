#!/usr/bin/env node
// ui_shoot.mjs (ui-loop): viewport and full-page screenshots plus UI metrics for each URL, size and theme.
//
//   node ui_shoot.mjs --out DIR --url URL [--url URL ...] [--sizes 1366x768,1440x900,390x844]
//                     [--themes light,dark] [--max-screens 6] [--focus-stops 40] [--skip 'css selector']
//                     [--wait-ms 500]
//
// Playwright: set PLAYWRIGHT_MODULE to the path of playwright's index.mjs if it is not importable as "playwright".
// Each URL gets its own folder DIR/NN-<host>-<path>/ with <size>-<theme>-vNN.png (one per screenful, at most
// --max-screens from the top) and full-<size>-<theme>.png. DIR/metrics.json holds every finding, DIR/metrics.md one
// row per view. Theme = the browser's prefers-color-scheme only: an app with its own theme switch needs its own
// setting. Lazy content: the page is scrolled (bounded) and images get at most 5 s. Images still loading are counted.
// Exit 0 when every view was captured, 1 when a view failed (navigation error, HTTP status >= 400 or an error while
// measuring, recorded in its row, the other views still run), 2 on bad arguments. Page CSP is bypassed so the
// metrics script can be injected.
import { mkdirSync, writeFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const args = process.argv.slice(2)
const bad = msg => { console.error(`ui_shoot: ${msg}`); process.exit(2) }
const opt = (name, def) => {
  const i = args.indexOf(name)
  if (i < 0) return def
  if (i + 1 >= args.length || args[i + 1].startsWith('--')) bad(`${name} needs a value`)
  return args[i + 1]
}
const urls = args.flatMap((a, i) => (a === '--url' ? [args[i + 1]] : []))
urls.forEach(u => { if (!u || u.startsWith('--')) bad('--url needs a value') })
const out = opt('--out')
if (!out || !urls.length) bad('usage: ui_shoot.mjs --out DIR --url URL [--url URL ...] [--sizes WxH,...] [--themes light,dark]')
const sizes = opt('--sizes', '1366x768,1440x900,390x844').split(',').map(s => {
  const m = s.trim().match(/^(\d+)x(\d+)$/)
  if (!m || Number(m[1]) < 200 || Number(m[2]) < 200) bad(`bad size ${s} (use WxH, each at least 200)`)
  return [Number(m[1]), Number(m[2])]
})
const themes = opt('--themes', 'light,dark').split(',').map(t => t.trim())
themes.forEach(t => { if (!['light', 'dark'].includes(t)) bad(`bad theme ${t}`) })
const int = (name, def, min, max) => {
  const v = Number(opt(name, def))
  if (!Number.isInteger(v) || v < min || v > max) bad(`${name} must be a whole number from ${min} to ${max}`)
  return v
}
const maxScreens = int('--max-screens', '6', 1, 100)
const focusStops = int('--focus-stops', '40', 0, 1000)
const skip = opt('--skip', '')
const waitMs = int('--wait-ms', '500', 0, 60000)
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const metricsJs = join(dirname(fileURLToPath(import.meta.url)), 'ui_metrics.js')

const folder = (u, i) => {
  let host = '', path = u
  try { const x = new URL(u); host = x.host; path = x.pathname + x.search + x.hash } catch {}
  const s = (host + '-' + path).replace(/[^A-Za-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 80) || 'page'
  return `${String(i + 1).padStart(2, '0')}-${s}`
}
mkdirSync(out, { recursive: true })
const all = []
const rows = ['| view | size | theme | status | console errors | images still loading | text < bar | edges < 3 | taps < 44 | text < 12px | sideways px | clipped | unnamed | focus stops without ring |', '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
let failed = 0
const browser = await chromium.launch()
const save = () => {
  writeFileSync(join(out, 'metrics.json'), JSON.stringify(all, null, 1))
  writeFileSync(join(out, 'metrics.md'), rows.join('\n') + '\n')
}
try {
  for (const [ui, url] of urls.entries()) {
    const name = folder(url, ui)
    mkdirSync(join(out, name), { recursive: true })
    for (const [w, h] of sizes) {
      for (const theme of themes) {
        const tag = `${w}x${h}-${theme}`
        const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: theme, deviceScaleFactor: 1, hasTouch: w < 600, isMobile: w < 600, bypassCSP: true })
        const page = await ctx.newPage()
        const errors = []
        page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)) })
        page.on('pageerror', e => errors.push(String(e).slice(0, 200)))
        let status = 'ok'
        try {
          const resp = await page.goto(url, { waitUntil: 'load', timeout: 60000 })
          if (resp && resp.status() >= 400) status = `HTTP ${resp.status()}`
          await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {}) // busy pages never go idle
        } catch (e) {
          status = 'navigation failed: ' + e.message.split('\n')[0].slice(0, 120)
        }
        if (status !== 'ok') {
          failed++
          all.push({ url, size: tag, status, consoleErrors: errors })
          rows.push(`| ${name} | ${w}x${h} | ${theme} | ${status} | ${errors.length} | | | | | | | | | |`)
          await ctx.close()
          continue
        }
        try {
          // Lazy content: scroll down in half screens (at most 40 steps), then wait at most 5 s for images.
          const pending = await page.evaluate(async () => {
            const H = Math.min(document.documentElement.scrollHeight, innerHeight * 20)
            for (let y = 0, n = 0; y < H && n < 40; y += innerHeight / 2, n++) { scrollTo(0, y); await new Promise(r => setTimeout(r, 60)) }
            const shown = i => i.getClientRects().length > 0 // a display:none image never loads, so it is not waited for
            const left = [...document.images].filter(i => !i.complete && shown(i))
            await Promise.race([
              Promise.all(left.map(i => new Promise(r => { i.addEventListener('load', r, { once: true }); i.addEventListener('error', r, { once: true }) }))),
              new Promise(r => setTimeout(r, 5000)),
            ])
            scrollTo(0, 0)
            return [...document.images].filter(i => !i.complete && i.getClientRects().length > 0).length
          }).catch(() => -1)
          await page.waitForTimeout(waitMs)
          const docH = await page.evaluate(() => document.documentElement.scrollHeight)
          for (let i = 0, y = 0; y < docH && i < maxScreens; i++, y += h) {
            await page.evaluate(yy => scrollTo(0, yy), y)
            await page.waitForTimeout(150)
            await page.screenshot({ path: join(out, name, `${tag}-v${String(i + 1).padStart(2, '0')}.png`) })
          }
          await page.evaluate(() => scrollTo(0, 0))
          await page.screenshot({ path: join(out, name, `full-${tag}.png`), fullPage: true })
          await page.addScriptTag({ path: metricsJs })
          const m = await page.evaluate(o => window.__uiMetrics(o), { phone: w < 600, skip })
          // Focus: record every control's unfocused style first, then Tab through with real key presses and let
          // focus transitions settle before reading the focused style.
          await page.evaluate(() => window.__uiSnapFocusables())
          let noRing = 0; const focus = []
          const first = await page.evaluate(() => window.__uiFocus()) // focused on load (autofocus): stop 0
          if (first) { focus.push(first); if (!first.indicator) noRing++ }
          for (let i = 0; i < focusStops; i++) {
            await page.keyboard.press('Tab')
            await page.waitForTimeout(250)
            const f = await page.evaluate(() => window.__uiFocus())
            if (!f) break
            focus.push(f)
            if (!f.indicator) noRing++
          }
          all.push({ ...m, url, size: tag, status, imagesStillLoading: pending, consoleErrors: errors, focus })
          rows.push(`| ${name} | ${w}x${h} | ${theme} | ok | ${errors.length} | ${pending} | ${m.text.length} | ${m.bounds.length} | ${m.taps.length} | ${m.small.length} | ${m.pageOverflow} | ${m.clipped.length} | ${m.names.length} | ${noRing} of ${focus.length} |`)
        } catch (e) {
          failed++
          status = 'error: ' + String(e.message || e).split('\n')[0].slice(0, 120)
          all.push({ url, size: tag, status, consoleErrors: errors })
          rows.push(`| ${name} | ${w}x${h} | ${theme} | ${status} | ${errors.length} | | | | | | | | | |`)
        }
        await ctx.close()
      }
    }
  }
} finally {
  save()
  await browser.close()
}
console.log(rows.join('\n'))
console.log(`shots and metrics in ${out}` + (failed ? ` (${failed} view(s) failed)` : ''))
process.exit(failed ? 1 : 0)
