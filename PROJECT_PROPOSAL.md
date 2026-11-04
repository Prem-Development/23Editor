# 23Editor — Project proposal

**Your footage. Your story.**

## Project summary

23Editor is a Flutter Android video editing assistant backed by Python, Meta models, and FFmpeg. A creator describes an edit in Tamil or English, reviews the proposed operations, and exports an edited MP4. The workspace combines natural-language requests with explicit timing, caption, framing, and media controls.

## Intended users and problem

The project serves content creators, small businesses, educators, and Tamil-speaking users who need to prepare short videos. Editing often requires navigating separate controls for cuts, captions, sound, framing, and supporting visuals. Expressing a goal in familiar words can reduce the steps needed to begin an edit while keeping explicit controls available for precision.

## Proposed solution and implemented workflow

A user chooses one or more clips, types or records an instruction, reviews the setup and editing plan, and generates an output. The backend validates model output before rendering. The app reports upload, planning, rendering, and output-preparation progress, with diagnostics when a stage fails.

The workspace includes source-time Keep/Remove, caption burn-in, style presets, aspect ratios, background music, timed sound effects, covers, generated artwork, image attachments, and supporting video placement. A reference video can guide style. The creator's source footage remains the main story.

## Model roles

| Model | Role in 23Editor |
| --- | --- |
| Muse Spark | Structured editing plan generation |
| Muse Voice Transcribe | Spoken instruction and source-speech transcription |
| SAM 3.1 | Subject tracking for requested framing operations |
| Muse Image | Explicit artwork and thumbnail generation |
| Muse Wonder | Optional retry for a Spark plan that fails validation |

## What distinguishes the workflow

- Tamil, English, and mixed-speech entry points.
- Reviewable edits with their instruction, control, or preset origin.
- Explicit source-time cuts and edited-time attachment placement.
- Separation of spoken edit instructions from source speech captions.
- Real FFmpeg exports and status markers tied to successful integration operations.
- Main clips, reference media, and supporting media have distinct roles.

## Demonstrated evidence

Supplied backend reports recorded a 24.98-second source exported as exactly 10 seconds for a first-10 instruction and exactly 13 seconds for a keep-2-to-15 instruction. The outputs included video and audio. Supplied phone screenshots show provider success markers, backend reachability, and the editing workspace. Phone confirmation of the later arbitrary-range fix was still pending in the latest report.

## Current limitations

The demonstration uses a PC-hosted backend. On-device open-model execution and social auto-publishing are outside the delivered workflow. Long-video and large-file performance benchmarks were deferred. An audio-stutter report needs listening-based acceptance on the phone. UI availability and successful provider calls should be distinguished from testing every feature combination.

## Next development steps

1. Complete repeatable phone acceptance across the supported edit combinations.
2. Measure genuine 5- and 10-minute recordings and large uploads.
3. Improve guidance for ambiguous instructions and out-of-range timestamps.
4. Extend quality checks for speech, music mixing, and playback continuity.
5. Evaluate backend hosting, cancellation, and longer-running jobs for broader use.

## Submission materials

Use the root README for setup and verified scope. Add the final GitHub and YouTube links to the submission form. Use an actual phone recording to demonstrate the chosen input, instruction, generated plan, and exported result.
