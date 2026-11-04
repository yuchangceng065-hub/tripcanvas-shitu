#!/usr/bin/env node
/**
 * Headless browser acceptance for the "one sentence -> confirmed trip" loop.
 *
 * Prerequisites (local only):
 *   1. API on http://127.0.0.1:8000 with a throwaway SQLite database
 *   2. `npm run dev` on http://127.0.0.1:5173
 *   3. Chrome with --remote-debugging-port=9222
 *
 * Usage: node scripts/browser-check.mjs
 * Override APP_URL / API_URL / CDP_URL / OUT_DIR when needed.
 */
import { writeFile } from 'node:fs/promises'
const CDP = process.env.CDP_URL || 'http://127.0.0.1:9222'
const APP = process.env.APP_URL || 'http://127.0.0.1:5173/'
const API = process.env.API_URL || 'http://127.0.0.1:8000/api/v1'
const OUT = process.env.OUT_DIR || '/tmp/tripcanvas-browser-check'
if (!/^https?:\/\/(127\.0\.0\.1|localhost)[:/]/.test(API) && process.env.ALLOW_REMOTE !== '1') {
  throw new Error('Refusing to create test accounts on a non-local API; set ALLOW_REMOTE=1 to override')
}
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
const targets = await (await fetch(CDP + '/json/list')).json()
const page = targets.find(target => target.type === 'page')
if (!page) throw new Error('No Chrome page target; start Chrome with --remote-debugging-port=9222')
const ws = new WebSocket(page.webSocketDebuggerUrl)
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject })
let seq = 0
const pending = new Map()
ws.onmessage = event => { const message = JSON.parse(event.data); if (message.id && pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id) } }
function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++seq
    pending.set(id, message => message.error ? reject(new Error(method + ' ' + JSON.stringify(message.error))) : resolve(message.result))
    ws.send(JSON.stringify({ id, method, params }))
  })
}
async function js(expression) {
  const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text)
  return result.result.value
}
async function waitFor(expression, timeout = 60000) {
  const end = Date.now() + timeout
  while (Date.now() < end) { if (await js(expression)) return true; await sleep(300) }
  throw new Error('Timed out waiting for: ' + expression)
}
async function screenshot(name) {
  const { data } = await send('Page.captureScreenshot', { format: 'png' })
  await writeFile(`${OUT}-${name}.png`, Buffer.from(data, 'base64'))
  console.log('screenshot', `${OUT}-${name}.png`)
}
await send('Page.enable')
await send('Runtime.enable')
await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false })
await send('Page.navigate', { url: APP })
await waitFor("!!document.querySelector('.brand')")
const username = 'browser_' + Date.now().toString(36)
const registered = await (await fetch(API + '/auth/register', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username, password: 'correct-horse-824!' }),
})).json()
if (!registered.access_token) throw new Error('Registration failed: ' + JSON.stringify(registered))
await js(`sessionStorage.setItem('tripcanvas.session', ${JSON.stringify(registered.access_token)})`)
await send('Page.reload')
await waitFor("!!document.querySelector('.brand')")
await sleep(1200)
await js(`(() => { const nav=[...document.querySelectorAll('.primary-nav button')].find(b=>b.textContent.includes('我的旅行')); nav.click(); return true })()`)
await sleep(400)
await js(`(() => { const open=[...document.querySelectorAll('button')].find(b=>b.textContent.includes('开启新旅行')); open.click(); return true })()`)
await waitFor("!!document.querySelector('.trip-builder')")
const text = '2026-11-14 去杭州两天，两个人，想去西湖和浙工大，喜欢咖啡，少走路，第二天 17:00 前到杭州东站。'
await js(`(() => { const area=document.querySelector('.trip-builder textarea'); area.value=${JSON.stringify(text)}; area.dispatchEvent(new Event('input',{bubbles:true})); return true })()`)
await js(`(() => { const parse=[...document.querySelectorAll('.trip-builder button')].find(b=>b.textContent.includes('识别需求')); parse.click(); return true })()`)
await waitFor("document.querySelectorAll('.mention-card').length>=3", 90000)
const mentions = await js("[...document.querySelectorAll('.mention-card>strong')].map(e=>e.textContent.trim())")
await screenshot('1-mentions')
await js(`(() => { for (const card of document.querySelectorAll('.mention-card')) { card.querySelector('.mention-options button')?.click() } return true })()`)
await sleep(400)
const chosen = await js("[...document.querySelectorAll('.mention-card')].map(c=>c.querySelector('button.chosen')?.textContent.trim()||'unresolved')")
await js(`(() => { const next=[...document.querySelectorAll('.trip-builder button')].find(b=>b.textContent.includes('确认需求')); next.click(); return true })()`)
await waitFor("!!document.querySelector('.requirements-summary')")
const summary = await js("document.querySelector('.requirements-summary').textContent.trim()")
await js(`(() => { const generate=[...document.querySelectorAll('.trip-builder button')].find(b=>/生成草案|生成并核验/.test(b.textContent)); generate.click(); return true })()`)
await waitFor("document.querySelectorAll('.draft-day').length>=2", 120000)
const draft = await js("[...document.querySelectorAll('.draft-day')].map(d=>d.textContent.replace(/\\s+/g,' ').trim())")
await screenshot('2-draft')
await js(`(() => { const save=[...document.querySelectorAll('.trip-builder button')].find(b=>b.textContent.includes('确认并保存')); save.click(); return true })()`)
await waitFor("!document.querySelector('.modal-backdrop')||document.querySelector('.modal-backdrop').offsetParent===null", 120000)
await sleep(700)
const toast = await js("document.querySelector('.toast-message')?.textContent.trim()||''")
await screenshot('3-saved')
const trips = await (await fetch(API + '/trips', { headers: { Authorization: 'Bearer ' + registered.access_token } })).json()
const saved = trips.find(trip => trip.days?.length === 2)
console.log(JSON.stringify({ mentions, chosen, summary, draft, toast, saved: saved ? { revision: saved.revision, days: saved.days.map(day => day.activities.length) } : null }, null, 2))
if (!saved || saved.days.length !== 2) throw new Error('The confirmed trip was not persisted')
console.log('PASS')
ws.close()
