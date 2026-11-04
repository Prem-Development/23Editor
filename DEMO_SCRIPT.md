# 23Editor — 60–90 second judged demo

Every step below uses a working feature. Nothing is narrated over a
loading screen, and no mocked result is presented as live.

Setup (before the camera rolls): backend running, phone connected over
USB with `adb reverse` in place, one talking-head clip (10+ seconds,
audible speech) and one product clip on the phone. Open the **AI
Status** tab once: backend reachable, API key configured, FFmpeg
available.

## Script (about 75 seconds)

**0:00–0:10 — The problem.**
"Short-form creators reshoot simple jobs. 23Editor looks at your
footage first, then edits it." Open 23Editor, tap **Create Video**,
pick the talking-head clip.

**0:10–0:25 — Understand the footage.**
"This is my video. What is actually in it?" Tap **Review Setup**, then
**Understand your footage**. Point at the summary card: duration,
orientation, speech word count, content hint. "No guessing — measured
from the file."

**0:25–0:40 — Build the edit in the app.**
Tap the **Captions** suggestion chip. The **Help me describe my edit**
sheet opens; tap **Write my brief**, read the editable brief aloud,
tap **Use this edit**. "The app wrote the instruction with me — I never
left for a chatbot."

**0:40–1:00 — Generate and verify.**
Tap **Generate edited video**. Watch the real stages: planning, then
rendering. The **Edited video** preview appears with its measured
duration; the original stays labeled beside it. Play a few seconds so
the judges hear the preserved voice and see captions.

**1:00–1:15 — Export and honesty.**
Tap **Share** to show the system sheet. End card: "Verified on-device
render. Captions are phrase-level, frame checks are spot samples —
everything the app claims, it measured."

## If time allows (bonus, 15 seconds)

Open the **Video cover** tab, type a title, tap **Make cover**, then
attach it as the **Cover image** — "thumbnail from my own footage,
exported beside the video."

## Fallback lines (only if something fails live)

- Transcription fails: "The diagnostics say exactly why — silence,
  no speech, or a provider error — instead of a fake empty caption."
- A style does not fit the footage: "It tells me before rendering
  rather than pretending an effect was applied."
