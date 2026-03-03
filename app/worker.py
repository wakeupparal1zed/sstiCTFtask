import json
import os
import time

from mako.template import Template
from redis import Redis

from app.db import get_campaign, get_job, init_db, update_job_status

REDIS_QUEUE = "render_jobs"


def get_redis_client() -> Redis:
    host = os.getenv("REDIS_HOST", "redis")
    port = int(os.getenv("REDIS_PORT", "6379"))
    return Redis(host=host, port=port, decode_responses=True)


def process_job(raw_payload: str) -> None:
    payload = json.loads(raw_payload)
    job_id = int(payload["job_id"])
    campaign_id = int(payload["campaign_id"])

    job = get_job(job_id)
    campaign = get_campaign(campaign_id)
    if not job or not campaign:
        return

    update_job_status(job_id, "running")

    try:
        context = json.loads(job["context_json"])

        # Intentionally vulnerable challenge behavior:
        # user-provided campaign body is compiled and rendered as a Mako template.
        output = Template(campaign["body_template"]).render(**context)
        update_job_status(job_id, "done", output=output, error=None)
    except Exception as exc:
        update_job_status(job_id, "failed", output=None, error=f"{type(exc).__name__}: {exc}")


def main() -> None:
    init_db()
    redis_client = get_redis_client()

    while True:
        try:
            item = redis_client.brpop(REDIS_QUEUE, timeout=5)
            if not item:
                continue
            _queue_name, payload = item
            process_job(payload)
        except Exception:
            time.sleep(1)


if __name__ == "__main__":
    main()
