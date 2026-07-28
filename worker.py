"""Delivery worker for CaseFlow AI.

The database is the source of truth. This process polls for durable pending jobs,
so a Redis or process restart cannot lose a CRM delivery request.
"""

from __future__ import annotations

import logging
import os
import time

from main import initialize_database, process_one_delivery_job


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("caseflow.worker")
POLL_SECONDS = float(os.getenv("WORKER_POLL_SECONDS", "2"))


def main() -> None:
    initialize_database()
    while True:
        try:
            result = process_one_delivery_job()
            if result["result"] == "no_pending_job":
                time.sleep(POLL_SECONDS)
            else:
                logger.info("delivery completed job_id=%s", result["job_id"])
        except Exception:
            logger.exception("delivery worker iteration failed")
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
