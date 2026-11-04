# 23Editor backend

Python stdlib-only service. No `pip install` needed — Python 3.11+ is enough.

Pipeline: **voice instruction → Muse editing plan → FFmpeg render.**

## Setup

1. Install FFmpeg (provides `ffmpeg` + `ffprobe`) and put it on PATH:
   - Windows: `winget install Gyan.FFmpeg` (or a gyan.dev release zip)
   - Check: `ffmpeg -version` and `ffprobe -version`
2. Put your key in `backend/.env` (see `.env.example`):
   - Get one at <https://dev.meta.ai> → API keys tab (starts with `LLM_`)
   - Variable name: `META_API_KEY` (`MODEL_API_KEY` also honored)
3. Run from this directory:
   - `python server.py` → listens on `http://127.0.0.1:8080`
4. Run the unit tests (no network, no FFmpeg binary needed):
   - `python -m unittest discover -s tests -t .`

`.env` is git-ignored. The key is used for outbound Meta calls only and is
never logged or returned by any endpoint.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/status` | Truthful capability status (see below) |
| POST | `/api/upload` | multipart `file` + `label` → `{file_id, media?}`. Labels: `source`, `reference`, `asset`. Video/audio probed for streams; images (png/jpg/webp) report dimensions |
| POST | `/api/join` | `{file_ids[1..8], aspect_ratio?}` → `{join_id, clips[{offset…}], duration, cache}`: one normalized timeline for plan/render/analyze; single clip returns itself with no transcode |
| POST | `/api/transcribe` | `{file_id, mode?, languages?, keywords?}` → `{text, turns}` (needs an audio stream; `400 No audio stream found.` otherwise) |
| POST | `/api/plan` | `{file_id?, instruction, style?, aspect_ratio?, fit?, captions?, analysis?, explicit_cuts?}` → validated plan |
| POST | `/api/render` | `{file_id, plan, instruction?}` → `{render_id, url, steps_used, warnings, adjustments, plan, output}` |
| GET | `/api/capabilities` | renderer-supported operations, assets, fonts |
| POST | `/api/edit` | audio? → plan → render in one call |
| POST | `/api/thumbnail` | `{prompt, size?}` → explicit-only artwork |
| POST | `/api/cover` | `{file_id, t, title, position?}` → footage-frame cover |
| POST | `/api/frame` | `{file_id, t}` → still frame asset |
| POST | `/api/analyze/start` | `{file_id}` → cancellable footage analysis job |
| GET/DELETE | `/api/analyze/{job}` | poll / cancel the analysis job |
| POST | `/api/brief` | `{goal, language?, detail?, analysis?}` → editable edit brief |
| POST | `/api/reference/analyze` | `{file_id}` → observable style traits |
| GET | `/api/files/{id}` | download a render or asset |

`mode` is one of `PUSH_TO_TALK`, `ENDPOINTING` (default), `DIARIZATION`.
`languages` uses Meta's documented names (Tamil and English work;
Sinhala is rejected because it is not in Meta's list of 25).
Only turn-level timestamps exist; word-level timestamps do not.

Multi-clip projects upload each main clip, then call `/api/join`
once per clip set. The join normalizes geometry to the first clip
(or a standard 9:16/16:9/1:1 target), frame rate to the fastest
input (capped at 60), and audio to stereo 44.1 kHz with silence
for silent clips; `clips[]` reports each clip's stable id, name,
and offset into the joined timeline. Joins cache by inputs (same
stable `join_id`), so reopening is a cheap cache hit and a missing
join file rebuilds; an unknown id fails with `404` plus
`{"missing": file_id}` so the app can re-upload exactly that clip.
Limits: at most 8 clips and 1800 joined seconds per call
(also served live at `GET /api/capabilities` → `join_limits`).

Plans only allow the catalogued operations below. Anything else is
rejected with `422` and reasons — never silently rendered. Known
aspect aliases (square → 1:1, …) are normalized explicitly and
reported in `adjustments`; unknown values are still rejected. Originals
are never overwritten; audio is stream-copied unless a trim or
another audio operation requires an exact re-encode.

An unambiguous "first N seconds" instruction (English or Tamil) is
reconciled into the plan when the model omits the trim or returns a
full-length one, with a note in `adjustments`. `warnings` is reserved
for items the creator should review (degraded analysis, retried model
calls, validation warnings). Every render is ffprobe-verified before
success is reported: the output duration must match the requested
interval (or the plan, when no explicit interval was requested), and an
expected audio stream must be present — otherwise the render fails
clearly and the bad file is deleted. `output` reports the measured
`duration` and `has_audio` of the verified file.

## Editing operations

Every operation the planner may emit is implemented by the renderer;
the single source is `OP_CATALOG` in `plan_schema.py`, served live at
`GET /api/capabilities` and injected into the planning prompt:

- Cuts (choose one mode): `trim` one interval; `keep` several joined
  intervals; `remove` intervals cut out of the result.
- `text`: timed overlay (position top/center/bottom, fade/slide/none,
  three sizes), shaped Tamil via libass + Nirmala UI.
- `zoom`: smooth zoom over an interval; may span a plain cut (motion
  stays continuous), must not cross a dissolve or overlap another
  zoom; at most 4.
- `grade`: warm/cool/vivid/mono color preset with 0–1 intensity.
- `transition`: dissolve between joined segments (0.25–1.0s).
- `captions`: `source: "auto"` transcribes speech, or explicit
  `segments` (used by the app's caption editor).
- `music`: uploaded audio mixed under the voice with dB volume and
  optional ducking. `sfx`: bundled effects (pop/chime/whoosh/click,
  synthesized locally in `assets.py`) or uploaded audio, placed at
  edited-output seconds.
- `intro` / `outro`: still-image lead-in/tail (0.5–10s each) that
  extend the final timeline. `banner`: image overlaid at
  edited-output seconds (7 positions, 3 sizes).
- `crop`, `frame_subject` (SAM tracking), `resize_pad` with
  `fit: pad` (letterbox) or `crop` (fill), `volume`, `fade`
  (up to two ops; strongest in/out win).

`intro`, `outro`, and `banner` are attached in the app from real
images (a footage cover, generated artwork, or an upload); the
planner never invents them or their file ids.

The model may also return `unsupported` (named effects outside the
catalog) and `notes`; both are shown in the app, never rendered.

## Timeline

Cuts, text, zoom, and captions use **source-footage seconds**
(float). The server remaps them to the edited timeline after
cuts/joins, including crossfade-overlap shifts; a zoom may span a
plain cut (motion stays continuous) but must not cross a dissolve.
Effects landing outside kept footage are dropped with an adjustment
note.

Two exceptions speak **edited-output seconds**: `sfx.at` and banner
`start`/`end`. `intro`/`outro` stills extend the final timeline, so
output-second events include them, and subtitles/voice shift past the
intro. Music always starts at 0:00 of the edited output.

## Styles, clarification, visual analysis

Style presets apply real operations deterministically (see
`_apply_style_effects`): Product Ad → vivid grade; Cinematic → warm
grade + fades; Social Reel → 9:16 crop fill when no aspect is chosen;
Tutorial → no automatic operations (it only guides the planner's
wording). Clearing the preset removes its effect. Explicit user
choices always override preset defaults and model suggestions.

Ambiguity is handled explicitly: "first N minutes" asks whether
seconds or minutes were meant (422, before any model call), and a plan
with no effective operation returns one focused clarification question
instead of a silent no-op render.

For visual-event requests without timestamps ("zoom when the product
appears"), the planner samples up to 6 timestamped frames plus any
supplied transcript context and asks Spark what is visible when;
failures degrade to a warning. Explicit timestamps skip this step.

Only the models each job needs are called (see `router.py`).
Generated images are optional assets; they reach the video only
through explicit user attachments (cover export, intro/outro stills,
banner overlays), never silently.

Transport: POSTs accept `Content-Length` or `Transfer-Encoding: chunked`
(size limits enforced either way). Connections stalled over 120s with
zero bytes are closed with `408`. Routes check Content-Type before
reading the body, so a mistyped request is rejected without reading it.
Uploads stream to disk in 64KB chunks (bounded RAM) up to 2GB, so a
genuine 10-minute phone recording fits; JSON bodies cap at 2MB.
Slow routes report per-stage milliseconds in `timings` (`upload`:
receive/probe; `transcribe`: convert/classify/stt; `plan`:
visual-analysis/model plus `model_roundtrips`; `render`:
sam/transcribe/ffmpeg/verify; `join`: probe/build; `analyze`:
audio/pauses/transcribe/cuts/frames), so the app shows where time
went instead of one total.

## Status labels

`/status` also reports `api_key_present`, `api_access`
(`no_key`/`verified`/`auth_error`/`unreachable`), and FFmpeg/ffprobe
presence, plus one row per integration:

- `verified` — a live call succeeded (stamp persists across restarts
  in `storage/verified.json`).
- `implemented_but_untested` — wired up, no live call yet.
- `provider_error` — the latest attempt failed at the provider, with
  the timestamp and message (persisted in `storage/failed.json`); a
  later success clears it.
- `unavailable` — missing key, binary, or hardware, with the reason.
- `planned` — designed but intentionally not built in this phase
  (Wonder escalation, social publishing).

`verified` for an integration means a real call to that Meta endpoint
succeeded — never just that this code exists, and never from a mocked
test (tests use isolated storage). Cover creation and reference
analysis are local-only FFmpeg work: their failures are feature
errors, never provider errors, and never mark the backend gone.

## Costs (per Meta's published pricing)

Every `/api/*` call except `/status` spends real money on your Meta team:
transcription by audio seconds, Spark by tokens, SAM per video frame,
images per image. Render itself is local FFmpeg (free). `GET /v1/models`
inside `/status` is free.

## Social publishing

Deferred this phase by user decision — not removed.
Roadmap lives in `../ROADMAP.md`.
