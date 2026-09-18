# GridWise — Run Locally or via Docker

## Prerequisites

| Requirement | Why |
|---|---|
| **Python 3.11** | Typed unions (`str \| None`) require 3.10+; pinned to 3.11 |
| **Groq API key** | LLM interpretation of operator notes (get one free at [console.groq.com](https://console.groq.com)) |
| **(Optional) Docker 20+** | For containerised deployment |

---

## Option A — Run Locally

### 1. Create the environment

```bash
# Using conda (recommended on macOS)
conda create -n gridwise python=3.11 -y
conda activate gridwise

# Or via venv
python3.11 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set environment variables

Create a `.env` file in the project root:

```bash
cat > .env << 'EOF'
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
LLM_MODEL=openai/gpt-oss-120b
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_TIMEOUT=8
EOF
```

Export them before running (or use `set -a && source .env`):

```bash
export $(grep -v '^#' .env | xargs)
```

#### CBC solver path (macOS only)

PuLP ships its own CBC binary inside the package, but on Apple Silicon it may fall back
to the scipy solver. If you have a native `cbc` on PATH, set `CBC_PATH` explicitly:

```bash
export CBC_PATH="$(which cbc)"   # e.g. /opt/homebrew/bin/cbc
```

On Linux the PuLP-bundled binary works out of the box — no `CBC_PATH` needed.

### 4. Run the server

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

`--reload` auto-restarts on code changes during development.

### 5. Run the tests

```bash
# Full suite (87 tests)
CBC_PATH="$(which cbc)" python -m pytest tests/ -q

# Just the 10 public sample cases end-to-end
CBC_PATH="$(which cbc)" python -m pytest tests/test_api.py -k "public" -v
```

### 6. Hit the API

```bash
# Health check
curl http://localhost:8000/health

# Submit a scenario
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d @tests/cases.json | python -m json.tool | head -30
```

---

## Option B — Run via Docker

### 1. Build the image

```bash
docker build -t gridwise:latest .
```

### 2. Run the container

```bash
docker run -d \
  --name gridwise \
  -p 8000:8000 \
  -e GROQ_API_KEY="gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
  -e LLM_TIMEOUT=8 \
  gridwise:latest
```

Without `GROQ_API_KEY` the service still works — it uses the deterministic regex
fallback parser for all operator notes.

### 3. Verify

```bash
curl http://localhost:8000/health
# → {"status":"ok","version":"0.1.0"}
```

### 4. Run tests inside the container

```bash
docker run --rm \
  -e GROQ_API_KEY="gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
  -v "$(pwd)/tests:/app/tests:ro" \
  gridwise:latest \
  python -m pytest tests/ -q
```

### 5. Docker Compose (optional)

Create a `docker-compose.yml`:

```yaml
services:
  gridwise:
    build: .
    ports:
      - "8000:8000"
    environment:
      - GROQ_API_KEY=${GROQ_API_KEY}
      - LLM_TIMEOUT=8
    restart: unless-stopped
```

Then:

```bash
# Start in background
docker compose up -d --build

# View logs
docker compose logs -f

# Stop
docker compose down
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'app'` | Run from the project root (`Cost_Energy/`), not from inside `app/` |
| `429 Too Many Requests` from Groq | Rate-limited — the service degrades to fallback automatically. Wait a few seconds and retry |
| `GROQ_API_KEY` not read | Ensure it's exported (`echo $GROQ_API_KEY`). The `.env` file alone doesn't export into the shell |
| SciPy fallback used instead of CBC | Install a native `cbc` (`brew install coin-or/coinor/cbc`) or set `CBC_PATH` explicitly |
| Docker build fails on `gcc`/`g++` | Ensure Docker has enough resources (Preferences → Resources → increase memory to ≥4 GB) |
| Container returns 200 with fallback path | Expected when `GROQ_API_KEY` is unset — the service still functions correctly |
