# Local Development Setup

## 1. Purpose

This guide explains how to run, validate, and troubleshoot the ClinicOps SaaS application foundation in a local development environment.

The current foundation includes:

- Python 3.12;
- dependency management through `uv`;
- FastAPI;
- PostgreSQL;
- SQLAlchemy;
- Alembic;
- Ruff;
- mypy;
- pytest;
- Docker Compose;
- API and worker processes from the same application package.

---

## 2. Prerequisites

Install the following tools:

- Git;
- Docker Desktop or a compatible Docker Engine;
- `uv`.

Verify the installations:

```powershell
git --version
docker --version
docker compose version
uv --version
```

Python 3.12 can be installed and managed through `uv`:

```powershell
uv python install 3.12
```

---

## 3. Clone and Enter the Repository

```powershell
git clone <repository-url>
cd clinicops-saas
```

---

## 4. Create the Local Environment File

Copy the public environment template:

```powershell
Copy-Item .env.example .env
```

The `.env` file is intentionally excluded from version control.

The default local configuration uses:

```text
Database host: localhost
Database port: 5432
Database name: clinicops
Database user: clinicops
```

The example credentials are intended only for local development.

---

## 5. Install Dependencies

Synchronize the virtual environment with the committed lockfile:

```powershell
uv sync --locked --all-groups
```

This command creates the local `.venv` directory when necessary and installs both runtime and development dependencies.

Verify that the application package is importable:

```powershell
uv run python -c "import clinicops; print(clinicops.__doc__)"
```

Expected output:

```text
ClinicOps SaaS application package.
```

---

## 6. Start PostgreSQL

Start the local database service:

```powershell
docker compose up -d postgres
```

Inspect the container status:

```powershell
docker compose ps
```

Verify PostgreSQL readiness:

```powershell
docker compose exec postgres pg_isready -U clinicops -d clinicops
```

Expected output includes:

```text
accepting connections
```

---

## 7. Apply Database Migrations

Apply all committed migrations:

```powershell
uv run alembic upgrade head
```

Inspect the current migration revision:

```powershell
uv run alembic current
```

Inspect the migration head:

```powershell
uv run alembic heads
```

Check whether SQLAlchemy metadata requires an uncommitted migration:

```powershell
uv run alembic check
```

The application does not create or modify the schema automatically during
startup. Neither the API process nor the worker process runs Alembic
automatically. Migrations remain explicit operator actions.

---

## 8. Bootstrap the initial local user

ClinicOps uses invitation-based onboarding. Before the first tenant can be
created, bootstrap one global user:

```powershell
uv run python -m clinicops.cli.bootstrap_user
```

The command reads:

```env
CLINICOPS_BOOTSTRAP_USER_EMAIL=
CLINICOPS_BOOTSTRAP_USER_PASSWORD=
```

It creates the global identity and password credential through the identity
application service. It does not create a tenant or run automatically during
application startup.

Repeated execution for an existing email is idempotent and does not reset the
stored password.

For the complete behavior and troubleshooting guidance, see
[`local-bootstrap.md`](local-bootstrap.md).

---

## 9. Run the API

Start the FastAPI development server:

```powershell
uv run uvicorn clinicops.main:app --reload
```

The API is available at:

```text
http://127.0.0.1:8000
```

OpenAPI documentation is available at:

```text
http://127.0.0.1:8000/docs
```

Alternative API documentation is available at:

```text
http://127.0.0.1:8000/redoc
```

Stop the server with:

```text
Ctrl + C
```

---

## 10. Run the Worker

In a second terminal, start the background worker:

```powershell
uv run python -m clinicops.worker
```

The worker:

- shares the same PostgreSQL database as the API;
- claims and executes durable background jobs sequentially;
- recovers stale processing claims on a configured cadence;
- exposes no HTTP port.

Stop the worker with:

```text
Ctrl + C
```

SIGINT and SIGTERM request graceful shutdown. An active handler is allowed to
finish before the process exits.

Direct local execution of the three common processes:

```powershell
uv run alembic upgrade head
uv run uvicorn clinicops.main:app --reload
uv run python -m clinicops.worker
```

---

## 11. Health Endpoints

### Liveness

```text
GET /api/v1/health/live
```

The liveness endpoint confirms that the HTTP process is responding.

It does not access PostgreSQL.

Test it with:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/live
```

Expected response:

```text
status
------
ok
```

### Readiness

```text
GET /api/v1/health/ready
```

The readiness endpoint confirms that the application can connect to PostgreSQL and execute a minimal query.

Test it with:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/ready
```

Expected response:

```text
status
------
ok
```

When PostgreSQL is unavailable, the endpoint returns HTTP `503`.

The database connection timeout is controlled by:

```text
CLINICOPS_DATABASE_CONNECT_TIMEOUT_SECONDS
```

---

## 12. Inspect Registered API Paths

Use the generated OpenAPI schema to inspect public endpoints:

```powershell
uv run python -c "from clinicops.main import app; print(list(app.openapi()['paths']))"
```

To include HTTP methods:

```powershell
uv run python -c "from clinicops.main import app; print([(path, list(methods)) for path, methods in app.openapi()['paths'].items()])"
```

---

## 13. Run with Docker Compose

API and worker processes are built from the same Docker image. Compose
services are `postgres`, `api`, and `worker`.

Start PostgreSQL:

```powershell
docker compose up -d postgres
```

Apply migrations explicitly through the API image. Neither `api` nor `worker`
runs Alembic on startup:

```powershell
docker compose run --rm api alembic upgrade head
```

Start the API and worker:

```powershell
docker compose up -d api worker
```

Inspect service status:

```powershell
docker compose ps
```

Follow worker logs:

```powershell
docker compose logs -f worker
```

API logs can be inspected separately:

```powershell
docker compose logs -f api
```

Stop or start only the worker:

```powershell
docker compose stop worker
docker compose start worker
```

Stop the Compose stack while preserving the named PostgreSQL volume:

```powershell
docker compose down
```

The worker exposes no published HTTP port. The API remains available on host
port `8000` and serves:

```text
GET /api/v1/health/live
GET /api/v1/health/ready
```

Container `init` forwards SIGINT and SIGTERM to the worker process. The worker
service uses a stop grace period long enough for an active handler to finish.

To run multiple worker processes against the same queue, scale the `worker`
service. The current Compose file publishes no ports on `worker`, so scaling is
valid:

```powershell
docker compose up -d --scale worker=2 api worker
```

Do not invent additional worker services or shell scripts that are not present
in the repository.

---

## 14. Run Automated Tests

Ensure PostgreSQL is running:

```powershell
docker compose up -d postgres
```

Run the complete test suite:

```powershell
uv run pytest
```

Run tests with coverage:

```powershell
uv run pytest --cov=clinicops --cov-report=term-missing
```

Run a specific test area:

```powershell
uv run pytest tests\unit -v
uv run pytest tests\integration\api -v
uv run pytest tests\integration\db -v
```

Integration database tests use the local PostgreSQL instance configured through the environment.

---

## 15. Run Quality Checks

Format the code:

```powershell
uv run ruff format .
```

Verify formatting:

```powershell
uv run ruff format --check .
```

Run linting:

```powershell
uv run ruff check .
```

Run static type checking:

```powershell
uv run mypy src tests
```

Verify the lockfile:

```powershell
uv lock --check
```

Build the Python package:

```powershell
uv build
```

Run the complete local validation sequence:

```powershell
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run alembic upgrade head
uv run alembic check
uv run pytest --cov=clinicops --cov-report=term-missing
uv build
```

---

## 16. Create a Database Migration

After changing SQLAlchemy models, create a migration:

```powershell
uv run alembic revision --autogenerate -m "describe schema change"
```

Review the generated migration before applying it.

Then run:

```powershell
uv run alembic upgrade head
uv run alembic check
```

Generated migrations must not be accepted without reviewing their upgrade and downgrade operations.

---

## 17. Stop or Reset Local Infrastructure

Stop the PostgreSQL container while preserving its data:

```powershell
docker compose stop postgres
```

Stop and remove the Compose containers while preserving the named volume:

```powershell
docker compose down
```

PostgreSQL data remains durable unless volumes are explicitly removed.

Remove the containers and local database volume:

```powershell
docker compose down -v
```

`docker compose down -v` permanently deletes the local PostgreSQL data volume.

---

## 18. Common Troubleshooting

### PostgreSQL Port Conflict

If port `5432` is already in use, change `POSTGRES_PORT` in `.env`.

The application database URL must use the same host port.

Example:

```text
POSTGRES_PORT=5433
CLINICOPS_DATABASE_URL=postgresql+psycopg://clinicops:clinicops@localhost:5433/clinicops
```

Restart the service after changing the port:

```powershell
docker compose down
docker compose up -d postgres
```

### Worker Cannot Connect to PostgreSQL

Confirm PostgreSQL is healthy and the worker database URL points at the intended
host:

```powershell
docker compose ps
docker compose exec postgres pg_isready -U clinicops -d clinicops
uv run python -c "from clinicops.core.config import get_settings; print(get_settings().database_url)"
```

When using Compose, the API and worker override
`CLINICOPS_DATABASE_URL` to reach the `postgres` service hostname.

### Migrations Have Not Been Applied

Apply migrations explicitly before starting the API or worker:

```powershell
uv run alembic upgrade head
```

Or through Compose:

```powershell
docker compose run --rm api alembic upgrade head
```

Confirm the revision:

```powershell
uv run alembic current
```

### No Jobs Are Available

An idle worker is expected when the queue has no eligible work. Confirm the API
is receiving webhook deliveries and that jobs are being enqueued after
authenticated ingestion. Inspect API and worker logs separately:

```powershell
docker compose logs -f api
docker compose logs -f worker
```

### Job Remains Processing After a Forced Crash

A claimed job remains `processing` until its lease expires. The worker recovers
stale claims on its configured cadence. Wait for lease expiration and recovery,
or inspect the job row in PostgreSQL. Heartbeat or lease extension is not
implemented.

### Worker Receives an Unknown Job Type

Unknown job types are terminal failures. The worker dead-letters the job
through the explicit handler registry. Confirm the job type matches a
registered handler such as `billing.webhook.process`.

### Readiness Request Waits Too Long

Confirm that the following variable exists in `.env`:

```text
CLINICOPS_DATABASE_CONNECT_TIMEOUT_SECONDS=3
```

Restart the API process after changing configuration.

### Local Environment File Affects Tests

The test settings configuration ignores the local `.env` file.

Tests may still read explicitly defined process environment variables with the `CLINICOPS_` prefix.

Inspect them with:

```powershell
Get-ChildItem Env:CLINICOPS_*
```

### Migration Connection Failure

Confirm that PostgreSQL is healthy:

```powershell
docker compose ps
docker compose exec postgres pg_isready -U clinicops -d clinicops
```

Confirm the configured database URL:

```powershell
uv run python -c "from clinicops.core.config import get_settings; print(get_settings().database_url)"
```

### Recreate the Python Environment

Remove and recreate the local environment:

```powershell
Remove-Item -Recurse -Force .venv
uv sync --locked --all-groups
```

---

## 19. Local Development Checklist

Before starting application work:

```text
[ ] Dependencies are synchronized from uv.lock
[ ] PostgreSQL is healthy
[ ] Alembic is at the current head
[ ] The API starts successfully
[ ] The worker starts successfully
[ ] Liveness returns HTTP 200
[ ] Readiness returns HTTP 200
[ ] Formatting passes
[ ] Linting passes
[ ] Type checking passes
[ ] Tests pass
[ ] The package builds successfully
```