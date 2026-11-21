# Contributing

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Before opening a pull request

This project keeps documentation, code constants and the distilled knowledge
base in strict agreement. A dedicated gate enforces it:

```bash
python caofeng-ppt/scripts/check_docs.py          # doc/code number reconciliation
python caofeng-ppt/scripts/test_gates.py          # 167 reverse-self-test assertions
python verify_install.py --skip-e2e               # structural + dependency checks
```

All three must exit 0. When you change a numeric threshold, update its single
source of truth in `caofeng.py` (`TH`) and mirror it in `SPEC.md` §7.1 — never
introduce a second definition point. When you change engine behavior, add a
reverse-self-test in `test_gates.py` that proves the gate actually bites.

## Conventions

- Keep English technical terms (KPI, SOP, LTV, OOXML, preset, gate IDs) untranslated.
- Do not add absolute local paths; use `--work`, `CAOFENG_WORK` or other env vars.
- Do not commit generated work products (`materials/`, `gen/`, `out/`, `report/`).
- Do not reintroduce third-party download links, access codes or promotional
  narration into `kb/`.

See `SECURITY.md` for the legal scope of the bundled knowledge base.
