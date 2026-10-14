# 0.8 development history

The 0.8 line was reconstructed from recorded work and the original Git
commits. These are development checkpoints created during reconstruction;
they do not claim that separate releases were published on the original dates.

| Version | Changes |
| --- | --- |
| 0.8.0-dev | Pre-recorder baseline; explicit FT8/FT4 0–3000 Hz defaults. |
| 0.8.1-dev | PCM WAV/RF64 and Ogg Opus containers and container regression tests. |
| 0.8.2-dev | Recording worker/controller, OPFS, IndexedDB/memory fallbacks, checkpoints, cross-tab protection. |
| 0.8.3-dev | Live capture before mute/playback, profile locks, controlled stop and recovery lifecycle. |
| 0.8.4-dev | Time/MiB counter, long-recording indicator, Chromium/WebKit regression cases and transport documentation. |
| 0.8.5-dev | Unlimited client-side decoded-message history and message counter. |
| 0.8.6-dev | Stale CWformer reference cleanup; regression suite and CI retained; synchronized release metadata. |

Source provenance:

- `32e2088`: pre-recorder source used as the baseline's parent.
- `f6f2298`: original combined recorder update, split into dependency-ordered
  container, storage, integration and presentation/test blocks.
- `55b0292`: normal stops no longer appear as recovery candidates.
- `96ca707`: remove the decoded-message limit.
- `86c66cf` and `74483d3`: use their combined final state, keeping restored
  tests and removing obsolete references. The temporary test deletion is not
  reproduced in the reconstructed line.

The final application source is preserved from `74483d3`, except for version
metadata. Original history is retained in
`backup/dev-before-version-rebuild-20261014`.
