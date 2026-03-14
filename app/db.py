import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DEFAULT_DB_PATH = "/data/app.db"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db_path() -> str:
    return os.getenv("DB_PATH", DEFAULT_DB_PATH)


@contextmanager
def db_conn():
    path = get_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with db_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id INTEGER,
                title TEXT NOT NULL,
                subject TEXT NOT NULL,
                body_template TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (owner_user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS render_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                owner_user_id INTEGER,
                target_email TEXT NOT NULL,
                context_json TEXT NOT NULL,
                status TEXT NOT NULL,
                output TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (campaign_id) REFERENCES campaigns(id),
                FOREIGN KEY (owner_user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                details TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS kb_articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_campaigns_owner_user_id
            ON campaigns(owner_user_id);

            CREATE INDEX IF NOT EXISTS idx_render_jobs_owner_user_id
            ON render_jobs(owner_user_id);
            """
        )
        ensure_column(
            conn,
            table_name="campaigns",
            column_name="owner_user_id",
            column_sql="INTEGER REFERENCES users(id)",
        )
        ensure_column(
            conn,
            table_name="render_jobs",
            column_name="owner_user_id",
            column_sql="INTEGER REFERENCES users(id)",
        )
        conn.commit()
        seed_demo_data(conn)


def ensure_column(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_sql: str,
) -> None:
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name in columns:
        return

    conn.execute(
        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}"
    )


def seed_demo_data(conn: sqlite3.Connection) -> None:
    has_ticket = conn.execute("SELECT 1 FROM tickets LIMIT 1").fetchone()
    if not has_ticket:
        conn.execute(
            """
            INSERT INTO tickets (title, details, status, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                "SMTP warm-up warning",
                "Deliverability dropped to 92% after new domain setup.",
                "open",
                utc_now(),
            ),
        )

    has_kb = conn.execute("SELECT 1 FROM kb_articles LIMIT 1").fetchone()
    if not has_kb:
        articles = [
            (
                "Template rendering notes",
                "Placeholders are supported in campaign templates used by the simulator.",
                utc_now(),
            ),
            (
                "Deliverability simulator",
                "Use simulator before sending campaigns. Rendering is done by a worker queue.",
                utc_now(),
            ),
        ]
        conn.executemany(
            "INSERT INTO kb_articles (title, body, created_at) VALUES (?, ?, ?)",
            articles,
        )
    else:
        conn.execute(
            """
            UPDATE kb_articles
            SET title = ?, body = ?
            WHERE title = 'Template syntax quickstart'
            """,
            (
                "Template rendering notes",
                "Placeholders are supported in campaign templates used by the simulator.",
            ),
        )

    conn.commit()


def create_user(username: str, password_hash: str) -> int:
    with db_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO users (username, password_hash, created_at)
            VALUES (?, ?, ?)
            """,
            (username, password_hash, utc_now()),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_user_by_username(username: str) -> sqlite3.Row | None:
    with db_conn() as conn:
        return conn.execute(
            """
            SELECT id, username, password_hash, created_at
            FROM users
            WHERE username = ? COLLATE NOCASE
            """,
            (username,),
        ).fetchone()


def get_user_by_id(user_id: int) -> sqlite3.Row | None:
    with db_conn() as conn:
        return conn.execute(
            """
            SELECT id, username, password_hash, created_at
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()


def create_campaign(owner_user_id: int, title: str, subject: str, body_template: str) -> int:
    with db_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO campaigns (owner_user_id, title, subject, body_template, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (owner_user_id, title, subject, body_template, utc_now()),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_campaigns(owner_user_id: int) -> list[sqlite3.Row]:
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, title, subject, created_at
            FROM campaigns
            WHERE owner_user_id = ?
            ORDER BY id DESC
            """,
            (owner_user_id,),
        ).fetchall()
        return list(rows)


def get_campaign(campaign_id: int, owner_user_id: int | None = None) -> sqlite3.Row | None:
    with db_conn() as conn:
        if owner_user_id is None:
            return conn.execute(
                "SELECT * FROM campaigns WHERE id = ?",
                (campaign_id,),
            ).fetchone()

        return conn.execute(
            """
            SELECT *
            FROM campaigns
            WHERE id = ? AND owner_user_id = ?
            """,
            (campaign_id, owner_user_id),
        ).fetchone()


def create_render_job(campaign_id: int, owner_user_id: int, target_email: str, context: dict) -> int:
    now = utc_now()
    with db_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO render_jobs (
                campaign_id,
                owner_user_id,
                target_email,
                context_json,
                status,
                output,
                error,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?)
            """,
            (
                campaign_id,
                owner_user_id,
                target_email,
                json.dumps(context),
                "queued",
                now,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_job_status(job_id: int, status: str, output: str | None = None, error: str | None = None) -> None:
    with db_conn() as conn:
        conn.execute(
            """
            UPDATE render_jobs
            SET status = ?, output = ?, error = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, output, error, utc_now(), job_id),
        )
        conn.commit()


def get_job(job_id: int, owner_user_id: int | None = None) -> sqlite3.Row | None:
    with db_conn() as conn:
        if owner_user_id is None:
            return conn.execute(
                """
                SELECT r.*, c.title AS campaign_title, c.subject AS campaign_subject
                FROM render_jobs r
                JOIN campaigns c ON c.id = r.campaign_id
                WHERE r.id = ?
                """,
                (job_id,),
            ).fetchone()

        return conn.execute(
            """
            SELECT r.*, c.title AS campaign_title, c.subject AS campaign_subject
            FROM render_jobs r
            JOIN campaigns c ON c.id = r.campaign_id
            WHERE r.id = ? AND r.owner_user_id = ?
            """,
            (job_id, owner_user_id),
        ).fetchone()


def list_jobs(owner_user_id: int) -> list[sqlite3.Row]:
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.campaign_id, r.target_email, r.status, r.created_at, c.title AS campaign_title
            FROM render_jobs r
            JOIN campaigns c ON c.id = r.campaign_id
            WHERE r.owner_user_id = ?
            ORDER BY r.id DESC
            """
            ,
            (owner_user_id,),
        ).fetchall()
        return list(rows)


def list_tickets() -> list[sqlite3.Row]:
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, details, status, created_at FROM tickets ORDER BY id DESC"
        ).fetchall()
        return list(rows)


def create_ticket(title: str, details: str) -> int:
    with db_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO tickets (title, details, status, created_at)
            VALUES (?, ?, 'open', ?)
            """,
            (title, details, utc_now()),
        )
        conn.commit()
        return int(cur.lastrowid)


def search_kb(query: str) -> list[sqlite3.Row]:
    with db_conn() as conn:
        if not query:
            rows = conn.execute(
                "SELECT id, title, body, created_at FROM kb_articles ORDER BY id DESC"
            ).fetchall()
        else:
            like = f"%{query}%"
            rows = conn.execute(
                """
                SELECT id, title, body, created_at
                FROM kb_articles
                WHERE title LIKE ? OR body LIKE ?
                ORDER BY id DESC
                """,
                (like, like),
            ).fetchall()
        return list(rows)


def dashboard_stats(owner_user_id: int) -> dict:
    with db_conn() as conn:
        campaigns = conn.execute(
            "SELECT COUNT(*) FROM campaigns WHERE owner_user_id = ?",
            (owner_user_id,),
        ).fetchone()[0]
        jobs = conn.execute(
            "SELECT COUNT(*) FROM render_jobs WHERE owner_user_id = ?",
            (owner_user_id,),
        ).fetchone()[0]
        tickets = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        queued = conn.execute(
            """
            SELECT COUNT(*)
            FROM render_jobs
            WHERE owner_user_id = ? AND status IN ('queued', 'running')
            """,
            (owner_user_id,),
        ).fetchone()[0]
        return {
            "campaigns": campaigns,
            "jobs": jobs,
            "tickets": tickets,
            "queued": queued,
        }
