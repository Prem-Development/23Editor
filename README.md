# 23Editor — Your footage. Your story.

A footage-aware mobile editing assistant. A creator uploads a clip, the
app inspects what is actually in it (length, orientation, cuts, speech),
suggests suitable edits, helps write the editing instruction inside the
app, and renders a real edited video — trimmed, captioned, titled,
zoomed, graded, and mixed — with FFmpeg. No external chatbot needed.

## The creator problem

Short-form creators reshoot or wrestle desktop timelines for simple
jobs: "keep the first 10 seconds, title it, zoom the demo, caption the
speech." 23Editor looks at the footage first, turns a spoken sentence
in Tamil, English, or mixed speech into an executable editing plan and
a verified render, with the original voice preserved and every effect
honestly reported.

## What Meta does (and does not do)

- **Muse Spark** (`muse-spark-1.3`) reads the instruction and returns a
  strictly validated editing plan. For visual-event requests without
  timestamps it also reads up to 6 sampled frames.
- **Muse Voice Transcribe** (`muse-voice-transcribe-1.0`) transcribes
  voice instructions (Tamil/English/mixed supported) and source speech
  for captions (phrase-level timing; no word alignment claimed).
- **SAM 3.1** (`sam-3.1`) tracks a named subject for reframed segments
  when the plan keeps a `frame_subject` operation.
- **Muse Image** (`muse-image-1.0`) is optional and explicit: thumbnails
  only, never a silent replacement for footage.
- **Muse Wonder** stays off unless explicitly enabled.

Unambiguous requests ("first N seconds") are enforced deterministically
even when the model omits the trim. Only the models each job needs are
called; `/status` (free `GET /v1/models`) shows what is available.
Meta never sees the final render: cutting, effects, mixing, and
verification run locally in FFmpeg.

Visual understanding is local: duration, orientation, scene cuts,
sampled frames, and audio/silence signals come from FFmpeg/ffprobe on
the backend machine, combined with the transcript. The app labels frame
samples as spot checks and never claims frame-perfect understanding.

## Architecture

```text
Flutter app (Android)            Python backend (stdlib only)
─────────────────────            ─────────────────────────────
Create: pick video,              /api/upload      → stored upload
  speak/type instruction           (labeled source or reference)
Workspace:                       /api/analyze     → cancellable footage
  Understand → suggest →           analysis, cached per source
  brief → review → render        /api/brief       → editable edit brief
  cuts/zoom/text/captions        /api/reference/analyze → style match
  music+SFX, cover/artwork       /api/transcribe  → turns + diagnosis
  + image attachments            /api/plan        → validated plan
  reference style card           /api/render      → FFmpeg render,
Projects: local JSON store         ffprobe-verified, served back
Share via system sheet           /api/frame /api/cover → footage covers
  + separate cover export        /api/thumbnail  → explicit-only artwork
                                 /api/capabilities→ renderer catalog
                                 /status          → key/access/FFmpeg +
                                                    per-integration labels
```

Cuts, text, zoom, and captions use source-video seconds; the server
remaps them to the edited output after cuts/joins. Sound effects and
banners use edited-output seconds; intro/outro stills extend the
final timeline. The server burns Tamil-safe subtitles with libass,
mixes voice/music/effects with ducking, and refuses to report success
unless duration, streams, and dimensions verify.

Guided flow: **Understand your footage** → suggestion chips (or the
**Help me describe my edit** brief builder, or a reference video's
style) → review the edit → **Generate edited video** → edited
preview → share. Advanced controls stay collapsed; every disabled
action explains what enables it, and API details live in Diagnostics.

## Setup

Needs: Flutter SDK, Python 3.11+, system FFmpeg + ffprobe on PATH, a
Meta API key (<https://dev.meta.ai> → API keys) in `backend/.env` as
`META_API_KEY` (never in the app bundle, logs, or repo).

```powershell
# 1. Backend (own terminal, from backend/)
cd C:\Development\editor_23\backend
python server.py

# 2. App (this hackathon phone, USB-connected)
C:\Users\volsr\AppData\Local\Android\Sdk\platform-tools\adb.exe -s UWNZSKZ9UKBM5LQ8 reverse tcp:8080 tcp:8080
cd C:\Development\editor_23
flutter run -d UWNZSKZ9UKBM5LQ8 --dart-define=BACKEND_URL=http://127.0.0.1:8080
```

Then: pick a clip → **Understand your footage** → pick a suggested
edit (or build one with **Help me describe my edit**) → review →
**Generate edited video** → preview → **Share**. Saved work reopens
from the Projects tab.

## Checks

- Backend: `cd backend` → `python -m unittest discover -s tests -t .`
  (319 tests, OK, zero skipped with FFmpeg on PATH; Meta-touching
  paths use mocked responses, render/frame checks use real FFmpeg)
- App: `dart analyze` (clean), then `flutter test --no-pub`
  (73 tests, OK)
- Combined proof: `test_combined_edit.py` renders keeps + Tamil
  title/captions + zoom + music + SFX + banner + intro and inspects
  frames and audio, not just duration
- See `DEMO_SCRIPT.md` for the 60–90 second judged demo.

## Limitations (honest)

- Footage understanding comes from sparse sampled frames (at most 8)
  plus audio signals — representative spot checks, not full viewing.
- Only Tamil, English, and mixed Tamil-English are advertised for
  speech; other languages use verified provider support only.
- Transitions are dissolves only. A zoom may span a plain cut
  (motion stays continuous) but must not cross a dissolve or overlap
  another zoom; text/captions and image attachments cannot combine
  with subject-tracked segments yet.
- Caption timing is phrase-level (turn timestamps or labeled
  approximation), not karaoke word timing.
- Tamil rendering needs Nirmala UI (ships with Windows); other systems
  need a Tamil font for libass.
- Reference style matching uses a local uploaded video only; there is
  no TikTok/URL download, and unsupported traits are listed, not
  promised.
- Image generation is artwork/cover only and clearly separated from
  video success; policy refusals suggest a frame-based cover instead.
- Direct social auto-publishing is roadmap-only; sharing uses the
  system share sheet, honestly labeled **Share**.
- Variable-frame-rate phone footage is re-encoded to constant rate
  only at dissolve joins; elsewhere timestamps pass through.
