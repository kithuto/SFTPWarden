# Contributing

Thank you for helping improve SFTPWarden.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,watch]"
pytest
ruff check .
ruff format --check .
```

## Style

- Keep code, CLI output, logs, examples, and docs in English.
- Prefer small modules with clear ownership.
- Do not print secrets.
- Keep runtime dependencies minimal.
- Preserve `old/` until the owner removes it manually.

