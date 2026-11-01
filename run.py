"""Convenience entry: python run.py  (equivalent to uvicorn api.index:app).

WEB_CONCURRENCY controls the worker count. Note: the CMS JSON store locks are
per-process — keep WEB_CONCURRENCY=1 unless /admin writes are disabled or
migrated to a real database (see readme "Production notes").
"""

import os

import uvicorn

from lib import config

if __name__ == "__main__":
    uvicorn.run(
        "api.index:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=config.PORT,
        workers=config.WEB_CONCURRENCY if config.WEB_CONCURRENCY > 1 else None,
        log_level="warning",  # access logs are emitted by the app's own middleware
    )
