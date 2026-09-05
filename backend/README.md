# Backend

FastAPI backend foundation for the Gulf Breeze Air platform.

## Run locally

From the repository root:

```bash
cp .env.example .env
```

Fill `POSTGRES_PASSWORD` in `.env`.

From `backend/`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Health endpoint:

- `GET /health`
