# Background Job Queue

## Overview

ClinicOps uses a PostgreSQL-backed durable job queue to coordinate asynchronous work without introducing a separate message broker into the current architecture.

The queue provides durable persistence, transactional enqueueing, safe concurrent claiming, retries, dead-letter handling, and stale-claim recovery. PostgreSQL remains the source of truth for both domain state and job execution state.

This foundation is designed for at-least-once execution. Job handlers must therefore be idempotent and must treat domain tables—not job rows—as the source of business truth.

## Architectural Position

The application remains a modular monolith with separate API and worker processes built from the same codebase and the same Docker image.

```text
API process
    -> persists domain state
    -> enqueues durable work in the same transaction

PostgreSQL
    -> stores domain records
    -> stores background job state
    -> coordinates concurrent claims

Worker process
    -> claims committed jobs
    -> invokes registered application handlers
    -> records success, retry, or dead-letter state
```

The repository implements both the durable PostgreSQL queue foundation and the worker runtime that executes registered handlers, including billing webhook processing.

## Responsibilities

The background job module owns:

- durable job persistence;
- execution lifecycle state;
- typed payload storage;
- idempotent enqueueing;
- availability scheduling;
- priority ordering;
- safe concurrent claiming;
- processing leases;
- claim ownership;
- retry scheduling;
- dead-letter state;
- stale-claim recovery;
- operational failure metadata;
- request and correlation traceability.

Domain modules own:

- business entities;
- business invariants;
- tenant ownership;
- provider state;
- subscription state;
- audit records;
- domain-specific idempotency.

A job coordinates execution. It does not duplicate domain state or become a second source of business truth.

## Persisted Model

The `background_jobs` table stores:

- `id`;
- `job_type`;
- `payload_version`;
- `payload`;
- `status`;
- `idempotency_key`;
- `priority`;
- `available_at`;
- `processing_attempt_count`;
- `max_attempts`;
- `worker_id`;
- `claim_token`;
- `claimed_at`;
- `lease_expires_at`;
- `last_error_code`;
- `last_error_message`;
- `last_failed_at`;
- `completed_at`;
- `dead_lettered_at`;
- `correlation_id`;
- `origin_request_id`;
- `created_at`;
- `updated_at`.

`job_type` is stored as a bounded string. Adding a new handler type is therefore an application change rather than a PostgreSQL enum migration.

`payload` is stored as JSONB. Payloads should contain durable resource identifiers and minimal handler input rather than copied business records or serialized ORM entities.

`payload_version` makes payload evolution explicit and allows handlers to reject unsupported schemas safely.

## Lifecycle

Supported statuses are:

```text
queued
processing
retry_scheduled
succeeded
dead_lettered
```

Supported transitions are:

```text
queued
    -> processing

retry_scheduled
    -> processing

processing
    -> succeeded

processing
    -> retry_scheduled

processing
    -> dead_lettered

stale processing
    -> retry_scheduled

stale processing
    -> dead_lettered
```

Terminal jobs are not claimable:

```text
succeeded
dead_lettered
```

Manual replay, cancellation, and administrative state mutation are intentionally outside the current foundation.

Database constraints enforce lifecycle consistency. Active processing rows require worker ownership, a claim token, claim timestamps, and a lease expiration. Queued, retry-scheduled, succeeded, and dead-lettered rows must not retain active claim ownership.

## Transaction Ownership

Repositories never commit or roll back.

Application services validate workflows, mutate state, and flush.

The caller owns commit or rollback.

This makes transactional enqueueing possible:

```text
Transaction
    -> persist durable domain event or state
    -> enqueue background job
    -> commit
```

The result is atomic:

```text
domain state and job both exist
or
neither exists
```

Claiming also uses an explicit transaction boundary:

```text
Claim transaction
    -> select and lock eligible rows
    -> mark rows as processing
    -> commit

Handler execution
    -> occurs after claim commit
    -> does not hold queue-selection locks

Completion or failure transaction
    -> lock the claimed job
    -> validate claim ownership
    -> record the transition
    -> commit
```

The worker runtime owns these transaction boundaries during job execution.

## Idempotent Enqueueing

A job may include a nullable `idempotency_key`.

For a new key:

```text
create the job
```

For an existing key with equivalent semantic work:

```text
return the existing job
```

For an existing key associated with different semantic work:

```text
raise an idempotency conflict
```

Semantic equivalence currently compares:

- `job_type`;
- `payload_version`;
- normalized JSON payload.

Operational metadata such as priority, correlation ID, request ID, availability, and maximum attempts does not redefine the identity of already-enqueued work.

Completed and dead-lettered jobs retain their idempotency keys. This prevents the same one-time operation from being recreated after reaching a terminal state.

The persistence adapter uses PostgreSQL conflict handling rather than recovering from an `IntegrityError` after a failed flush. This preserves the outer transaction for the caller.

## Eligible Job Claiming

Eligible jobs satisfy all of the following:

- status is `queued` or `retry_scheduled`;
- `available_at` is not in the future according to PostgreSQL;
- `processing_attempt_count` is lower than `max_attempts`.

Selection order is:

1. priority descending;
2. availability ascending;
3. creation time ascending;
4. job ID ascending.

Workers claim rows using:

```sql
FOR UPDATE SKIP LOCKED
```

This allows multiple worker processes to claim separate rows concurrently without waiting on rows already locked by another claimant.

Claiming updates:

- status to `processing`;
- attempt count by one;
- worker ID;
- a new claim token;
- claim timestamp;
- lease expiration.

The claim transaction must commit before a handler executes.

## Claim Ownership

Every successful claim receives a new UUID `claim_token`.

Completion and failure operations require:

- job ID;
- worker ID;
- claim token.

The transition service locks the job row and verifies all ownership data before mutating state.

This prevents a stale worker from completing or failing work after:

```text
its lease expired
    -> stale recovery released the job
    -> another worker claimed it
    -> a new claim token was issued
```

A previous worker ID and claim token cannot mutate a newer claim.

## Processing Leases

A claimed job receives `lease_expires_at`.

A processing job is stale when:

```text
status = processing
and
lease_expires_at <= PostgreSQL current time
```

The initial design assumes short-lived handlers that finish within a conservative lease.

Heartbeat and lease-extension support are intentionally deferred until a measured handler duration requires them.

## Successful Completion

A valid claimant may transition a processing job to `succeeded`.

Completion:

- records `completed_at` using PostgreSQL time;
- clears worker ownership;
- clears the claim token;
- clears claim and lease timestamps;
- preserves earlier failure metadata for operational history.

No handler-result payload is stored. Domain records remain the source of business results.

## Failure Classification

Execution failures are classified as:

```text
retryable
terminal
```

Retryable failures include temporary conditions such as provider unavailability, network interruption, or a dependency that may become available later.

Terminal failures include malformed payloads, unsupported payload versions, unknown job types, invalid durable resource identity, or business states that cannot be retried safely.

A retryable failure schedules another attempt while:

```text
processing_attempt_count < max_attempts
```

A retryable failure at the maximum attempt count becomes dead-lettered.

A terminal failure becomes dead-lettered immediately.

Persisted failure metadata is bounded and sanitized before storage.

## Retry Policy

Retries use capped exponential backoff with equal jitter.

Conceptually:

```text
exponential delay
    = base delay * 2^(attempt number - 1)

capped delay
    = minimum(exponential delay, maximum delay)

equal-jitter delay
    = half of capped delay
      + randomized value between zero and the remaining half
```

This avoids immediate retry loops and reduces synchronized retry bursts across workers.

The random-value provider is injectable so retry behavior remains deterministic under tests.

## Stale-Claim Recovery

Recovery selects expired processing rows using:

```sql
FOR UPDATE SKIP LOCKED
```

Selection order is:

1. lease expiration ascending;
2. creation time ascending;
3. job ID ascending.

An expired claim has already consumed one attempt because the original claim was committed before handler execution.

Recovery records a stable operational failure:

```text
processing_lease_expired
```

Then it transitions the job to:

```text
retry_scheduled
```

when attempts remain, or:

```text
dead_lettered
```

when the maximum attempt count has been reached.

Recovery clears all previous claim ownership. The old claim token is never reused.

Concurrent recovery processes skip rows already locked by another recovery transaction.

## Delivery Semantics

The queue provides at-least-once execution.

It does not claim exactly-once execution.

A critical crash window remains possible:

```text
handler applies a domain change
    -> worker crashes
    -> job success is not recorded
    -> lease expires
    -> job is executed again
```

Handlers must therefore be idempotent.

The existing billing webhook processor and reconciliation service already contain persistence, ordering, and replay protections that support this delivery model.

## Database Time

PostgreSQL time is authoritative for:

- job availability;
- claim timestamps;
- lease expiration;
- stale-job detection;
- completion timestamps;
- failure timestamps;
- dead-letter timestamps.

This avoids inconsistent execution decisions caused by clock skew between API and worker processes.

The pure retry-delay calculation remains application-side and independently testable.

## Indexing

The queue defines a unique partial index for non-null idempotency keys.

Eligible-job polling is supported by a partial index over:

- priority;
- availability;
- creation time;
- job ID;

for queued and retry-scheduled jobs.

Stale recovery is supported by a partial index over:

- lease expiration;
- creation time;
- job ID;

for processing jobs.

These indexes align with the ordering and filters used by the queue repositories.

## Security

Job payloads must not contain:

- passwords;
- access tokens;
- refresh tokens;
- provider credentials;
- webhook-signing secrets;
- raw credential material;
- arbitrary provider URLs;
- serialized ORM objects;
- duplicated full webhook payloads.

Payloads should contain durable local identifiers such as a billing webhook event ID or subscription ID.

Persisted error messages must be bounded, sanitized, and appropriate for operational diagnosis.

`correlation_id` is required for end-to-end traceability.

`origin_request_id` is optional because some jobs originate from scheduled or recovery workflows rather than HTTP requests.

The current foundation does not expose a public job-administration API.

## Concurrency and Failure Testing

The background job foundation includes PostgreSQL integration tests for:

- concurrent idempotent enqueueing;
- concurrent batch claiming;
- claim rollback;
- completion and failure row locking;
- retry followed by later success;
- retry exhaustion and dead-lettering;
- terminal failure without unnecessary attempts;
- stale recovery;
- concurrent stale-recovery processes;
- stale recovery versus claiming;
- late completion after recovery;
- concurrent completion versus terminal failure;
- transaction rollback after lifecycle mutation.

Concurrent tests use independent SQLAlchemy sessions and deterministic synchronization primitives rather than timing-based sleeps.

## Worker Runtime

The worker process is composed in `clinicops.worker` and started with:

```text
python -m clinicops.worker
```

Composition responsibilities:

- load validated application settings;
- create the SQLAlchemy session factory;
- build the explicit job-handler registry;
- resolve worker identity;
- install SIGINT and SIGTERM shutdown handlers;
- run the sequential execution loop until stop is requested.

### Handler Protocol and Registry

Handlers implement an explicit protocol:

```text
job_type
supported_payload_version
execute(claimed_job)
```

Handlers are registered in an explicit in-process registry. Resolution is deterministic by `job_type`.

Unknown job types and unsupported payload versions are terminal failures. Job payloads cannot select import paths, class names, or executable code. Dynamic handler loading is intentionally excluded.

The current registry includes:

```text
billing.webhook.process
```

### Worker Identity and Settings

Worker identity is either:

- an explicit `CLINICOPS_WORKER_ID`;
- or a generated value based on hostname, process ID, and a short unique suffix.

Worker runtime settings:

```text
CLINICOPS_WORKER_ID
CLINICOPS_WORKER_POLL_INTERVAL_SECONDS
CLINICOPS_WORKER_LEASE_SECONDS
CLINICOPS_WORKER_STALE_RECOVERY_INTERVAL_SECONDS
CLINICOPS_WORKER_STALE_RECOVERY_BATCH_SIZE
CLINICOPS_WORKER_RETRY_BASE_DELAY_SECONDS
CLINICOPS_WORKER_RETRY_MAXIMUM_DELAY_SECONDS
```

Defaults match local development needs: one-second idle polling, a five-minute lease, sixty-second stale-recovery cadence, and capped exponential retry delays.

### Iteration Flow

Each iteration:

1. recovers stale processing claims when the recovery interval is due;
2. claims at most one eligible job;
3. resolves the registered handler;
4. executes the handler after the claim transaction has committed;
5. records completion or failure in a separate queue transaction.

When no job is available, the worker waits on an interruptible idle poll using the configured poll interval. SIGINT and SIGTERM set a stop event so idle waiting can end promptly. An in-flight handler is allowed to finish before process exit.

One worker process executes one claimed job at a time. Horizontal throughput comes from running multiple worker processes against the same PostgreSQL queue.

### Independent Transaction Boundaries

Worker execution separates three database boundaries:

```text
Claim transaction
    -> select and lock one eligible job
    -> mark the job as processing
    -> commit

Handler execution
    -> uses an independent domain session and transaction
    -> does not hold queue-selection locks
    -> must not call providers while queue row locks are held

Completion or failure transaction
    -> lock the claimed job
    -> validate worker ID and claim token
    -> record succeeded, retry_scheduled, or dead_lettered
    -> commit
```

Billing webhook handling follows this model: the claim commits first,
`ProcessBillingWebhookEventService` runs in a fresh billing transaction that
includes outcome audit recording, and queue completion or failure is recorded
afterward. Audit failure rolls back billing/webhook state and prevents
successful handler completion. Job-completion failure after billing commit may
cause replay; equivalent replay reuses the already committed semantic audit
fact. ClinicOps does not claim that job completion and billing mutation are
atomically committed together, and it does not claim exactly-once execution.

### Failure Classification and Claim Loss

Registered handler failures are classified as retryable or terminal.

Unexpected exceptions raised by a handler are treated as retryable so temporary faults can be retried within attempt limits.

Unknown job types and unsupported payload versions are terminal.

If completion or failure recording observes that claim ownership has been lost—for example after stale recovery and a newer claim—the worker records a claim-loss outcome and does not retry the obsolete claim token.

### Structured Logging

Worker and job execution logs include structured context such as:

- worker ID;
- job ID;
- job type;
- processing attempt count;
- maximum attempts;
- claim token;
- correlation ID;
- origin request ID;
- outcome;
- duration.

Raw job payloads and secrets are not logged.

### Billing Webhook Job Integration

Authenticated webhook ingestion enqueues:

```text
job_type = billing.webhook.process
payload = { webhook_event_id }
idempotency_key = billing-webhook-process:<webhook_event_id>
```

in the same HTTP transaction that persists `BillingWebhookEvent`.

Duplicate provider deliveries reuse one durable event and one semantic job. The public `202 Accepted` response does not expose the job ID. Billing domain tables remain the source of truth; the job row coordinates execution only.

Worker execution builds system audit attribution from durable job context:

```text
actor type = system
source = worker
correlation ID = durable job correlation ID
request ID = origin request ID when present, otherwise null
```

Processed and ignored outcomes record one semantic audit fact per webhook event
and outcome. At-least-once job delivery plus idempotent domain processing plus
idempotent audit recording remain the supported guarantee. ClinicOps does not
claim exactly-once execution.

End-to-end HTTP-to-worker processing tests and at-least-once replay-safety tests cover this path. Billing lifecycle details remain in `docs/architecture/billing-lifecycle.md`.

### Container Execution Model

API and worker processes are built from the same Docker image.

Compose services:

```text
postgres
api
worker
```

The worker command is `python -m clinicops.worker`. The worker exposes no HTTP port. PostgreSQL health is a dependency for both API and worker. Container `init` forwards signals so graceful shutdown reaches the worker process. Migrations remain explicit operator actions; neither API nor worker runs Alembic automatically.

## Current Implementation Boundary

Implemented:

- durable `BackgroundJob` persistence;
- Alembic migration and indexes;
- typed enqueue, claim, completion, failure, and recovery contracts;
- JSON payload normalization;
- idempotent enqueueing;
- PostgreSQL-backed availability selection;
- `FOR UPDATE SKIP LOCKED` claiming;
- processing attempts and leases;
- claim-token ownership;
- successful completion;
- retryable and terminal failure handling;
- capped exponential backoff with equal jitter;
- dead-letter state;
- stale-claim recovery;
- explicit job-handler protocol;
- explicit job-handler registry;
- deterministic handler resolution;
- unknown-job-type and unsupported-payload-version terminal failure;
- worker identity generation and configuration;
- worker runtime settings;
- sequential one-job-at-a-time execution per process;
- horizontal scaling through multiple worker processes;
- periodic stale-claim recovery cadence;
- interruptible idle polling;
- graceful shutdown through SIGINT and SIGTERM;
- claim commit before handler execution;
- independent billing and queue transaction boundaries;
- unexpected handler exceptions classified as retryable;
- claim-loss detection;
- structured worker and job execution logging;
- typed billing webhook job payload;
- `billing.webhook.process` handler;
- concrete SQLAlchemy billing webhook processor;
- atomic billing webhook ingestion and job enqueueing;
- stable event-derived job idempotency key;
- duplicate provider delivery reusing one durable event and one semantic job;
- worker system audit attribution with correlation and origin-request propagation;
- billing transaction plus audit recording before separate job completion;
- end-to-end HTTP-to-worker billing processing tests;
- at-least-once replay safety tests;
- worker process entrypoint `python -m clinicops.worker`;
- API and worker processes from the same Docker image;
- Compose `postgres`, `api`, and `worker` services;
- unit, integration, failure-path, and concurrency tests.

Intentionally deferred:

- periodic billing subscription reconciliation scheduling;
- generic recurring-job scheduler;
- reconciliation job producer;
- public job-administration API;
- manual replay API;
- job cancellation;
- in-process parallel handler execution;
- heartbeat or lease extension;
- external message broker;
- metrics backend;
- distributed tracing backend;
- frontend worker controls.

## Intentional Technology Choice

PostgreSQL was selected because the current architecture requires transactional coupling between domain persistence and job enqueueing.

A dedicated broker may become appropriate when measured throughput, latency, workload isolation, or operational requirements justify the added infrastructure.

The current decision prioritizes:

- transactional reliability;
- operational simplicity;
- local reproducibility;
- clear failure semantics;
- production-minded concurrency behavior;
- architecture that can be explained and defended.