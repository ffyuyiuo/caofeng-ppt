# Security & Legal Notice

## Scope of the knowledge base

The `caofeng-ppt/kb/` directory stores **derived technique data** — layout
coordinates, color palettes, animation presets, quality-scoring thresholds and
abstract prompt grammar — extracted from a corpus of third-party slide
templates and tutorial videos. Before public release, the corpus was sanitized:

- third-party cloud-drive download links and access codes were removed;
- creator promotional / call-to-action narration was stripped from transcripts;
- machine-specific absolute paths and proxy endpoints were neutralized and made
  configurable (`--proxy` / `CAOFENG_PROXY`).

The underlying third-party templates, videos and course materials are **not**
included in this repository. All rights in those original works remain with
their respective owners. If you are a rights holder and believe any derived
data here infringes your rights, please open an issue and it will be reviewed
and removed where appropriate.

## No credentials

The skill ships with no API keys or access tokens. The only optional credential
is `CAOFENG_OPENVERSE_TOKEN`, read from the environment at runtime; it is never
stored in the repository. Image and video generation are performed manually in
the web versions of Doubao / Jimeng — no paid image-generation API is called
and no model credits are consumed by the tool.

## Network behavior

All automated network access targets public, license-readable image sources
(Wikimedia Commons, Openverse, Unsplash) for CC-licensed material. Domestic
resources are fetched directly; foreign resources go through a locally
configured proxy. There is no telemetry and no data is uploaded to third
parties.

## De-watermarking

`dewatermark.py` and `lama_cleaner.py` remove the platform corner marks that
the Doubao / Jimeng free web tier overlays on **user-generated** outputs, so
that those outputs can be composited into slides. They are intended solely for
content you generated yourself and have the right to use. Do not use them to
remove watermarks from works you do not own or lack permission to modify.
Respect the terms of service of the platforms involved.

## Reporting

Report security issues via a GitHub issue on this repository. Please do not
post sensitive details publicly.
