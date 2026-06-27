# Walkthrough video — caption QA

Adversarial check that **every burned-in caption in the deep-dive video matches
what is actually on screen** in the same frame.

**Verdict: PASS.** All distinct captions were verified against their frames; the
two automated flags were re-checked by hand and are **false positives**. No
caption changes were required.

## Method

1. Extracted **76 still frames**, one every **4 s**, across the 5:03 deep-dive
   (`walkthrough-deepdive.mp4`). Each frame contains *both* the burned-in caption
   and the app screen behind it, so a reviewer can check them against each other
   with no timestamp guesswork.
2. Fanned the frames out to **10 independent, read-only reviewers** (8 frames
   each) via a Playwright/Workflow run. Each reviewer viewed every frame and
   returned, per frame: the transcribed caption, a one-line screen summary, and a
   `matches` verdict — instructed to be adversarial and flag any caption that
   names the wrong screen, claims something not shown, or is contradicted.
3. Manually re-verified every flag against the source frames.

Reproduce: `cd scripts/demo && npm run record`, then re-extract frames with
`ffmpeg -i docs/videos/walkthrough-deepdive.mp4 -vf fps=1/4 frames/f_%03d.png`.
(Frames are not committed — `docs/videos/qa/` is gitignored.)

## Coverage

| Metric | Value |
|--------|-------|
| Frames reviewed | 76 |
| Frames with a caption | 68 |
| Chapter title cards / transitions | 8 |
| Distinct captions observed | 38¹ |
| Captions matching their screen | **all** |
| Genuine mismatches | **0** |

¹ Each of the ~30 scripted captions was observed at least once. The count is 38
because reviewers' visual transcription of the small caption text produced minor
variant strings for some captions (see note below).

## The two automated flags — both false positives

| Frame | Flag | Re-verification |
|-------|------|-----------------|
| `f_052` (~204 s) | "Policy Matching" caption shown over the assessment header / Verification Checks | **False positive — transition frame.** The neighboring frames `f_050` and `f_051` (the caption's actual on-screen duration) clearly show the **Policy Matching** tab — the per-code coverage matrix, the provider-verification card (NPI 1669542008, Meghan Osei, *unverified*), and the criteria. `f_052` caught the ~1 s moment the page scrolled back to the top as the next tab was selected. Caption is correct. |
| `f_036` (~140 s) | "Needs Review … one **red** blocker" not visible | **False positive — misread.** The actual caption reads "one **real** blocker," and the frame shows the **Needs Review** badge + summary, which the caption accurately describes. The reviewer transcribed "real" as "red" and searched for a literal red element. |

## Notes

- **Reviewer transcription quirks (not defects):** because the reviewers OCR the
  small caption text from downscaled frames, a few transcriptions drifted from the
  real wording (e.g. "Every"→"Entry", "payer"→"paper", "reviewer"→"retrieves",
  "spans"→"system"). The authoritative caption text lives in
  [`scripts/demo/scenes.mjs`](../../scripts/demo/scenes.mjs); the reliable signal
  from this pass is the **screen-match verdict**, not the transcription.
- **One minor cosmetic observation (not a caption error):** at the Policy-Matching
  → Submission-Readiness hand-off (~204 s) the page briefly scrolls to the top for
  ~1 s before settling on the next tab. It does not affect caption accuracy; it
  could be smoothed in a future re-record by adding a short settle before the tab
  switch in `record-walkthrough.mjs`.
- Captions are intentionally **qualitative** about the outcome (the agents reason
  live, so the confidence/criteria numbers vary run to run — this take shows
  72 %/MEDIUM). The deterministic beat the captions rely on — the provider's NPI
  fails **Gate 1**, so the packet is held for **Needs Review** — was present and
  correct across all relevant frames.
