"""
app/api/routes/vanna.py
────────────────────────
Vanna AI administration.

  GET  /vanna/training-data   What Vanna currently knows (DDL, docs, Q→SQL)
  POST /vanna/train           Re-train from the semantic YAML layer — call after
                              editing semantic/ files (no restart needed)
"""

import logging

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/vanna", tags=["Vanna AI"])


def _require_enabled():
    if not get_settings().vanna_enabled:
        raise HTTPException(status_code=404, detail="Vanna AI is disabled (VANNA_ENABLED=false).")


@router.get("/training-data")
async def training_data():
    _require_enabled()
    from app.vanna_ai.engine import get_vanna

    df = await run_in_threadpool(lambda: get_vanna().get_training_data())
    records = df.fillna("").to_dict(orient="records") if df is not None else []
    counts = {}
    for r in records:
        counts[r.get("training_data_type", "unknown")] = counts.get(r.get("training_data_type", "unknown"), 0) + 1
    return {"counts": counts, "items": records}


@router.post("/train")
async def retrain():
    _require_enabled()
    from app.vanna_ai.analyst import clear_cache
    from app.vanna_ai.engine import retrain as do_retrain

    stats = await run_in_threadpool(do_retrain)
    clear_cache()
    logger.info("[Vanna] Re-trained: %s", stats)
    return {"status": "success", "trained": stats}
