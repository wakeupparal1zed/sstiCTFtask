import json
import os
import re
import sqlite3
from secrets import compare_digest, token_urlsafe
from urllib.parse import urlsplit

from flask import (
    Flask,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from redis import Redis
from werkzeug.security import check_password_hash, generate_password_hash

from app.db import (
    create_campaign,
    create_render_job,
    create_ticket,
    create_user,
    dashboard_stats,
    get_campaign,
    get_job,
    get_user_by_id,
    get_user_by_username,
    init_db,
    list_campaigns,
    list_jobs,
    list_tickets,
    search_kb,
)

REDIS_QUEUE = "render_jobs"
CSRF_SESSION_KEY = "_csrf_token"
PUBLIC_ENDPOINTS = {"login", "register", "robots", "static"}
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


def get_redis_client() -> Redis:
    host = os.getenv("REDIS_HOST", "redis")
    port = int(os.getenv("REDIS_PORT", "6379"))
    return Redis(host=host, port=port, decode_responses=True)


def enqueue_render_job(job_id: int, campaign_id: int, target_email: str) -> None:
    payload = {
        "job_id": job_id,
        "campaign_id": campaign_id,
        "target_email": target_email,
    }
    client = get_redis_client()
    client.lpush(REDIS_QUEUE, json.dumps(payload))


def queue_size() -> int:
    try:
        client = get_redis_client()
        return int(client.llen(REDIS_QUEUE))
    except Exception:
        return -1


def is_safe_redirect_target(target: str | None) -> bool:
    if not target:
        return False

    parts = urlsplit(target)
    return not parts.scheme and not parts.netloc and target.startswith("/")


def get_next_target(default_endpoint: str = "dashboard") -> str:
    candidate = (
        request.form.get("next")
        or request.args.get("next")
        or request.headers.get("X-Next")
    )
    if is_safe_redirect_target(candidate):
        return candidate
    return url_for(default_endpoint)


def validate_username(username: str) -> str | None:
    if not USERNAME_RE.fullmatch(username):
        return "Username must be 3-32 chars and use only letters, digits, ., _, or -."
    return None


def validate_password(password: str, password_confirm: str | None = None) -> str | None:
    if len(password) < 10:
        return "Password must be at least 10 characters."
    if password_confirm is not None and password != password_confirm:
        return "Passwords do not match."
    return None


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY") or token_urlsafe(32),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "0") == "1",
    )
    init_db()

    def is_api_request() -> bool:
        return request.path.startswith("/api/")

    def current_user_id() -> int:
        return int(g.user["id"])

    def get_csrf_token() -> str:
        token = session.get(CSRF_SESSION_KEY)
        if token:
            return token

        token = token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
        return token

    def csrf_error(message: str):
        if is_api_request():
            return jsonify({"error": message}), 400
        return message, 400

    def enforce_csrf():
        expected = session.get(CSRF_SESSION_KEY)
        if not expected:
            return csrf_error("Missing CSRF session token.")

        provided = request.form.get("csrf_token", "")
        if request.is_json:
            data = request.get_json(silent=True) or {}
            provided = str(data.get("csrf_token", ""))

        provided = provided or request.headers.get("X-CSRF-Token", "")
        if not provided or not compare_digest(expected, str(provided)):
            return csrf_error("Invalid CSRF token.")
        return None

    @app.before_request
    def load_user_and_enforce_auth():
        g.user = None
        user_id = session.get("user_id")
        if user_id:
            g.user = get_user_by_id(int(user_id))
            if g.user is None:
                session.clear()

        if request.endpoint is None or request.endpoint == "static":
            return None

        if request.endpoint in PUBLIC_ENDPOINTS:
            if g.user is not None and request.endpoint in {"login", "register"}:
                return redirect(url_for("dashboard"))
            if request.method in {"POST"}:
                return enforce_csrf()
            return None

        if g.user is None:
            if is_api_request():
                return jsonify({"error": "authentication required"}), 401
            return redirect(url_for("login", next=request.full_path if request.query_string else request.path))

        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            return enforce_csrf()

        return None

    @app.context_processor
    def inject_template_globals():
        return {
            "current_user": g.get("user"),
            "csrf_token": get_csrf_token(),
        }

    @app.route("/register", methods=["GET", "POST"])
    def register():
        error = None
        username = ""

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            password_confirm = request.form.get("password_confirm", "")

            error = validate_username(username) or validate_password(password, password_confirm)
            if error is None and get_user_by_username(username):
                error = "Username is already taken."

            if error is None:
                try:
                    user_id = create_user(
                        username=username,
                        password_hash=generate_password_hash(password, method="scrypt"),
                    )
                except sqlite3.IntegrityError:
                    error = "Username is already taken."

            if error is None:
                session.clear()
                session["user_id"] = user_id
                flash("Account created.")
                return redirect(get_next_target())

        return render_template(
            "auth.html",
            auth_mode="register",
            error=error,
            username=username,
            next_target=get_next_target(),
        )

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        username = ""

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            user = get_user_by_username(username) if username else None

            if not user or not check_password_hash(user["password_hash"], password):
                error = "Invalid username or password."
            else:
                session.clear()
                session["user_id"] = int(user["id"])
                flash("Signed in.")
                return redirect(get_next_target())

        return render_template(
            "auth.html",
            auth_mode="login",
            error=error,
            username=username,
            next_target=get_next_target(),
        )

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        flash("Signed out.")
        return redirect(url_for("login"))

    @app.route("/")
    def dashboard():
        stats = dashboard_stats(current_user_id())
        return render_template("index.html", stats=stats)

    @app.route("/campaigns")
    def campaigns():
        return render_template("campaigns.html", campaigns=list_campaigns(current_user_id()))

    @app.route("/campaigns/new", methods=["GET", "POST"])
    def campaigns_new():
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            subject = request.form.get("subject", "").strip()
            body_template = request.form.get("body_template", "")
            if not title or not subject or not body_template:
                return render_template(
                    "campaign_new.html",
                    error="Title, subject and template body are required.",
                )

            campaign_id = create_campaign(
                owner_user_id=current_user_id(),
                title=title,
                subject=subject,
                body_template=body_template,
            )
            return redirect(url_for("campaign_view", campaign_id=campaign_id))

        return render_template("campaign_new.html", error=None)

    @app.route("/campaigns/<int:campaign_id>")
    def campaign_view(campaign_id: int):
        campaign = get_campaign(campaign_id, owner_user_id=current_user_id())
        if not campaign:
            return "Campaign not found", 404
        return render_template("campaign_view.html", campaign=campaign)

    @app.route("/campaigns/<int:campaign_id>/simulate", methods=["POST"])
    def campaign_simulate(campaign_id: int):
        campaign = get_campaign(campaign_id, owner_user_id=current_user_id())
        if not campaign:
            return "Campaign not found", 404

        target_email = request.form.get("target_email", "qa@orbit.local").strip() or "qa@orbit.local"
        context = {
            "user": {
                "name": "Jordan Tester",
                "tier": "growth",
                "company": "Northwind",
            },
            "company": {
                "name": "Orbit Mail",
                "plan": "pro",
            },
            "preview": {
                "channel": "smtp",
                "build": "2026.03.01",
            },
        }
        job_id = create_render_job(
            campaign_id=campaign_id,
            owner_user_id=current_user_id(),
            target_email=target_email,
            context=context,
        )
        enqueue_render_job(job_id=job_id, campaign_id=campaign_id, target_email=target_email)
        return redirect(url_for("job_view", job_id=job_id))

    @app.route("/jobs")
    def jobs():
        return render_template("jobs.html", jobs=list_jobs(current_user_id()))

    @app.route("/jobs/<int:job_id>")
    def job_view(job_id: int):
        job = get_job(job_id, owner_user_id=current_user_id())
        if not job:
            return "Job not found", 404
        return render_template("job_view.html", job=job)

    @app.route("/tickets", methods=["GET", "POST"])
    def tickets():
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            details = request.form.get("details", "").strip()
            if title and details:
                create_ticket(title=title, details=details)
            return redirect(url_for("tickets"))

        return render_template("tickets.html", tickets=list_tickets())

    @app.route("/kb")
    def kb():
        q = request.args.get("q", "").strip()
        return render_template("kb.html", query=q, articles=search_kb(q))

    @app.route("/status")
    def status():
        stats = dashboard_stats(current_user_id())
        queue_len = queue_size()
        info = {
            "api": "green",
            "worker": "green",
            "smtp": "degraded",
            "redis_queue_len": queue_len,
            "queued_jobs": stats["queued"],
        }
        return render_template("status.html", info=info)

    @app.route("/api/campaigns", methods=["POST"])
    def api_create_campaign():
        data = request.get_json(silent=True) or {}
        title = str(data.get("title", "")).strip()
        subject = str(data.get("subject", "")).strip()
        body_template = str(data.get("body_template", ""))

        if not title or not subject or not body_template:
            return jsonify({"error": "title, subject, body_template required"}), 400

        campaign_id = create_campaign(
            owner_user_id=current_user_id(),
            title=title,
            subject=subject,
            body_template=body_template,
        )
        return jsonify({"campaign_id": campaign_id}), 201

    @app.route("/api/campaigns/<int:campaign_id>/simulate", methods=["POST"])
    def api_simulate(campaign_id: int):
        campaign = get_campaign(campaign_id, owner_user_id=current_user_id())
        if not campaign:
            return jsonify({"error": "campaign not found"}), 404

        data = request.get_json(silent=True) or {}
        target_email = str(data.get("target_email", "qa@orbit.local")).strip() or "qa@orbit.local"
        context = {
            "user": {
                "name": "Jordan Tester",
                "tier": "growth",
                "company": "Northwind",
            },
            "company": {
                "name": "Orbit Mail",
                "plan": "pro",
            },
            "preview": {
                "channel": "smtp",
                "build": "2026.03.01",
            },
        }
        job_id = create_render_job(
            campaign_id=campaign_id,
            owner_user_id=current_user_id(),
            target_email=target_email,
            context=context,
        )
        enqueue_render_job(job_id=job_id, campaign_id=campaign_id, target_email=target_email)
        return jsonify({"job_id": job_id, "status": "queued"}), 202

    @app.route("/api/jobs/<int:job_id>")
    def api_job(job_id: int):
        job = get_job(job_id, owner_user_id=current_user_id())
        if not job:
            return jsonify({"error": "job not found"}), 404

        return jsonify(
            {
                "job_id": int(job["id"]),
                "campaign_id": int(job["campaign_id"]),
                "status": job["status"],
                "target_email": job["target_email"],
                "output": job["output"],
                "error": job["error"],
                "created_at": job["created_at"],
                "updated_at": job["updated_at"],
            }
        )

    @app.route("/robots.txt")
    def robots():
        content = """User-agent: *
Disallow: /admin
"""
        return app.response_class(content, mimetype="text/plain")

    return app


if __name__ == "__main__":
    flask_app = create_app()
    port = int(os.getenv("PORT", "8000"))
    flask_app.run(host="0.0.0.0", port=port, debug=False)
