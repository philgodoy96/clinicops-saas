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

Audit logs are persisted in PostgreSQL and participate in the same transaction as the corresponding domain mutation.

```text
domain mutation
    -> append audit entry
    -> flush
    -> caller commit
```

The transaction either commits both the business state and the audit entry or commits neither.

## Implementation Status

The durable audit-log foundation currently includes:

- tenant-scoped `AuditLogEntry` persistence;
- PostgreSQL migration `0009`;
- explicit user and system actor contracts;
- explicit HTTP, worker, CLI, and system sources;
- database-enforced actor consistency;
- bounded and deterministic JSON metadata normalization;
- explicit metadata versioning;
- idempotent recording;
- semantic replay conflict detection;
- insert-only repository behavior;
- caller-owned transaction boundaries;
- rollback coupling with domain mutations;
- tenant-scoped lookup;
- cursor-based tenant timeline queries;
- action and resource filters;
- concurrency and rollback integration tests;
- application-surface append-only behavior.

Domain integrations and the tenant-facing read API are intentionally implemented in a separate slice.

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

Denied operations and validation failures remain operational or security log concerns unless a dedicated security-event model is introduced later.

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
- tenant-scoped retrieval primitives;
- stable cursor ordering.

The audit module does not own:

- tenant authorization;
- role-based access control;
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

A future domain integration follows this pattern:

```text
domain service
    -> validate operation
    -> mutate domain state
    -> record audit entry
    -> flush

HTTP route or worker transaction
    -> commit once
```

This design prevents a domain mutation from being committed without its required audit entry.

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

The current foundation does not support global or platform-wide audit entries.

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

### Actions

Actions are stable bounded strings such as:

```text
tenant.created
tenant.ownership_transferred
membership.role_changed
invitation.accepted
billing.subscription.cancelled
billing.webhook.processed
```

Actions are application identifiers rather than PostgreSQL enum values.

This allows new audited domain operations to be introduced without requiring an enum migration for every action.

### Resource attribution

Each entry contains:

```text
resource_type
resource_id
```

`resource_id` is stored as a bounded string.

The preferred value is a durable local resource identifier.

Resource identifiers are historical references. They do not grant authorization and must never be used as a substitute for tenant-scoped access checks.

### Timestamp

`recorded_at` is generated by PostgreSQL.

Database time is authoritative for persisted ordering.

## Metadata

Audit metadata contains explicit, safe context required to explain the historical transition.

Examples:

```json
{
  "previous_role": "STAFF",
  "new_role": "ADMIN"
}
```

```json
{
  "previous_plan": "starter",
  "new_plan": "growth"
}
```

Metadata does not contain full domain snapshots.

The PostgreSQL column is named:

```text
metadata
```

The SQLAlchemy model maps it through the Python attribute:

```text
event_metadata
```

This avoids collision with SQLAlchemy declarative metadata.

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

The initial metadata policy enforces:

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

Audit metadata must not contain:

- passwords;
- password hashes;
- access tokens;
- refresh tokens;
- session tokens;
- provider credentials;
- webhook secrets;
- webhook signatures;
- authorization headers;
- raw request bodies;
- raw webhook payloads;
- payment credentials;
- exception tracebacks;
- arbitrary ORM objects.

The audit service enforces structural safety and JSON compatibility.

Calling modules remain responsible for selecting explicit metadata that is safe to persist.

## Recording Service

Audit entries are recorded through:

```text
RecordAuditLogService
```

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

## Idempotency

`idempotency_key` is optional.

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

Idempotency supports at-least-once workflows. It does not provide an exactly-once execution guarantee.

## Append-Only Application Surface

Audit entries represent immutable historical facts.

The audit module intentionally exposes no application operation for:

- updating an entry;
- replacing metadata;
- changing actor attribution;
- changing the timestamp;
- deleting an entry.

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

## Tenant-Scoped Queries

The foundation provides safe repository primitives for a future HTTP read API.

Supported operations include:

```text
get_by_id_for_tenant
list_page_for_tenant
```

Every operation requires `tenant_id`.

There is no unscoped repository query.

### Stable ordering

Timeline queries use:

```text
recorded_at DESC
id DESC
```

The UUID tie-breaker provides stable ordering when multiple entries share the same database timestamp.

### Cursor pagination

The cursor contains:

```text
recorded_at
audit_log_id
```

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

Page limits are bounded from 1 through 100.

Offset pagination is intentionally not used for potentially large tenant histories.

### Filters

The repository supports optional filtering by:

- action;
- resource type;
- resource ID.

A resource ID filter requires a resource type.

Arbitrary JSON metadata filtering is not currently supported.

A metadata GIN index is intentionally deferred until measured query requirements justify it.

### Returned records

Read operations return immutable application records rather than mutable SQLAlchemy entities.

Metadata is copied before returning so consumers cannot mutate the ORM JSON structure through a returned record.

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

PostgreSQL remains appropriate for the current architecture because audit persistence must participate in the same transaction as domain mutations.

## Security Properties

The foundation enforces:

- mandatory tenant ownership;
- explicit actor attribution;
- consistent system and user actor combinations;
- bounded identifiers;
- safe JSON-native metadata;
- no implicit object serialization;
- no raw payload storage;
- no public mutation API;
- no unscoped query primitive;
- correlation and request traceability;
- conflict-safe idempotency;
- foreign-key preservation of actor and tenant attribution.

Authorization for reading audit history belongs to the future HTTP integration.

Repository tenant scope does not replace API authorization, membership validation, or RBAC.

## Current Implementation Boundary

Implemented:

- durable audit persistence;
- actor and source contracts;
- metadata normalization;
- idempotent recording;
- conflict detection;
- transaction-safe recording;
- tenant-scoped lookup;
- cursor pagination;
- action and resource filters;
- concurrency tests;
- rollback tests;
- tenant-isolation tests;
- append-only application surface.

Intentionally deferred to the audit integration slice:

- tenant lifecycle audit emission;
- membership and ownership audit emission;
- invitation audit emission;
- billing audit emission;
- worker-originated audit emission;
- tenant-scoped HTTP read API;
- audit RBAC;
- public filter and cursor schemas;
- transport error mappings.

Intentionally deferred beyond the integration slice:

- export;
- retention automation;
- platform-wide audit history;
- security-attempt event persistence;
- table partitioning;
- arbitrary metadata queries;
- frontend audit controls.