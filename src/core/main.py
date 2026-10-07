from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI(
    title="Enterprise Monitoring API",
    version="0.1.0",
)


@app.get("/api/health")
def get_health():
    return {
        "contract_version": 2,
        "rules": "available",
        "ml": "unavailable",
        "agent": "unavailable",
        "demo": True,
    }


app.mount(
    "/",
    StaticFiles(directory="src/interface/web", html=True),
    name="frontend",
)
