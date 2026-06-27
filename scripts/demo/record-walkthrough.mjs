// =============================================================================
// Playwright "director" — records a captioned video walkthrough of the
// Provider Prior Authorization app against a live (or local) deployment.
//
//   cd scripts/demo && npm i
//   TARGET_URL=https://<frontend-fqdn>/ node record-walkthrough.mjs
//
// Output (OUTDIR, default ../../docs/videos):
//   _raw/walkthrough.webm   — the master recording (gitignored)
//   _raw/chapters.json      — chapter -> wall-clock offset map (for ffmpeg)
// Captions/cursor/highlights are injected overlays; scene text comes from
// scenes.mjs (shared with gen-script.mjs so video and script never drift).
// =============================================================================
import { chromium } from "playwright";
import { fileURLToPath } from "url";
import fs from "fs";
import path from "path";
import { TARGET_URL, CHAPTERS, SCENES, scene, chapter } from "./scenes.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..", "..");
const OUTDIR = process.env.OUTDIR || path.join(repoRoot, "docs", "videos");
const RAWDIR = path.join(OUTDIR, "_raw");
const DLDIR = path.join(OUTDIR, "_downloads");
const [VW, VH] = (process.env.VIEWPORT || "1280x800").split("x").map(Number);
for (const d of [OUTDIR, RAWDIR, DLDIR]) fs.mkdirSync(d, { recursive: true });

const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- Injected overlay: caption bar, simulated cursor, chapter card, highlight
const OVERLAY = `(() => {
  const ID='__demoOverlay';
  const ready=(fn)=>{ if(document.body) fn(); else document.addEventListener('DOMContentLoaded',fn); };
  ready(()=>{
    if(document.getElementById(ID)) return;
    const tag=document.createElement('div'); tag.id=ID; tag.style.display='none'; document.body.appendChild(tag);
    const style=document.createElement('style');
    style.textContent=\`
      #__cap{position:fixed;left:50%;bottom:40px;transform:translateX(-50%);max-width:980px;
        background:rgba(16,28,34,.93);color:#fff;font:500 25px/1.45 system-ui,'Segoe UI',sans-serif;
        padding:16px 28px;border-radius:13px;z-index:2147483640;opacity:0;transition:opacity .35s ease;
        box-shadow:0 12px 34px rgba(0,0,0,.4);text-align:center;pointer-events:none;border:1px solid rgba(255,255,255,.12)}
      #__cap.show{opacity:1}
      #__cursor{position:fixed;left:-100px;top:-100px;width:24px;height:24px;border-radius:50%;
        background:rgba(12,110,119,.30);border:2.5px solid #0C6E77;z-index:2147483647;pointer-events:none;
        transform:translate(-50%,-50%);transition:width .12s,height .12s;box-shadow:0 0 0 5px rgba(12,110,119,.10)}
      #__chapter{position:fixed;inset:0;z-index:2147483646;display:flex;flex-direction:column;align-items:center;
        justify-content:center;gap:16px;background:linear-gradient(135deg,#0C6E77,#0a474e);color:#fff;opacity:0;
        transition:opacity .5s ease;pointer-events:none;text-align:center;padding:40px}
      #__chapter.show{opacity:1}
      #__chapter .ct{font:600 56px/1.1 'Iowan Old Style','Palatino Linotype',Palatino,Georgia,serif;letter-spacing:-.01em;max-width:1000px}
      #__chapter .cs{font:400 23px/1.45 system-ui,'Segoe UI',sans-serif;color:#cdeff1;max-width:780px}
      .__hl{outline:3px solid #B26A07 !important;outline-offset:3px !important;box-shadow:0 0 0 6px rgba(178,106,7,.18) !important;border-radius:6px}
    \`;
    document.head.appendChild(style);
    const cap=document.createElement('div'); cap.id='__cap'; document.body.appendChild(cap);
    const cur=document.createElement('div'); cur.id='__cursor'; document.body.appendChild(cur);
    const ch=document.createElement('div'); ch.id='__chapter'; ch.innerHTML='<div class="ct"></div><div class="cs"></div>'; document.body.appendChild(ch);
    window.addEventListener('mousemove',(e)=>{cur.style.left=e.clientX+'px';cur.style.top=e.clientY+'px';},{passive:true});
    window.__cap=(t)=>{ if(!t){cap.classList.remove('show');return;} cap.textContent=t; cap.classList.add('show'); };
    window.__chapter=(title,sub)=>{ if(!title){ch.classList.remove('show');return;} ch.querySelector('.ct').textContent=title; ch.querySelector('.cs').textContent=sub||''; ch.classList.add('show'); };
    window.__clickPulse=()=>{ cur.style.width='15px';cur.style.height='15px'; setTimeout(()=>{cur.style.width='24px';cur.style.height='24px';},130); };
    window.__hl=(sel)=>{ try{const el=typeof sel==='string'?document.querySelector(sel):sel; if(el){el.classList.add('__hl'); setTimeout(()=>el.classList.remove('__hl'),1700);}}catch(e){} };
  });
})();`;

async function main() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: VW, height: VH },
    recordVideo: { dir: RAWDIR, size: { width: VW, height: VH } },
    acceptDownloads: true,
  });
  await context.addInitScript(OVERLAY);
  const page = await context.newPage();

  const t0 = Date.now();
  const chapters = [];
  let lastPos = { x: VW / 2, y: VH / 2 };

  const cap = (t) => page.evaluate((x) => window.__cap(x), t).catch(() => {});
  const say = async (id) => { const s = scene(id); await cap(s.caption); await sleep(s.holdMs); };
  const hl = (sel) => page.evaluate((s) => window.__hl(s), sel).catch(() => {});
  async function card(key) {
    const c = chapter(key);
    chapters.push({ key: c.key, title: c.title, atMs: Date.now() - t0 });
    await cap("");
    await page.evaluate(([t, s]) => window.__chapter(t, s), [c.title, c.sub]);
    await sleep(key === "intro" || key === "close" ? 4200 : 2600);
    await page.evaluate(() => window.__chapter(null));
    await sleep(450);
  }
  async function scrollTo(locator) {
    try { await locator.scrollIntoViewIfNeeded({ timeout: 8000 }); await sleep(500); } catch {}
  }
  async function moveTo(locator) {
    const box = await locator.boundingBox();
    if (!box) return;
    const x = box.x + Math.min(box.width / 2, box.width <= 260 ? box.width / 2 : 120);
    const y = box.y + box.height / 2;
    const steps = 16;
    for (let i = 1; i <= steps; i++) {
      await page.mouse.move(lastPos.x + (x - lastPos.x) * i / steps, lastPos.y + (y - lastPos.y) * i / steps);
      await sleep(11);
    }
    lastPos = { x, y };
  }
  async function click(locator) {
    await scrollTo(locator);
    await moveTo(locator);
    await page.evaluate(() => window.__clickPulse && window.__clickPulse()).catch(() => {});
    await sleep(120);
    await locator.click({ timeout: 15000 });
  }
  async function showText(re) { // scroll a heading/text into center
    try { const l = page.getByText(re).first(); await scrollTo(l); await hl(undefined); } catch {}
  }

  log("target:", TARGET_URL);
  await page.goto(TARGET_URL, { waitUntil: "networkidle", timeout: 60000 });
  await page.mouse.move(VW / 2, VH / 2);
  await sleep(1500);

  // ---- Intro ----
  await card("intro");
  await say("intro");

  // ---- 1 · Build the packet ----
  await card("intake");
  await say("intake-overview");
  // open sample picker
  const picker = page.locator("button").filter({ hasText: /Pulmonology|Choose a sample case/ }).first();
  await click(picker);
  await say("intake-sample");
  await click(page.getByRole("option", { name: /Orthopedics/ }));
  await sleep(300);
  await click(page.getByRole("button", { name: "Load Sample" }));
  await sleep(1200);
  await say("intake-loaded");
  // advanced toggle (only if collapsed)
  try {
    const t = page.locator("button").filter({ hasText: "EHR/FHIR" }).first();
    if (await t.count()) { const lbl = (await t.innerText()).trim(); if (/Show/i.test(lbl)) await click(t); }
  } catch {}
  await showText(/Advanced provider intake/i);
  await say("intake-advanced");
  await showText(/Diagnosis Codes/i);
  await say("intake-codes");

  // ---- 2 · Run the assessment ----
  await card("assess");
  const assess = page.getByRole("button", { name: /Assess Prior Auth Packet/ });
  await click(assess);
  await say("assess-click");
  // rotate phase captions while the ~90s run proceeds
  const resultsTab = page.getByRole("tab", { name: /Clinical Evidence/ });
  let resultsReady = false;
  resultsTab.waitFor({ state: "visible", timeout: 220000 }).then(() => { resultsReady = true; }).catch(() => {});
  for (const p of ["assess-preflight", "assess-phase1", "assess-phase2", "assess-phase3"]) {
    if (resultsReady) break;
    try { await page.getByText(/Pre-flight|Phase 1|Phase 2|Phase 3|in progress/i).first().scrollIntoViewIfNeeded({ timeout: 3000 }); } catch {}
    await say(p);
  }
  if (!resultsReady) await cap("Finalizing the readiness assessment…");
  await resultsTab.waitFor({ state: "visible", timeout: 220000 });
  await sleep(3500);

  // ---- 3 · The verdict ----
  await card("verdict");
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" }));
  await sleep(800);
  await showText(/Submission Readiness Assessment/i); await say("verdict");
  await showText(/Verification Checks/i); await say("verify-checks");
  await showText(/Payer Policy Requirements/i); await say("requirements");
  await showText(/Documentation (Gaps|Action Required)/i); await say("gaps");
  await showText(/Payer Policy References/i); await say("policy-refs");
  await showText(/Clinical Evidence Rationale/i); await say("rationale");

  // ---- 4 · Inside each reviewer ----
  await card("reviewers");
  const agentTab = (re) => page.getByRole("tab", { name: re });
  await click(agentTab(/Doc\.?\s*Completeness/)); await sleep(800);
  await showText(/Compliance Checks Performed|Documentation Checklist/i); await say("agent-doc");
  await click(agentTab(/Clinical Evidence/)); await sleep(800);
  await showText(/Diagnosis Validation/i); await say("agent-clinical");
  await click(agentTab(/Policy Matching/)); await sleep(800);
  await showText(/Per-Code Coverage|Provider Verification/i); await say("agent-policy");
  await click(agentTab(/Submission Readiness/)); await sleep(800);
  await showText(/Submission Readiness Gate Pipeline|Gate 1/i); await say("agent-submission");

  // ---- 5 · Human sign-off ----
  await card("signoff");
  try {
    const nameInput = page.locator("#reviewer_name");
    await scrollTo(nameInput);
    await nameInput.fill("Jane Doe, Prior Auth Specialist");
    await sleep(500);
  } catch {}
  try { await click(page.getByRole("button", { name: /Revise Assessment/ })); await sleep(700); } catch {}
  await showText(/Rationale/i);
  await say("signoff-revise");
  try { await click(page.getByRole("button", { name: /^Cancel$/ })); await sleep(600); } catch {}
  try {
    await click(page.getByRole("button", { name: /Accept AI Assessment/ }));
  } catch (e) { log("accept failed:", e.message); }
  await say("signoff-accept");
  try { await page.getByRole("button", { name: /Download Provider Letter/ }).waitFor({ state: "visible", timeout: 30000 }); } catch {}
  await showText(/Authorization Recorded|Download Provider Letter/i);
  await say("signoff-letter");
  try {
    const [dl] = await Promise.all([
      page.waitForEvent("download", { timeout: 15000 }),
      click(page.getByRole("button", { name: /Download Provider Letter/ })),
    ]);
    await dl.saveAs(path.join(DLDIR, "provider-letter.pdf"));
    log("saved provider letter");
  } catch (e) { log("provider letter dl skipped:", e.message); }

  // ---- 6 · The report ----
  await card("report");
  await showText(/Submission Readiness Report/i);
  try {
    const [dl] = await Promise.all([
      page.waitForEvent("download", { timeout: 15000 }),
      click(page.getByRole("button", { name: /Download Report/ })),
    ]);
    await dl.saveAs(path.join(DLDIR, "submission-readiness-report.pdf"));
    log("saved audit report");
  } catch (e) { log("report dl skipped:", e.message); }
  await say("report");

  // ---- 7 · Under the hood ----
  await card("underhood");
  try { await click(page.getByRole("tab", { name: /^Debug Console$/ })); await sleep(1000); } catch {}
  await say("debug-intro");
  try { await click(page.getByRole("tab", { name: /^Timeline$/ })); await sleep(800); } catch {}
  await say("debug-timeline");
  try {
    await click(page.getByRole("tab", { name: /^Events$/ })); await sleep(900);
    // open the Request sub-tab in the event inspector (best effort)
    try { await click(page.getByRole("tab", { name: /^Request$/ })); await sleep(500); } catch {}
  } catch {}
  await say("debug-events");
  try { await click(page.getByRole("tab", { name: /^Graph$/ })); await sleep(800); } catch {}
  await say("debug-graph");
  try { await click(page.getByRole("tab", { name: /^Foundry$/ })); await sleep(800); } catch {}
  await say("debug-foundry");

  // ---- Close ----
  await cap("");
  await card("close");
  await say("close");
  await cap("");
  await sleep(800);

  await context.close();
  const webm = await page.video().path();
  const finalWebm = path.join(RAWDIR, "walkthrough.webm");
  try { fs.renameSync(webm, finalWebm); } catch { fs.copyFileSync(webm, finalWebm); }
  fs.writeFileSync(path.join(RAWDIR, "chapters.json"), JSON.stringify({ durationMs: Date.now() - t0, viewport: [VW, VH], chapters }, null, 2));
  await browser.close();
  log("DONE. webm:", finalWebm);
  log("chapters:", chapters.map((c) => `${c.key}@${(c.atMs / 1000).toFixed(1)}s`).join("  "));
}

main().catch((e) => { console.error(e); process.exit(1); });
