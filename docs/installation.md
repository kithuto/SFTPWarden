# Installation

For local development:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,watch]"
sftpwarden --version
```

Build the runtime image locally:

```bash
docker build -t sftpwarden:local -f docker/runtime/Dockerfile .
```

The top-level `Dockerfile` remains as a compatibility entrypoint.

