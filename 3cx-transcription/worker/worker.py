"""RQ worker entrypoint."""
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import redis
from rq import Worker, Queue

from api.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

if __name__ == "__main__":
    conn = redis.from_url(settings.REDIS_URL)
    queues = [Queue("default", connection=conn)]
    worker = Worker(queues, connection=conn)
    worker.work(with_scheduler=True)
