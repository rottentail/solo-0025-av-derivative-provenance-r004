# AV Derivative Provenance

AV Derivative Provenance is a Python backend for deterministic audio and video
derivation. It accepts PCM WAV and planar Y4M media, applies bounded
operations, records content digests and lineage, and reproduces a completed
result on demand to verify that the stored bytes still match the plan.

The application uses only the Python standard runtime. It does not need
FFmpeg, network access, credentials, or an online database.

## What it does

- Ingests one- or two-channel signed 16-bit PCM WAV. WAV acceptance depends
  on the running interpreter's standard-library `wave` module: plain
  `WAVE_FORMAT_PCM` files are accepted on every supported runtime, while
  `WAVE_FORMAT_EXTENSIBLE` files are only readable on Python 3.12 or newer.
  See [Cross-runtime WAV handoff](#cross-runtime-wav-handoff) for the full
  handoff rules.
- Ingests planar 4:2:0 YUV4MPEG2 in the `C420` family.
- Applies audio trim, gain, normalization, and two-source mixing.
- Applies video trim, even-aligned crop, luma offset, and horizontal mirror.
- Persists immutable assets, rendition state, provenance edges, and integrity
  checks.
- Replays the stored operation plan and compares both bytes and media
  descriptors.

## Quick start

```bash
python3 -m compileall -q avprovenance scripts
python3 -m avprovenance --host 127.0.0.1 --port 8765 --data-dir .avprovenance-data
```

The startup line includes the active host, port, and data directory. Stop the
process with `Ctrl+C`.

## HTTP endpoints

### Health

```http
GET /health
```

### Upload an asset

```http
POST /v1/assets
Content-Type: application/json

{
  "filename": "source.wav",
  "data_base64": "<strict base64>"
}
```

### Read an asset

```http
GET /v1/assets/{asset_id}
```

### Create a rendition

```http
POST /v1/renditions
Content-Type: application/json

{
  "primary_asset_id": "asset_...",
  "secondary_asset_id": null,
  "output_name": "derived.wav",
  "operations": [
    {
      "type": "media_trim",
      "start_seconds": 0.1,
      "duration_seconds": 0.3
    },
    {"type": "audio_gain", "db": -3},
    {"type": "audio_normalize", "target_peak": 0.8}
  ]
}
```

Rendition creation is synchronous at the HTTP boundary. The durable transition
history still records `queued`, `running`, and the terminal state.

Audio-only operations:

- `media_trim` with `start_seconds` and `duration_seconds`
- `audio_gain` with `db`
- `audio_normalize` with `target_peak`
- `audio_mix` with `secondary_gain`, `primary_weight`, and `secondary_weight`

Video-only operations:

- `media_trim` with `start_frame` and `frame_count`
- `video_crop` with even `x`, `y`, `width`, and `height`
- `video_luma_offset` with `amount`
- `video_mirror`

### Read rendition state

```http
GET /v1/renditions/{rendition_id}
```

### Read lineage

```http
GET /v1/renditions/{rendition_id}/lineage
```

### Verify reproduction

```http
POST /v1/renditions/{rendition_id}/integrity-checks
Content-Type: application/json

{}
```

The successful response has `status: "verified"`. A byte or descriptor
difference returns HTTP `409` with code `INTEGRITY_MISMATCH`; the mismatch
record remains durable.

## Media operation examples

Audio mixing plan:

```json
[
  {"type": "media_trim", "start_seconds": 0.05, "duration_seconds": 0.3},
  {"type": "audio_gain", "db": -3},
  {
    "type": "audio_mix",
    "secondary_gain": -6,
    "primary_weight": 0.75,
    "secondary_weight": 0.4
  },
  {"type": "audio_normalize", "target_peak": 0.8}
]
```

Video plan:

```json
[
  {"type": "media_trim", "start_frame": 1, "frame_count": 4},
  {
    "type": "video_crop",
    "x": 8,
    "y": 6,
    "width": 32,
    "height": 18
  },
  {"type": "video_luma_offset", "amount": 12},
  {"type": "video_mirror"}
]
```

## Data layout

```text
.avprovenance-data/
├── state.json
└── content/
    └── <digest-prefix>/
        └── <digest-prefix>/
            └── <sha256>
```

`state.json` is replaced atomically. Binary objects are content-addressed by
SHA-256 and written through temporary siblings before replacement.

## Source layout

```text
avprovenance/
├── application.py
├── http_api.py
├── models.py
├── validation.py
├── media/
├── renditions/
├── storage/
└── transport/
```

The HTTP boundary calls the application layer. The application coordinates
plan handling, media operations, and persistence. The media layer does not
depend on HTTP or the repository.

## Workflow checks

The baseline intentionally has no automated test suite. Tests are deferred to
the later task stage, which will add red/green verification coverage. The
current production workflow checks are:

```bash
python3 scripts/workflow_checks.py ingest-media-asset
python3 scripts/workflow_checks.py execute-rendition
python3 scripts/workflow_checks.py verify-reproduction
```

Each check starts a temporary HTTP server, generates media bytes in memory,
exercises the public endpoints, and shuts the server down.

## Cross-runtime WAV handoff

Recordings arrive from newer runtimes, while some replay environments still
run Python 3.11. This section states exactly what "the finished program plays"
and "the old environment can re-verify the saved source and recipe" each
depend on, for WAV files that carry a `WAVE_FORMAT_EXTENSIBLE`
(`wFormatTag = 0xFFFE`) format chunk. It documents observed behavior only; it
does not change any processing code.

### Why the runtime version matters

The WAV reader is the standard-library `wave` module; this project adds no
container parser of its own (`avprovenance/media/wav.py` opens every input
through `wave.open`).

- Through Python 3.11, `wave` accepts only `WAVE_FORMAT_PCM` (`0x0001`) in
  the `fmt ` chunk. Any other tag, including `0xFFFE`, fails with
  `wave.Error: unknown format: 65534` before the project's own channel,
  sample-width, or rate checks run.
- Python 3.12 added reading support for `WAVE_FORMAT_EXTENSIBLE` (CPython
  issue gh-77171 / pull request gh-96777, merged 2022-09-14; release note in
  the 3.12 changelog: "Support reading wave files with the
  WAVE_FORMAT_EXTENSIBLE format in the wave module"). The reader still
  requires the 16-byte `SubFormat` GUID to equal
  `KSDATAFORMAT_SUBTYPE_PCM`; any other subformat is rejected, and the
  change is read-only — `wave` writing still emits plain
  `WAVE_FORMAT_PCM`. Python 3.13 keeps the same behavior.

### Behavior matrix (version behavior plus independent samples)

The rows below were reproduced with hand-built WAV samples (not with files
produced by this application) on CPython 3.11.2, 3.12.16, and 3.13.18. The
evidence scripts and captured output are kept in `.evidence/` (not shipped;
see `git status` exclusion in `.gitignore`).

| Sample (`fmt ` tag / SubFormat / geometry) | Python 3.11 | Python 3.12 / 3.13 |
| --- | --- | --- |
| Plain PCM, tag `0x0001`, 16-bit, 1–2 ch | accepted | accepted |
| Extensible, tag `0xFFFE`, SubFormat PCM, 16-bit, 1–2 ch | rejected: `unknown format: 65534` → `415 UNSUPPORTED_MEDIA` | accepted |
| Extensible, SubFormat IEEE float (`00000003-…`) | rejected at the tag check | rejected by the SubFormat GUID check (`unknown extended format: …`) — 16-bit PCM is still required |
| Extensible, SubFormat PCM, 4 channels | rejected at the tag check | read by `wave`, then rejected by this project's own rule: one or two channels only (`415 UNSUPPORTED_MEDIA`) |

Note the two rejection layers are different. On 3.11 the standard library
rejects the container itself. On 3.12+ the container opens, but the project
still enforces its own geometry and 16-bit PCM rules in
`avprovenance/media/wav.py::_validate_params`, so a newer interpreter does
not widen the accepted media envelope beyond what the descriptor records.

### Condition A — the finished program (rendition output) can play

A rendition produced by this application is always encoded by `encode_wav`,
which writes through `wave.Wave_write`. On all observed runtimes
(3.11/3.12/3.13) the writer emits a 16-byte plain `WAVE_FORMAT_PCM`
`fmt ` chunk — it never writes `0xFFFE`. Therefore:

- Playback/readability of a stored output on the old environment depends
  only on the output's own bytes being plain PCM WAV within the recorded
  geometry (one or two channels, 16-bit, 8–192 kHz), not on which runtime
  rendered it and not on what container the sources used.
- Independent check: an extensible-PCM source ingested and rendered on
  3.12 produced an output whose `fmt ` tag is `0x0001`; the same stored
  output blob opens with `wave.open` and passes the project's own
  `inspect_media` on 3.11 unchanged (digest identical across runtimes).
- Consequence: do not declare every already-playing finished program as
  needing remaking. If the stored output is plain PCM WAV — and every output
  this application has ever written is — the old environment can read it.

### Condition B — the old environment can re-verify against the saved source and recipe

Re-verification (`POST /v1/renditions/{id}/integrity-checks`) reopens the
recorded parent assets and replays the normalized operation plan, requiring
equal descriptors and equal SHA-256 bytes
(`avprovenance/renditions/verifier.py`). It needs more than a readable output:

1. Every parent asset blob must be openable by the runtime's `wave` module.
   If a parent is an extensible recording, Python 3.11 fails at container
   open; the verifier catches `UnsupportedMedia` and persists a `mismatch`
   audit with reason `UNSUPPORTED_MEDIA` (reproduced digest `null`). This
   was reproduced end-to-end: one durable data directory built on 3.12
   returns `verified` on 3.12 and 3.13 but `mismatch / UNSUPPORTED_MEDIA`
   on 3.11, without any bytes being changed.
2. The recorded output blob itself must still match its digest, and the
   plan and parent set recorded on the provenance edge must match the
   rendition. Those checks are runtime-independent.
3. The recipe must contain only operations present in the deployed build;
   plan drift and missing parents produce their own stable mismatch reasons
   (`plan_digest_drift`, `parent_set_drift`, `provenance_unavailable`,
   `source_or_output_unavailable`).

So an old-environment re-verification is possible only when **all** parents
are plain `WAVE_FORMAT_PCM` WAV (or non-WAV Y4M, which has no tag issue). A
single extensible parent is sufficient to block same-bytes replay on 3.11.

### What must not be done

- **Do not overwrite or "flatten" the original recording to make the old
  environment accept it.** Assets are immutable and content-addressed by
  SHA-256; changing the `fmt ` chunk — even a header-only tag rewrite that
  leaves every audio frame untouched — produces a different digest
  (observed: original `f1c0c6ee…` vs. rewritten `cb2fcd09…`). The existing
  finished programs are bound to the original asset IDs and the original
  source digest through their provenance edges. Replacing the blob breaks
  those edges and turns every historical rendition's replay into a
  mismatch; it also destroys the fact of what was originally recorded.
- **Do not treat a converted copy as an unchanged source.** A plain-PCM
  rewrite of an extensible file is a *new* byte object. If such a copy is
  ever needed, it must be ingested as a new asset with its own digest and
  its own lineage describing the conversion, and any rendition made from
  it is a new rendition — the old rendition still points at the original
  extensible parent. The application currently provides no conversion
  operation, so this path cannot be executed through the public API; any
  out-of-band conversion is outside the supported workflow and carries no
  in-system provenance.
- **Do not rebuild finished programs merely because they are "old".**
  Condition A is about the output bytes. Plain-PCM outputs remain readable
  on 3.11 regardless of source container; rebuilding is justified only by a
  new business need, never by the runtime boundary itself, and a rebuild
  cannot retroactively change an existing provenance edge anyway.
- **Do not assume extensible means PCM.** The `SubFormat` GUID decides the
  actual sample encoding. Float and other non-PCM subformats are rejected
  on 3.12+ as well, and no promise is made that a container-level rewrite
  preserves audio semantics or summaries. There is no supported path that
  converts such files while guaranteeing unchanged digests or descriptors;
  such a guarantee cannot exist because any byte change changes the digest
  by construction.

### Decision table for handoff

| Situation | Play finished program on 3.11? | Re-verify source + recipe on 3.11? | Supported handling |
| --- | --- | --- | --- |
| All parents plain PCM; output plain PCM | Yes | Yes | Normal handoff; run integrity checks anywhere |
| Extensible-PCM parent; output plain PCM (the common new-runtime case) | Yes — output is tag `0x0001` and was independently read on 3.11 | No — parent fails at `wave.open`, persisted reason `UNSUPPORTED_MEDIA` | Keep the original immutable; verify on a 3.12+ runtime, or carry the recorded mismatch audit as evidence that replay is runtime-blocked rather than corrupt |
| Extensible non-PCM (e.g. float) or >2-channel source | No output exists — ingest is rejected on every current runtime | No | Retain the original recording outside the system; it cannot form an asset or a rendition without a future capability |
| Any proposal to overwrite the original blob | Would appear to work locally | Destroys historical replay for every rendition bound to that digest | Explicitly unsupported; never do this |

### Retention and evidence when same-runtime replay is impossible

When an extensible source makes 3.11 re-verification impossible, the
sound-preserving and fact-preserving options are:

1. **Retain the original extensible blob and the durable state unchanged**,
   and perform the reproduction check on a 3.12+ runtime against the same
   data directory. A `verified` result there, together with the 3.11
   `UNSUPPORTED_MEDIA` mismatch audit, documents both lineage integrity and
   the precise runtime boundary.
2. If only 3.11 is available, retain the stored output (which remains
   playable) and the mismatch audit record. The audit is durable evidence
   of *why* replay could not run; it must not be edited into a pass.
3. If a plain-PCM sibling of the recording is genuinely required for an
   old environment, create it as a separately named, separately ingested
   asset through a documented, out-of-band process, and treat derivatives
   of it as new renditions. The system cannot certify that the conversion
   preserves any summary of the original beyond the raw sample frames you
   explicitly preserve; do not claim digest or descriptor equivalence.

### Reproducing the evidence

The `.evidence/` directory (git-ignored) contains the generator and probes
used for the table above; it is documentation tooling, not part of the
service, and no production module imports it.

```bash
# Hand-built samples: plain PCM, extensible PCM, extensible float, 4-channel
python3 -I .evidence/scripts/make_samples.py .evidence/samples
# Probe stdlib wave and the project media layer on the current interpreter
python3 -I .evidence/scripts/probe_local.py .evidence/samples
# Full cross-runtime sequence: render on 3.12, read output on 3.11,
# and demonstrate the digest change from a header-only tag rewrite
python3.12 -I .evidence/scripts/handoff_e2e.py
python3.11 -I .evidence/scripts/handoff_e2e.py
```

## Environment

No environment variables are required. Runtime choices use `--host`, `--port`,
and `--data-dir`. Incoming request bodies are limited to 36 MiB, decoded media
payloads to 24 MiB, WAV geometry to one or two channels, and Y4M geometry to
1920x1080 with at most 240 frames. WAV container acceptance additionally
depends on the interpreter: `WAVE_FORMAT_EXTENSIBLE` requires Python 3.12 or
newer because the standard-library `wave` module only learned that tag there;
see [Cross-runtime WAV handoff](#cross-runtime-wav-handoff).
