// =============================================================================
// Renders docs/walkthrough-script.md from scenes.mjs — the timed narration
// script (deep-dive + teaser), kept in lock-step with the recorded captions.
//   cd scripts/demo && node gen-script.mjs
// =============================================================================
import { fileURLToPath } from "url";
import fs from "fs";
import path from "path";
import { CHAPTERS, SCENES, TARGET_URL } from "./scenes.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..", "..");
const OUT = path.join(repoRoot, "docs", "walkthrough-script.md");

// Teaser = the highlight scenes (the rest is trimmed/sped in post).
const TEASER = new Set([
  "intro", "intake-loaded", "assess-click", "assess-phase1", "verdict",
  "agent-policy", "agent-submission", "report", "close",
]);

const fmt = (ms) => `${Math.round(ms / 100) / 10}s`;
const byChapter = (key) => SCENES.filter((s) => s.chapter === key);

let md = `# Walkthrough — Narration Script

*Auto-generated from \`scripts/demo/scenes.mjs\` (run \`node scripts/demo/gen-script.mjs\`).
The on-screen captions in the video are the \`Caption\` lines below; the \`Voiceover\`
lines are the fuller narration for a human reader or text-to-speech. Captions stay
qualitative because the agents reason live — the deterministic beat is that the
provider's NPI fails Gate 1, so the packet is held for review.*

- **Recording target:** \`${TARGET_URL}\`
- **Videos:** \`docs/videos/walkthrough-deepdive.mp4\` (full) · \`docs/videos/walkthrough-teaser.mp4\` (~3 min)
- **Approx. total caption time:** ${fmt(SCENES.reduce((a, s) => a + s.holdMs, 0))} (plus the ~90s live assessment run)

---

## Deep-dive script

`;

for (const c of CHAPTERS) {
  const scenes = byChapter(c.key);
  if (!scenes.length) continue;
  md += `### ${c.title}\n\n*${c.sub}*\n\n`;
  for (const s of scenes) {
    md += `- **[${fmt(s.holdMs)}] ${s.id}** — _${s.action}_\n`;
    md += `  - **Caption:** ${s.caption}\n`;
    md += `  - **Voiceover:** ${s.narration}\n`;
  }
  md += `\n`;
}

md += `---

## Teaser script (~3 min)

The teaser is cut from the same recording — these are the beats it keeps (the
assessment wait is sped up, chapters cards are the cut points):

`;
for (const s of SCENES.filter((s) => TEASER.has(s.id))) {
  md += `- **${s.id}** — ${s.caption}\n`;
}

md += `\n---\n\n_Disclaimer (shown on the closing card): an AI-assisted draft for prior-auth preparation; not a payer coverage determination. Human review is required before submission._\n`;

fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, md);
console.log("wrote", OUT, `(${md.length} bytes)`);
