# 23Editor — phone acceptance checklist

Backend running, USB `adb reverse` in place, app built with
`--dart-define=BACKEND_URL=http://127.0.0.1:8080`.
Backend and app builds are shown in the AI Status tab — check them
against the current source before judging a failure.

## 1. Connection truth (AI Status tab)

- [ ] Backend reachable, API key configured, Meta access Available,
      FFmpeg available with a version string.
- [ ] Each integration shows one honest label: Verified (with a live
      success date), Not yet tested, Provider error (with timestamp
      and message), Unavailable, or Planned.
- [ ] Kill the backend, reopen AI Status: "Backend is not connected."
      with Try again. Restart it: labels return (verified dates kept).

## 2. Core edit (talking head, 10s+ with speech)

- [ ] Create Video → pick clip → Review Setup.
- [ ] **Understand your footage** → **Find what's in my video**:
      duration/orientation/speech summary, frame strip, suggestion
      chips. Cancel works mid-run.
- [ ] Tap the **Captions** chip → **Write my brief** → **Use this
      edit**: the instruction field fills with the brief.
- [ ] **Edits to apply** lists the plan with each op's origin.
- [ ] **Generate edited video**: real stages (uploading, planning,
      rendering, preparing), then **Edited video** with measured
      duration beside the labeled **Original video**.
- [ ] Play the edit: voice preserved, captions visible and timed.
- [ ] **Share** opens the system sheet (labeled Share, not published).

## 3. Cuts, attachments, cover

- [ ] Keep & remove: add 0–4s and 7–13s keeps; output-span previews
      and estimated length look right; mixed keep+remove blocks
      Generate with a clear message.
- [ ] Video cover tab: make a cover from a frame + title; attach it
      as **Cover image**; after a render, **Save cover** /
      **Share cover** appear beside the edited video.
- [ ] Attach an **Intro still** (2s) and a **Banner overlay** with
      start/end inside the estimated output; render; intro plays
      first and the banner floats over the footage.
- [ ] Changing any setting after a plan discards the plan/output
      (stale results never reappear).

## 4. Failure honesty

- [ ] Silent clip + captions: "no speech" diagnosis with a
      written-title offer — never fabricated captions.
- [ ] SFX/banner past the output end: rejected with the valid range
      before rendering.
- [ ] Airplane-mode mid-cover (backend dead): "Backend is not
      connected." Slow backend: the feature error says the backend is
      still reachable — cover/reference failures never claim a
      disconnect while the backend answers.

## 5. In-app acceptance instructions

Talking head (10s+ speech):
"Keep the first 10 seconds. Show 'My day' for the first two seconds
with a fade-in. Slowly zoom between seconds 3 and 5. Add captions for
the speech. Keep the original voice."

Product (with a music file on the device):
"Keep the best 8 seconds. Add this music quietly under my voice.
Zoom in on the product between seconds 2 and 4."

Reference style (two clips):
Upload the edit as source and a reel as the reference video, tap
**Apply to my video**, then generate. The output keeps the source
footage with the reference's pacing/framing adapted to it.
