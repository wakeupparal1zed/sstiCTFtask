import json
import os

from flask import Flask, jsonify, redirect, render_template, request, url_for
from redis import Redis

from app.db import (
    create_campaign,
    create_render_job,
    create_ticket,
    dashboard_stats,
    get_campaign,
    get_job,
    init_db,
    list_campaigns,
    list_jobs,
    list_tickets,
    search_kb,
)

REDIS_QUEUE = "render_jobs"


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


def create_app() -> Flask:
    app = Flask(__name__)
    init_db()

    @app.route("/")
    def dashboard():
        stats = dashboard_stats()
        return render_template("index.html", stats=stats)

    @app.route("/campaigns")
    def campaigns():
        return render_template("campaigns.html", campaigns=list_campaigns())

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

            campaign_id = create_campaign(title=title, subject=subject, body_template=body_template)
            return redirect(url_for("campaign_view", campaign_id=campaign_id))

        return render_template("campaign_new.html", error=None)

    @app.route("/campaigns/<int:campaign_id>")
    def campaign_view(campaign_id: int):
        campaign = get_campaign(campaign_id)
        if not campaign:
            return "Campaign not found", 404
        return render_template("campaign_view.html", campaign=campaign)

    @app.route("/campaigns/<int:campaign_id>/simulate", methods=["POST"])
    def campaign_simulate(campaign_id: int):
        campaign = get_campaign(campaign_id)
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
        job_id = create_render_job(campaign_id=campaign_id, target_email=target_email, context=context)
        enqueue_render_job(job_id=job_id, campaign_id=campaign_id, target_email=target_email)
        return redirect(url_for("job_view", job_id=job_id))

    @app.route("/jobs")
    def jobs():
        return render_template("jobs.html", jobs=list_jobs())

    @app.route("/jobs/<int:job_id>")
    def job_view(job_id: int):
        job = get_job(job_id)
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
        stats = dashboard_stats()
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

        campaign_id = create_campaign(title=title, subject=subject, body_template=body_template)
        return jsonify({"campaign_id": campaign_id}), 201

    @app.route("/api/campaigns/<int:campaign_id>/simulate", methods=["POST"])
    def api_simulate(campaign_id: int):
        campaign = get_campaign(campaign_id)
        if not campaign:
            return jsonify({"error": "campaign not found"}), 404

        data = request.get_json(silent=True) or {}
        target_email = str(data.get("target_email", "qa@orbit.local"))
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
        job_id = create_render_job(campaign_id=campaign_id, target_email=target_email, context=context)
        enqueue_render_job(job_id=job_id, campaign_id=campaign_id, target_email=target_email)
        return jsonify({"job_id": job_id, "status": "queued"}), 202

    @app.route("/api/jobs/<int:job_id>")
    def api_job(job_id: int):
        job = get_job(job_id)
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
