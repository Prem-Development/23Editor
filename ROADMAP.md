# 23Editor roadmap

## Phase 1 — done

Mobile interface, real local video selection, real playback.
No backend, no AI, honest empty states.

## Phase 2 — in progress (this phase)

Backend + Meta Model API integration:

- Voice instruction (Muse Voice Transcribe) + video-audio transcription
- Validated editing plans (Muse Spark, JSON-schema constrained)
- Subject-aware crop/zoom (SAM 3.1 tracking → FFmpeg)
- Real renders (FFmpeg; originals preserved, voice preserved by default)
- Optional thumbnails (Muse Image, explicit requests only)
- Truthful per-integration capability status
- Routing: only the models each job needs; Wonder escalation off by default

## Social publishing — DEFERRED, NOT REMOVED

Implement after the editing/export flow works. Priority order:

1. YouTube
2. Instagram
3. TikTok
4. Facebook
5. WhatsApp sharing

Requirements (kept):

- Account authorization per network (OAuth / official APIs only).
- Publishing credentials stay separate from the Meta Model API key.
- Editable captions and hashtags before posting.
- Final user approval inside the app for every post.
- Export/share fallback (system share sheet, file handoff) wherever
  direct posting is unavailable or unauthorized.
- Never post silently; never invent reach, views, or engagement stats.
