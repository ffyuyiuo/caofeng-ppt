# Changelog

## 3.17.0 (2026-11-19)

- 文本体裁要点页图形化收口（#61）：`parallel_enum` 补句末标点判据、`propose_lines`
  对无数字的 ≥3 要点页返回卡片栅格提案，修复培训/制度/手册类文档图形化率归零。
- 新增反向自证断言，累计 167 项；29 道机器闸口径一致。

## 3.16.0 — 3.15.x

- 体裁中立收口：修复标题退化成单字、表格配额按文档密度、同级尺跨体裁。
- 干净上下文审计：下线从未被读取的死阈值、合并重复口径、补齐 6 个阈值文档。
- 数据页「一页一断言」hero-first：关键数字先立 ≥60pt 主视觉，明细进演讲者备注。

## Public-release build (this repository)

This is the public-release snapshot of 3.17.0. Relative to the internal
distribution package, the following changes were applied:

- Added `LICENSE` (MIT), `SECURITY.md`, `CHANGELOG.md`, `CONTRIBUTING.md`,
  `.gitignore`.
- Sanitized `caofeng-ppt/kb/`: removed third-party drive links, access codes and
  creator promotional narration from transcripts and study notes (141 lines).
- Replaced machine-specific absolute paths with neutral placeholders in 29
  files; made the local proxy configurable via `--proxy` / `CAOFENG_PROXY`.
- Regenerated `MANIFEST.json` and `sha256sums.txt` to match the sanitized tree.
- Removed distribution-only archives and legacy version artifacts.

## Corpus-layer removal (public release)

To avoid redistributing third-party derived content, the public build drops the
raw corpus layer that is not needed for slide generation:

- Removed 276 per-video ASR transcripts (`kb/transcripts/`) and 26 per-frame
  analysis notes (`kb/video_logic/`), plus their indexes (`video_index.json`,
  `video_logic.json`).
- Removed 12 study-only scripts with no runtime dependency; the self-checks that
  named them were retuned to the retained render/gate path.
- Kept every distilled parameter the engine and gates consume (color system,
  layout archetypes, animation mechanics, thresholds, parity baseline), plus the
  study conclusions in `kb/*_study.md`.

Net effect: 389 → 73 tracked files. Slide generation, the 29 gates and their
reverse self-tests remain unchanged.
