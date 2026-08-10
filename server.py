from __future__ import annotations

import threading
import webbrowser
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.pkulaw_mcp import PkuLawMCPClient, PkuLawMCPError
from backend.risk_map import build_graph, sync_pkulaw_cases


ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"

app = FastAPI(title="科创企业风险知识地图", version="1.0.0")


class RiskMapSyncRequest(BaseModel):
    risk_codes: list[str] = Field(default_factory=list)
    limit_per_risk: int = 3


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB / "index.html", media_type="text/html")


@app.get("/risk-map-data.js", include_in_schema=False)
def offline_data() -> FileResponse:
    return FileResponse(WEB / "risk-map-data.js", media_type="application/javascript")


@app.get("/api/risk-map")
def risk_map_data() -> dict:
    graph = build_graph()
    graph["pkulaw"] = PkuLawMCPClient().status()
    return graph


@app.get("/api/risk-map/pkulaw-status")
def pkulaw_status() -> dict:
    return PkuLawMCPClient().status()


@app.post("/api/risk-map/sync")
def sync_risk_map(req: RiskMapSyncRequest) -> dict:
    if req.limit_per_risk < 1 or req.limit_per_risk > 50:
        raise HTTPException(400, "limit_per_risk 必须在 1 到 50 之间")
    try:
        result = sync_pkulaw_cases(req.risk_codes, req.limit_per_risk)
    except PkuLawMCPError as exc:
        raise HTTPException(503, str(exc)) from exc
    return result | {"graph": build_graph()}


@app.get("/api/health")
def health() -> dict:
    graph = build_graph()
    return {
        "ok": True,
        "service": "risk-knowledge-map",
        "nodes": len(graph["nodes"]),
        "edges": len(graph["edges"]),
        "pkulaw_configured": PkuLawMCPClient().configured,
    }


if __name__ == "__main__":
    import uvicorn

    url = "http://127.0.0.1:8025/"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=8025)
