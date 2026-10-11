from fastapi import FastAPI

app = FastAPI(title="going-once-api", version="0.1.0")


@app.get("/healthz", tags=["ops"])
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. Deliberately touches no dependencies."""
    return {"status": "ok"}