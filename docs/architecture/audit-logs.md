# Durable Audit Logs

## Overview

ClinicOps maintains a durable, tenant-scoped audit history for committed business and security-sensitive state changes.

Audit logs answer historical questions such as:

- Who performed an operation?
- Which tenant owned the affected resource?
- Which resource changed?
- What safe contextual information describes the transition?
- Which request or asynchronous workflow produced the change?
- When was the historical fact committed?

Audit logs are persisted in PostgreSQL and participate in the same local transaction as the corresponding domain mutation.

```text
authorize and validate
    -> mutate domain state
    -> flush
    -> record audit entry using the same SQLAlchemy Session
    -> route commits once
```

The local transaction either commits both the business state and the audit entry or commits neither.

ClinicOps does not claim distributed atomicity between PostgreSQL and external payment providers. Where provider success can precede a later local failure, the project retains its existing reconciliation trade-off.

## Implementation Status

Durable Audit Logs are fully implemented for the approved current-release boundary.

The milestone includes:

- tenant-scoped `AuditLogEntry` persistence;
- PostgreSQL migration `0009`;
- explicit user and system actor contracts;
- explicit HTTP, worker, CLI, and system sources;
- database-enforced actor consistency;
- bounded and deterministic JSON metadata normalization;
- explicit metadata versioning;
- domain audit emission for tenancy, invitations, and billing;
- worker-originated billing webhook audit emission;
- idempotent recording;
- semantic replay conflict detection;
- insert-only repository behavior;
- caller-owned transaction boundaries for ordinary HTTP mutations;
- rollback coupling with domain mutations;
- tenant-scoped lookup;
- cursor-based tenant timeline queries;
- action and resource filters;
- opaque cursor transport;
- tenant-scoped OWNER/ADMIN read API;
- audit read authorization;
- safe public response contracts;
- Problem Details error mapping;
- concurrency, rollback, authorization, pagination, and integration tests;
- application-surface append-only behavior.

## Audit Logs and Operational Logs

Operational logs and audit logs serve different responsibilities.

### Operational logs

Operational logs describe runtime behavior:

- request outcomes;
- latency;
- exceptions;
- retry attempts;
- worker startup and shutdown;
- temporary infrastructure failures;
- provider call failures;
- job claim and lease behavior.

They support debugging, monitoring, and incident investigation.

### Audit logs

Audit logs describe committed historical facts:

- actor attribution;
- tenant attribution;
- resource attribution;
- committed business transitions;
- security-sensitive changes;
- safe before-and-after context;
- request and correlation traceability.

Audit entries are not created merely because an operation was attempted. They represent state changes that reached the transaction boundary successfully.

Denied operations, validation failures, and no-op outcomes do not emit misleading historical facts. Those remain operational or security log concerns unless a dedicated security-event model is introduced later.

## Architectural Boundary

The audit module is part of the ClinicOps modular monolith.

It does not own domain authorization or business invariants. Calling domain services remain responsible for deciding whether an operation is valid and permitted.

The audit module owns:

- audit actor validation;
- source classification;
- metadata validation;
- durable persistence;
- idempotent recording;
- semantic replay comparison;
- recording context helpers;
- `AuditRecorder` / `SqlAlchemyAuditRecorder` composition;
- tenant-scoped retrieval;
- opaque cursor encode and decode;
- read authorization policy for audit history;
- public read response schemas.

The audit module does not own:

- tenant authorization for domain mutations;
- role-based access control for non-audit resources;
- domain state transitions;
- application logging;
- metrics;
- distributed tracing;
- event sourcing;
- domain-state reconstruction;
- request-body storage;
- raw webhook-payload storage;
- retention execution.

## Transaction Ownership

Audit recording follows the same transaction ownership rules used throughout ClinicOps.

```text
repository
    -> executes persistence operations
    -> never commits
    -> never rolls back

application service
    -> validates
    -> creates or reuses the audit entry
    -> flushes

runtime caller
    -> commits or rolls back
```

Implemented domain mutation flow:

```text
authorize and validate
    -> mutate domain state
    -> flush
    -> record audit entry using the same SQLAlchemy Session
    -> route commits once
```

Domain recording uses:

```text
AuditRecordingContext
AuditRecorder
SqlAlchemyAuditRecorder
RecordAuditLogService
```

### Guarantees

- successful domain state and audit history commit together in the same local transaction;
- audit persistence failure prevents the corresponding local domain commit;
- caller rollback removes both pending domain changes and audit entries;
- tenancy and invitation services do not commit or roll back;
- authenticated billing services may own intermediate commits for provider orchestration, but leave the final local subscription mutation and audit entry for the HTTP route to commit;
- mutation routes remain transaction owners for the final auditable state;
- rejected and no-op operations do not emit misleading historical facts.

Billing webhook processing records the outcome audit entry inside the billing/webhook transaction before that transaction commits. Job completion remains a separate queue transaction.

### Successful transaction

```text
domain mutation succeeds
audit insert succeeds
caller commits
    -> both become durable
```

### Audit persistence failure

```text
domain mutation is pending
audit insert fails
caller rolls back
    -> neither becomes durable
```

### Caller rollback

```text
domain mutation is pending
audit entry is pending
caller rolls back
    -> neither becomes durable
```

The audit history must not claim that a state change occurred when that state change was rolled back.

## Persistence Model

Audit entries are stored in:

```text
audit_log_entries
```

The primary persisted fields are:

```text
id
tenant_id

actor_type
actor_user_id
actor_role

source

action

resource_type
resource_id

metadata_version
metadata

idempotency_key

request_id
correlation_id

recorded_at
```

### Tenant ownership

Every audit entry belongs to exactly one tenant.

`tenant_id` is required for persistence and for every repository read primitive.

There is no global or platform-wide audit history.

### Actor attribution

Supported actor types are:

```text
user
system
```

A user actor requires:

```text
actor_type = user
actor_user_id = non-null
```

A system actor requires:

```text
actor_type = system
actor_user_id = null
actor_role = null
```

`actor_role` is an optional historical snapshot of the effective tenant role at the time of a user operation.

It is not used for future authorization decisions.

### Sources

Supported technical sources are:

```text
http
worker
cli
system
```

Actor type and source describe different dimensions.

For example:

```text
actor_type = system
source = worker
```

represents an automated operation executed by a background worker.

## Implemented Action Catalog

Actions are stable application strings rather than PostgreSQL enum values.

The implemented catalog is:

```text
tenant.created
tenant.ownership_transferred

membership.role_changed
membership.removed

invitation.created
invitation.accepted
invitation.revoked

billing.subscription.created
billing.subscription.plan_changed
billing.subscription.cancelled

billing.webhook.processed
billing.webhook.ignored

patient.created
patient.updated
patient.archived
patient.restored
```

New audited domain operations can be introduced without requiring an enum migration for every action.

## Implemented Resource Catalog

Stable resource types are:

```text
tenant
membership
invitation
subscription
billing_webhook_event
patient
```

Each entry stores:

```text
resource_type
resource_id
```

`resource_id` is a bounded string historical reference. Preferred values are durable local resource identifiers.

Resource identifiers do not grant authorization and must never substitute for tenant-scoped access checks.

### Timestamp

`recorded_at` is generated by PostgreSQL.

Database time is authoritative for persisted ordering.

## Actor Attribution by Runtime

### Authenticated HTTP operations

Trusted runtime attribution for authenticated tenant mutations:

```text
actor type = user
actor user ID = authenticated/authorized principal
actor role = trusted tenant-role snapshot
source = http
```

Role values are persisted as historical lowercase tenant-role strings such as `owner`, `admin`, and `staff`.

The role snapshot is historical context only.

### Public invitation acceptance

Public capability-token acceptance is not an authenticated user request.

```text
actor type = system
actor user ID = null
actor role = null
source = http
```

The accepted user ID and role are stored only in approved safe metadata.

The invitation token does not prove an authenticated user actor.

### Billing webhook worker

Worker-originated billing webhook processing uses:

```text
actor type = system
actor user ID = null
actor role = null
source = worker
```

Trace context is propagated from durable job context:

- correlation ID from the durable job;
- originating request ID when available;
- `null` request ID when no originating request exists.

## Metadata

Audit metadata contains explicit, safe context required to explain the historical transition.

The PostgreSQL column is named:

```text
metadata
```

The SQLAlchemy model maps it through the Python attribute:

```text
event_metadata
```

This avoids collision with SQLAlchemy declarative metadata.

### Implemented safe metadata shapes

```json
{
  "tenant_name": "Northstar Health Clinic"
}
```

```json
{
  "target_user_id": "uuid",
  "previous_role": "staff",
  "new_role": "admin"
}
```

```json
{
  "target_user_id": "uuid",
  "removed_role": "staff"
}
```

```json
{
  "previous_owner_user_id": "uuid",
  "new_owner_user_id": "uuid"
}
```

```json
{
  "invited_role": "admin"
}
```

```json
{
  "accepted_user_id": "uuid",
  "accepted_role": "admin"
}
```

```json
{
  "plan": "starter",
  "status": "active"
}
```

```json
{
  "previous_plan": "starter",
  "new_plan": "professional"
}
```

```json
{
  "previous_status": "active",
  "new_status": "canceled"
}
```

```json
{
  "event_type": "subscription.updated",
  "processing_outcome": "processed"
}
```

```json
{ "status": "active", "version": 1 }
```

```json
{ "version": 2, "changed_fields": ["email", "phone"] }
```

```json
{ "previous_status": "active", "new_status": "archived", "version": 3 }
```

```json
{ "previous_status": "archived", "new_status": "active", "version": 4 }
```

Subscription status values use the persisted enum spelling `canceled`. The audit action name remains `billing.subscription.cancelled`.

Metadata does not contain full domain snapshots.

## Metadata Validation

Only JSON-native values are accepted:

- null;
- boolean;
- integer;
- finite floating-point value;
- string;
- array;
- object with string keys.

The normalizer rejects implicit object conversion.

Examples of rejected values include:

- UUID objects;
- datetime objects;
- Decimal objects;
- bytes;
- tuples;
- sets;
- ORM entities;
- custom objects;
- NaN;
- positive infinity;
- negative infinity.

Callers must explicitly convert durable identifiers to strings.

### Structural limits

The metadata policy enforces:

```text
maximum nesting depth:       6
maximum keys per object:     50
maximum items per array:     100
maximum object-key length:   100 characters
maximum string length:       2,000 characters
maximum serialized size:     16 KiB
```

Objects are normalized deterministically before persistence and semantic comparison.

### Prohibited content

Audit metadata intentionally excludes:

- passwords and password hashes;
- access and session tokens;
- invitation tokens and token hashes;
- authorization headers;
- webhook signatures;
- raw HTTP request bodies;
- raw webhook payloads;
- provider credentials and secrets;
- payment data;
- provider response snapshots;
- exception tracebacks;
- ORM objects;
- arbitrary full entity snapshots;
- internal audit idempotency keys.

The audit service enforces structural safety and JSON compatibility.

Calling modules remain responsible for selecting explicit metadata that is safe to persist.

## Recording Service

Audit entries are recorded through:

```text
RecordAuditLogService
```

Runtime callers typically compose recording through:

```text
SqlAlchemyAuditRecorder
```

which implements the `AuditRecorder` protocol and builds `RecordAuditLogService` against the caller's `Session`.

The service receives an immutable `RecordAuditLogCommand` containing:

```text
tenant_id
actor
source
action
resource_type
resource_id
correlation_id
metadata_version
metadata
idempotency_key
request_id
```

The service:

1. validates the command;
2. normalizes bounded string fields;
3. validates and normalizes metadata;
4. creates a candidate `AuditLogEntry`;
5. inserts or reuses the entry;
6. flushes the session;
7. verifies semantic equivalence for replay;
8. returns the durable ID, timestamp, and creation status.

The service never commits or rolls back.

## Domain Integration Flows

### Tenancy

Implemented emissions:

- `tenant.created` when a tenant is created;
- `tenant.ownership_transferred` after ownership transfer;
- `membership.role_changed` when a membership role actually changes;
- `membership.removed` when a membership is removed.

Authenticated HTTP routes supply `AuditRecordingContext.http_user(...)`.

No-op role assignments that leave the role unchanged do not emit an audit entry.

### Invitations

Implemented emissions:

- `invitation.created`;
- `invitation.accepted`;
- `invitation.revoked`.

Issue and revoke use authenticated HTTP user attribution.

Acceptance uses the public HTTP system actor contract described above.

### Authenticated billing lifecycle

Implemented emissions:

- `billing.subscription.created`;
- `billing.subscription.plan_changed`;
- `billing.subscription.cancelled`.

These records use authenticated HTTP user attribution and safe plan or status metadata.

Provider calls remain outside the final local auditable commit. ClinicOps does not claim external-provider and PostgreSQL distributed atomicity. Reconciliation remains the recovery path when provider success precedes a later local failure.

### Worker webhook outcomes

Implemented emissions:

- `billing.webhook.processed`;
- `billing.webhook.ignored`.

These records use worker system attribution and include safe `event_type` and `processing_outcome` metadata.

### Patients

See [Patients Domain](patients.md).

Implemented emissions:

- `patient.created`;
- `patient.updated`;
- `patient.archived`;
- `patient.restored`.

Authenticated HTTP routes supply `AuditRecordingContext.http_user(...)`.

Patient audit recording uses the same SQLAlchemy Session as the patient mutation. The service records the audit entry before the HTTP route commits. Audit persistence failure prevents the mutation from committing.

Read and list operations do not emit audit events.

Patient audit metadata excludes:

- `full_name`;
- `email`;
- `phone`;
- `date_of_birth`;
- `external_reference` values.

## Idempotency and Replay

`idempotency_key` is optional and internal.

It is persisted for replay protection and is not part of the public audit read response.

### Deterministic keys currently used

```text
tenant-created:{tenant_id}
invitation-created:{invitation_id}
invitation-accepted:{invitation_id}
invitation-revoked:{invitation_id}
subscription-created:{subscription_id}
subscription-plan-changed:{subscription_id}:{client_idempotency_key}
subscription-cancelled:{subscription_id}:{client_idempotency_key}
billing-webhook-audit:{webhook_event_id}:processed
billing-webhook-audit:{webhook_event_id}:ignored
patient-created:{patient_id}
patient-updated:{patient_id}:{version}
patient-archived:{patient_id}:{version}
patient-restored:{patient_id}:{version}
```

Role changes, membership removal, and ownership transfer intentionally omit synthetic audit keys because those domain operations have no durable operation identifier suitable for semantic replay reuse.

Plan change and cancellation derive audit keys from the subscription identifier plus the client billing idempotency key already used for provider-operation replay protection.

### Entry without an idempotency key

When no key is supplied, each service execution appends a new historical entry.

```text
execute
    -> new entry

execute again
    -> another new entry
```

### First execution with a key

When the key does not yet exist:

```text
insert entry
return created = true
```

### Equivalent replay

When the key already exists and the semantic event is equivalent:

```text
reuse existing entry
return created = false
```

The semantic identity includes:

- tenant ID;
- actor type;
- actor user ID;
- actor role;
- source;
- action;
- resource type;
- resource ID;
- metadata version;
- normalized metadata.

Transport identifiers do not redefine an existing historical fact:

- request ID;
- correlation ID;
- recorded timestamp.

The first committed request and correlation identifiers remain preserved.

### Conflicting replay

When the same key refers to a different semantic event:

```text
raise AuditLogIdempotencyConflictError
```

Semantic conflict for the same idempotency key is not silently ignored.

The caller transaction remains responsible for rollback.

### Concurrent recording

The repository uses PostgreSQL conflict-safe insertion rather than relying on an `IntegrityError` recovery path.

```text
INSERT
ON CONFLICT DO NOTHING
RETURNING id
```

Concurrent equivalent writers resolve one durable entry.

Concurrent conflicting writers produce one winner and one semantic conflict.

### Supported worker guarantee

ClinicOps does not claim exactly-once execution.

The supported worker guarantee is:

```text
at-least-once job delivery
    + idempotent domain processing
    + idempotent audit recording
```

Equivalent replay reuses the existing audit fact.

## Billing Webhook Transaction Boundary

Billing webhook job execution uses this boundary:

```text
job claimed
    -> billing transaction opens
    -> billing/webhook state changes
    -> audit entry recorded
    -> billing transaction commits
    -> handler returns
    -> job completion persists separately
```

Consequences:

- audit failure rolls back billing/webhook state and prevents successful handler completion;
- existing job retry behavior remains active;
- job-completion failure after billing commit may cause replay;
- replay reuses the already committed semantic audit fact;
- job completion remains intentionally separate from the billing transaction.

ClinicOps does not claim that job completion and billing mutation are atomically committed together.

## Append-Only Application Surface

Audit entries represent immutable historical facts.

The audit module intentionally exposes no application operation for:

- updating an entry;
- replacing metadata;
- changing actor attribution;
- changing the timestamp;
- deleting an entry.

There is no public audit-write endpoint.

The repository exposes no:

```text
update
delete
commit
rollback
get_all
list_all
```

The model does not include:

```text
updated_at
deleted_at
version
```

This is an application-surface append-only guarantee.

A database superuser can still execute direct SQL modifications. Production hardening should use a restricted runtime database role that cannot update or delete rows from `audit_log_entries`.

That deployment permission model is intentionally separate from the application migration.

## Tenant-Scoped Read API

Implemented endpoint:

```text
GET /api/v1/tenants/{tenant_id}/audit-logs
```

There is no global audit endpoint.

### Authorization

```text
OWNER -> allowed
ADMIN -> allowed
STAFF -> denied
```

The request must remain:

- authenticated;
- tenant-scoped;
- membership-scoped;
- permission-checked through `audit_log:read`.

Authorization starts from the authorized tenant ID in the request path. Resource IDs and actor-role snapshots never bypass that check.

### Query parameters

```text
limit
cursor
action
resource_type
resource_id
```

Constraints:

```text
default limit = 50
1 <= limit <= 100
resource_id requires resource_type
```

### Opaque cursor transport

The cursor is URL-safe Base64 transport over a small JSON payload containing:

```text
recorded_at
audit_log_id
```

Clarify:

- the cursor is not an authorization credential;
- tenant scope is always taken from the authorized request;
- the token is not encrypted or signed because it contains pagination position rather than secret data;
- malformed cursors return a client-safe Problem Details response.

### Stable ordering

Timeline queries use:

```text
recorded_at DESC
id DESC
```

The UUID tie-breaker provides stable ordering when multiple entries share the same database timestamp.

The next-page predicate is:

```text
recorded_at < cursor.recorded_at
OR
(
    recorded_at = cursor.recorded_at
    AND id < cursor.audit_log_id
)
```

The repository fetches one additional row to determine whether another page exists.

Offset pagination is intentionally not used for potentially large tenant histories.

### Filters

The API and repository support optional filtering by:

- action;
- resource type;
- resource ID.

A resource ID filter requires a resource type.

Arbitrary JSON metadata filtering, actor filtering, and date-range filtering are not currently supported.

A metadata GIN index is intentionally deferred until measured query requirements justify it.

### Returned records

Read operations return immutable application records rather than mutable SQLAlchemy entities.

Metadata is copied before returning so consumers cannot mutate the ORM JSON structure through a returned record.

## Public Response Contract

Example response:

```json
{
  "items": [
    {
      "id": "uuid",
      "tenant_id": "uuid",
      "actor": {
        "type": "user",
        "user_id": "uuid",
        "role": "admin"
      },
      "source": "http",
      "action": "membership.role_changed",
      "resource": {
        "type": "membership",
        "id": "uuid"
      },
      "metadata_version": 1,
      "metadata": {
        "previous_role": "staff",
        "new_role": "admin",
        "target_user_id": "uuid"
      },
      "request_id": "request-id",
      "correlation_id": "correlation-id",
      "recorded_at": "ISO-8601 timestamp"
    }
  ],
  "next_cursor": null
}
```

Serialized enum fields such as `actor.type` and `source` use lowercase values.

`idempotency_key` is not part of the public response.

## Problem Details

Audit API errors use:

```text
application/problem+json
```

Responses preserve:

- stable error code;
- client-safe public detail;
- request ID;
- correlation ID;
- trace response headers.

Status mapping:

```text
invalid cursor or query configuration -> HTTP 400
request-shape validation such as out-of-range limit -> HTTP 422
access denial -> HTTP 403
unauthenticated request -> HTTP 401
```

Internal cursor parsing or persistence diagnostics must not be exposed.

## Database Constraints and Indexes

PostgreSQL constraints protect:

- supported actor types;
- supported sources;
- valid actor combinations;
- metadata version greater than or equal to one;
- non-blank required identifiers;
- non-blank optional identifiers when supplied;
- required tenant ownership;
- user and tenant foreign-key integrity;
- unique non-null idempotency keys.

Indexes support:

### Tenant timeline

```text
tenant_id
recorded_at DESC
id DESC
```

### Tenant action history

```text
tenant_id
action
recorded_at DESC
id DESC
```

### Tenant resource history

```text
tenant_id
resource_type
resource_id
recorded_at DESC
id DESC
```

### Idempotency

```text
UNIQUE idempotency_key
WHERE idempotency_key IS NOT NULL
```

## Foreign-Key Policy

`tenant_id` references the tenant with:

```text
ON DELETE RESTRICT
```

`actor_user_id` references the user with:

```text
ON DELETE RESTRICT
```

Committed historical attribution must not be silently removed or detached by deleting a tenant or user.

Resource identifiers are intentionally not foreign keys.

Audit history should remain readable when domain resources evolve, are archived, or use different identifier formats.

## Retention

The initial retention policy is:

```text
retain indefinitely
```

The current release does not include:

- automatic deletion;
- retention workers;
- archival jobs;
- tenant-configurable retention;
- export;
- legal-hold workflows.

A future retention policy must consider:

- regulatory expectations;
- tenant contracts;
- storage cost;
- privacy obligations;
- legal hold;
- export requirements.

## Scaling

Current indexes are tenant-first because tenant-scoped timelines are the approved read pattern.

The table is naturally partitionable by tenant and time, but partitioning is intentionally deferred until measured storage or query behavior requires it.

The current design avoids premature:

- table partitioning;
- metadata GIN indexes;
- separate audit storage;
- event-stream infrastructure.

PostgreSQL remains appropriate for the current architecture because audit persistence must participate in the same local transaction as domain mutations.

## Security Properties

The implemented milestone enforces:

- mandatory tenant ownership;
- explicit actor attribution;
- trusted HTTP user attribution for authenticated mutations;
- system attribution for public invitation acceptance;
- system attribution for worker webhook processing;
- consistent system and user actor combinations;
- bounded identifiers;
- safe JSON-native metadata;
- no secret or raw payload storage;
- internal-only idempotency keys;
- no public mutation API;
- no unscoped query primitive;
- OWNER/ADMIN read authorization with STAFF denial;
- correlation and request traceability;
- conflict-safe idempotency;
- foreign-key preservation of actor and tenant attribution;
- application-surface append-only behavior;
- runtime database-role hardening as deployment guidance.

Repository tenant scope does not replace API authentication, membership validation, or RBAC.

A database superuser remains outside the application-level append-only guarantee.

## Current Implementation Boundary

Implemented:

- durable audit persistence;
- actor and source contracts;
- metadata normalization;
- domain audit emission;
- worker-originated audit emission;
- idempotent recording;
- conflict detection;
- transaction-safe recording;
- tenant-scoped lookup;
- opaque cursor transport;
- tenant-scoped OWNER/ADMIN read API;
- public response schemas that omit `idempotency_key`;
- Problem Details mappings;
- action and resource filters;
- concurrency tests;
- rollback tests;
- tenant-isolation tests;
- authorization and pagination API tests;
- domain and worker integration tests;
- append-only application surface.

Intentionally deferred beyond the current release:

- audit mutation endpoints;
- global or platform-wide audit API;
- audit export or CSV download;
- retention jobs and archival workflows;
- tenant-configurable retention;
- metadata search;
- actor filter;
- date-range filter;
- platform-administrator audit view;
- security-attempt event persistence;
- table partitioning;
- metadata GIN index;
- frontend audit screens;
- Professionals audit integration.

These remain deliberate engineering boundaries rather than incomplete foundation work.
