# ClinicOps SaaS — System Design

## 1. Architecture Style

ClinicOps is designed as a modular monolith.

The system is deployed from one codebase and uses one PostgreSQL database, while preserving explicit boundaries between business modules.

The initial runtime topology is:

```text
API Client
    |
    v
FastAPI API Process
    |
    v
PostgreSQL
    ^
    |
Background Worker Process
```

The API process and worker process are separate runtime processes built from the same application codebase.

The API handles synchronous HTTP workflows.

The worker handles durable asynchronous jobs.

PostgreSQL is the source of truth for business state, webhook event state, audit records, and background job state.

---

## 2. Why a Modular Monolith

ClinicOps contains several workflows that require strong transactional consistency:

* creating a tenant and its owner membership;
* accepting an invitation and creating a membership;
* transferring ownership;
* ingesting a payment webhook and enqueueing durable processing;
* applying ordered billing transitions;
* recording audit events;
* scheduling durable background jobs.

Keeping these workflows inside one application and one database allows related changes to commit or roll back together.

Microservices are intentionally deferred because they would add:

* distributed transactions;
* internal network failures;
* asynchronous consistency between core modules;
* duplicated authorization concerns;
* service discovery and deployment complexity;
* greater local development and operational cost.

Module boundaries should still be explicit enough that a future extraction can occur when independent scaling or deployment provides measurable value.

---

## 3. High-Level Modules

### Identity

Owns:

* users;
* password credentials;
* refresh tokens or sessions.

Responsibilities:

* registration;
* login;
* access token creation;
* token refresh;
* logout;
* current-user identity;
* user account status.

Identity answers:

```text
Who is making the request?
```

Identity does not decide what the user may do inside a tenant.

---

### Tenants

Owns:

* tenants;
* memberships.

Responsibilities:

* tenant creation;
* tenant lifecycle;
* membership lifecycle;
* role assignment;
* ownership transfer;
* tenant status.

Tenants answers:

```text
Which tenant does the user belong to?
What role does the user hold in that tenant?
```

---

### Authorization

Responsibilities:

* resolving authenticated identity;
* resolving target tenant;
* loading the active membership;
* evaluating tenant permissions;
* enforcing tenant status restrictions.

Authorization rules must be centralized through policies or dependencies rather than scattered across route handlers.

---

### Invitations

Owns:

* invitations.

Responsibilities:

* secure token generation;
* token hashing;
* invitation expiration;
* invitation acceptance;
* email matching;
* membership creation coordination;
* fake email scheduling.

---

### Patients

Owns:

* tenant-scoped patient records.

Responsibilities:

* tenant-scoped patient creation;
* listing and retrieval with keyset pagination;
* partial update with optimistic concurrency;
* archive and restore;
* patient audit emission.

Patients are operational records and not authenticated identities.

Detailed design lives in [Patients Domain](patients.md).

---

### Professionals

Owns:

* tenant-scoped clinical provider profiles.

Responsibilities:

* tenant-scoped professional creation;
* listing and retrieval with keyset pagination;
* partial update with optimistic concurrency;
* archive and restore;
* optional Membership association;
* professional audit emission;
* unlink on Membership removal while preserving the Professional.

`Professional` and `Membership` remain separate entities:

```text
Professional
    -> tenant-owned clinical provider profile

Membership
    -> platform access and tenant authorization
```

Architecture decisions:

* Professional and Membership remain separate entities;
* optional one-to-one association;
* same-tenant composite foreign key;
* one Membership may link to at most one Professional;
* active and archived Professional lifecycle;
* no hard delete endpoint;
* expected-version optimistic concurrency;
* keyset pagination ordered by `created_at DESC, id DESC`;
* tenant-local external reference uniqueness;
* Membership removal unlinks and preserves the Professional in one transaction;
* audit writes share mutation transactions;
* routes own commit, services and repositories do not.

Detailed design lives in [Professionals Domain](professionals-domain.md).

---

### Billing

Owns:

* plans;
* subscriptions;
* invoices.

Responsibilities:

* plan definitions;
* tenant subscriptions;
* invoice lifecycle;
* subscription lifecycle;
* plan entitlements;
* billing recovery;
* grace-period behavior.

Billing state changes occur through explicit services and valid state transitions.

---

### Webhooks

Owns:

* billing webhook events.

Responsibilities:

* reading the raw request body;
* HMAC signature validation;
* provider event parsing;
* immutable event persistence;
* event deduplication;
* enqueueing durable billing webhook processing;
* event processing status.

The Webhooks module is an integration boundary that translates external provider payloads into internal billing commands.

---

### Background Jobs

Owns:

* durable background jobs.

Responsibilities:

* enqueueing;
* polling;
* locking;
* handler registration;
* retry scheduling;
* backoff and jitter;
* success, retry, and dead-letter states;
* stale-lock recovery.

The queue infrastructure does not own business behavior. Domain modules provide business-specific handlers.

Detailed design lives in [Background Job Queue](background-jobs.md).

---

### Audit

Owns:

* audit records.

Responsibilities:

* recording business-relevant actions;
* actor identification;
* tenant context;
* resource context;
* request and correlation identifiers;
* append-only chronological querying;
* OWNER/ADMIN tenant audit read access.

Detailed design lives in [Durable Audit Logs](audit-logs.md).

---

## 4. Data Ownership Rules

Each module owns the business rules and persistence behavior of its entities.

A shared database does not imply shared ownership.

The intended ownership map is:

```text
Identity
  User
  RefreshToken or Session

Tenants
  Tenant
  Membership

Invitations
  Invitation

Patients
  Patient

Professionals
  Professional

Billing
  Plan
  Subscription
  Invoice

Webhooks
  BillingWebhookEvent

Background Jobs
  BackgroundJob

Audit
  AuditLogEntry
```

Other modules must not arbitrarily update models owned by another module.

Cross-module changes must occur through explicit services or narrow interfaces.

---

## 5. Authentication and Tenant Context

Login is global:

```text
POST /api/v1/auth/login
```

The login operation identifies a global user.

It does not require a tenant slug or tenant identifier.

After authentication, a user can list tenants backed by active memberships and operate on tenant-scoped resources:

```text
GET /api/v1/auth/me
GET /api/v1/tenants
GET /api/v1/tenants/{tenant_id}/patients
GET /api/v1/tenants/{tenant_id}/professionals
```

The access token identifies the user.

The route identifies the tenant.

The active membership determines the tenant role and permissions.

```text
access token
    |
    v
authenticated user
    |
target tenant from route
    |
active membership lookup
    |
permission evaluation
    |
tenant-scoped operation
```

The access token must not be treated as the authoritative source for current tenant role membership.

Authorization reads the current membership state from the database so role changes and membership deactivation take effect without waiting for access-token expiration.

---

## 6. Tenant-Scoped Query Rule

Every tenant-owned resource query must include tenant context.

Correct query pattern:

```text
patient.id = requested_patient_id
AND patient.tenant_id = requested_tenant_id
```

Unsafe behavior:

```text
Load patient by patient_id only
Then compare tenant_id after loading
```

Tenant context belongs in repository methods and database query predicates.

This rule applies to:

* memberships;
* invitations;
* patients;
* professionals;
* subscriptions;
* invoices;
* tenant audit records;
* tenant-owned background jobs or job payload references.

Authorization and repository tenant scoping are complementary controls.

Authorization decides whether the user may act.

Repository scoping ensures the query cannot accidentally cross the tenant boundary.

---

## 7. Tenant Lifecycle

Persisted tenant states:

```text
active
disabled
```

### Active

An active tenant permits authorized tenant workflows.

### Disabled

A disabled tenant cannot authorize normal tenant operations.

Users may still authenticate globally, but tenant-scoped authorization rejects operations against a disabled tenant.

Physical tenant deletion remains intentionally deferred.

Tenant deletion involves retention, audit, billing, privacy, and recovery requirements that should not be represented by a generic delete endpoint.

---

## 8. Membership and Ownership Lifecycle

Persisted membership states:

```text
active
disabled
```

An invitation is not a pending membership.

Before acceptance:

```text
Invitation exists
Membership does not exist
```

After successful acceptance:

```text
Invitation is accepted
Active membership exists
```

Critical rules:

* one membership per user and tenant;
* exactly one active owner membership per tenant as a domain invariant
  (database-enforced at-most-one; transactionally preserved at-least-one);
* invitations cannot assign the owner role;
* disabled Memberships cannot authorize operations;
* the owner cannot be disabled or removed through generic membership paths;
* ownership transfer is an explicit transaction.

---

## 9. Tenant Role Model

Persisted v1 roles:

```text
owner
admin
staff
```

Permission matrix:

| Capability | Owner | Admin | Staff |
| --- | ---: | ---: | ---: |
| View tenant details | Yes | Yes | Yes |
| Update tenant details | Yes | Yes | No |
| Manage billing | Yes | No | No |
| Transfer ownership | Yes | No | No |
| Invite admins | Yes | Yes | No |
| Invite staff | Yes | Yes | No |
| Manage non-owner memberships | Yes | Yes, with restrictions | No |
| Read, create, and update patients | Yes | Yes | Yes |
| Archive and restore patients | Yes | Yes | No |
| Read and list professionals | Yes | Yes | Yes |
| Create, update, archive, and restore professionals | Yes | Yes | No |
| Link and unlink professional Membership | Yes | Yes | No |
| View tenant audit logs | Yes | Yes | No |

OWNER, ADMIN, and STAFF may read, create, and update patients. Only OWNER and ADMIN may archive or restore patients.

OWNER and ADMIN may read, create, update, archive, and restore professionals, and may link or unlink Membership. STAFF may read and list professionals only.

OWNER and ADMIN may read tenant audit logs. STAFF is denied audit read.

Authorization permissions are centrally defined and tested.

They must not be recreated as ad hoc role comparisons throughout route handlers.

---

## 10. Request Processing Flow

A typical tenant-scoped request follows:

```text
HTTP request
    |
Request ID middleware
    |
Authentication
    |
Tenant resolution
    |
Membership resolution
    |
Tenant-status validation
    |
Permission evaluation
    |
Application service
    |
Domain validation
    |
Database transaction
    |
Audit record and durable job creation
    |
HTTP response
```

Route handlers should remain thin.

They may:

* parse HTTP input;
* resolve dependencies;
* invoke application services;
* return response schemas;
* map known application failures through shared error handlers.

They should not:

* implement state machines;
* coordinate multi-repository workflows;
* perform unrestricted model updates;
* contain raw authorization logic;
* control transaction boundaries for complex use cases.

---

## 11. Transaction Boundaries

Transactions protect workflows that must succeed or fail as one unit.

### Tenant Creation

```text
Create tenant
Create owner membership
Create audit record
Commit
```

A tenant must not exist without its owner membership.

### Invitation Acceptance

```text
Lock invitation
Validate token hash
Validate expiration and status
Resolve or create user
Validate invitation email
Create membership
Mark invitation accepted
Create audit record
Commit
```

Concurrent acceptance requests must not create duplicated memberships or accept the same invitation more than once.

### Ownership Transfer

```text
Lock tenant
Lock current owner membership
Lock target membership
Validate both memberships
Promote target to owner
Demote current owner to admin
Create audit record
Commit
```

The system must never expose an intermediate state with zero or two owners.

### Billing Webhook Ingestion

```text
receive raw request
    -> validate HMAC before persistence
    -> parse provider envelope
    -> persist immutable webhook event
    -> handle duplicate identity safely
    -> enqueue one billing.webhook.process job in the same transaction
    -> commit
    -> return 202 Accepted
```

Invalid signatures must not create webhook events, billing transitions, audit business events, or background jobs.

Duplicate provider deliveries return `202 Accepted` after confirming that the event was previously persisted and the processing job was reserved safely.

### Billing Webhook Worker Processing

```text
claim eligible job
    -> build worker audit attribution
    -> process persisted webhook event
    -> apply ordered billing transition or ignore stale event
    -> record billing webhook audit fact
    -> commit processing transaction
    -> complete the job in the queue lifecycle
```

Job completion is a separate queue transaction from billing webhook processing.

### Domain Change and Background Job

```text
Create or update domain record
Insert required background job
Create audit record
Commit together
```

This avoids committing a business change while losing the asynchronous work it requires.

---

## 12. Application Services

Application services coordinate business workflows.

Examples include:

```text
CreateTenantService
TransferTenantOwnershipService
AcceptInvitationService
CreateBillingSubscriptionService
ScheduleBillingPlanChangeService
IngestBillingWebhookService
ProcessBillingWebhookEventService
EnqueueBackgroundJobService
ClaimBackgroundJobsService
CompleteBackgroundJobService
CreatePatientService
ArchivePatientService
CreateProfessionalService
LinkProfessionalMembershipService
```

Application services may:

* coordinate repositories;
* enforce workflow invariants;
* define transaction boundaries;
* call provider interfaces;
* create audit records;
* enqueue durable jobs.

Application services must not depend on FastAPI request or response objects.

---

## 13. Provider Boundaries

External providers are accessed through explicit interfaces.

### Email Provider

Conceptual interface:

```text
EmailProvider
  send_invitation(...)
  send_payment_receipt(...)
```

V1 implementation:

```text
FakeEmailProvider
```

### Payment Provider

Conceptual interface:

```text
PaymentProvider
  create_payment(...)
  verify_webhook_signature(...)
  parse_webhook_event(...)
```

V1 implementation:

```text
FakePaymentProvider
```

Provider-specific payloads must be translated before entering core billing workflows.

The Billing module should not depend directly on fake-provider payload structures.

---

## 14. Webhook Processing Model

Webhook handling is split into HTTP ingestion and worker processing.

### Webhook ingestion transaction

```text
receive raw request
    -> validate HMAC before persistence
    -> parse provider envelope
    -> persist immutable webhook event
    -> handle duplicate identity safely
    -> enqueue one billing.webhook.process job in the same transaction
    -> commit
    -> return 202 Accepted
```

### Worker processing transaction

```text
claim eligible job
    -> build worker audit attribution
    -> process persisted webhook event
    -> apply ordered billing transition or ignore stale event
    -> record billing webhook audit fact
    -> commit processing transaction
    -> complete the job in the queue lifecycle
```

Job completion is a separate queue transaction from billing webhook processing.

Invalid signatures must not create webhook events, billing transitions, audit business events, or background jobs.

---

## 15. Background Job Model

The PostgreSQL-backed queue supports:

```text
queued
processing
retry_scheduled
succeeded
dead_lettered
```

A job includes:

```text
job_type
payload
payload_version
processing_attempt_count
max_attempts
priority
available_at
worker_id
claim_token
claimed_at
lease_expires_at
last_error_code
last_error_message
last_failed_at
completed_at
dead_lettered_at
idempotency_key
origin_request_id
correlation_id
```

Workers acquire jobs using a query based on:

```sql
SELECT ... FOR UPDATE SKIP LOCKED
```

Worker flow:

```text
Select eligible jobs
    |
Acquire row locks
    |
Mark selected jobs processing
    |
Commit claim
    |
Execute handler
    |
Succeeded?
 /        \
Yes        No
 |          |
Mark        Retry allowed?
succeeded    /       \
             Yes      No
              |        |
     Schedule retry    Mark dead_lettered
```

The queue provides at-least-once execution.

Locking prevents normal concurrent execution of the same job.

Idempotent handlers prevent duplicate side effects when execution is repeated after failures or stale-lock recovery.

Detailed design lives in [Background Job Queue](background-jobs.md).

---

## 16. Retry Strategy

Retry scheduling uses capped exponential backoff with jitter and injectable deterministic inputs in tests.

A conceptual delay shape is:

```text
capped(base_delay * 2^(attempt - 1)) with jitter in [half, full]
```

Retry behavior must:

* limit maximum delay;
* limit maximum attempts;
* classify retryable and non-retryable failures;
* preserve the latest error;
* remain deterministic under automated tests through injectable time and randomness.

---

## 17. Audit Records

Audit logs are business records.

Persisted fields include:

```text
id
tenant_id
actor_type
actor_user_id when applicable
actor_role when applicable
source
action
resource_type
resource_id
metadata_version
metadata
idempotency_key when applicable
request_id when applicable
correlation_id
recorded_at
```

Actor types:

```text
user
system
```

Sources:

```text
http
worker
cli
system
```

Actions and resource types use stable identifiers.

Audit recording is transactionally coupled with the corresponding domain mutation.

OWNER and ADMIN may read tenant audit logs through the tenant-scoped read API. STAFF is denied.

`idempotency_key` is an internal replay-protection field and is not part of the public audit read response.

Audit records are append-only.

Audit metadata must not include:

* raw passwords;
* password hashes;
* access tokens;
* refresh tokens;
* invitation tokens;
* provider secrets;
* unnecessary patient-sensitive data.

Detailed design lives in [Durable Audit Logs](audit-logs.md).

---

## 18. Application Logs

Application logs describe operational behavior.

Examples:

```text
request_started
request_finished
authentication_failed
webhook_signature_invalid
webhook_duplicate_received
job_retry_scheduled
job_dead_lettered
database_error
```

Structured logs should support fields such as:

```text
event
request_id
correlation_id
user_id
tenant_id
job_id
payment_event_id
duration_ms
status_code
```

Application logs are not substitutes for audit records.

---

## 19. Request ID and Correlation ID

A request ID identifies one incoming HTTP request or one worker execution.

A correlation ID connects a broader business workflow.

Example:

```text
Webhook HTTP request
- request_id: req-001
- correlation_id: payment-event-123

Invoice transition
- correlation_id: payment-event-123

Audit record
- correlation_id: payment-event-123

Receipt background job
- correlation_id: payment-event-123

Worker execution
- request_id: job-run-456
- correlation_id: payment-event-123
```

Request and correlation identifiers should be propagated to:

* structured logs;
* audit records;
* billing webhook event records;
* background jobs;
* relevant response headers.

---

## 20. Pagination

Offset pagination is appropriate for relatively small operational lists:

* invitations;
* invoices;
* memberships.

Patients and professionals use opaque keyset pagination ordered by:

```text
created_at DESC, id DESC
```

Cursor pagination is appropriate for append-heavy chronological records:

* audit logs;
* billing webhook events;
* background jobs.

Cursor ordering should use a stable composite key such as:

```text
recorded_at + id
```

or another resource-specific stable timestamp plus `id`.

This prevents ambiguous ordering when multiple records have the same timestamp.

---

## 21. Security Boundaries

### Credential Boundary

* Argon2id password hashing;
* hashed refresh tokens;
* hashed invitation tokens;
* no raw credential logging;
* timing-safe secret comparisons.

### Tenant Boundary

* tenant context in every tenant-owned query;
* active membership required;
* current role loaded from the database;
* no role stored on the global user;
* disabled tenants cannot authorize normal tenant operations.

### Provider Boundary

* exact raw-body HMAC validation;
* replay-window consideration;
* unique provider event identifiers;
* provider payload translation.

### Operational Boundary

* generic public error responses for unexpected failures;
* detailed structured internal logs;
* no secret or token exposure;
* correlation identifiers for investigation.

---

## 22. Scaling Direction

The initial deployment may use:

```text
One API process
One worker process
One PostgreSQL instance
```

The architecture can later support:

```text
Multiple stateless API instances
Multiple workers
Managed PostgreSQL
```

Multiple workers coordinate through PostgreSQL locking.

Potential future extraction candidates include:

* notification delivery;
* payment provider integrations;
* background job execution;
* audit export.

Extraction should follow operational evidence rather than architectural speculation.

---

## 23. Major Failure Responses

| Failure mode | Design response |
| --- | --- |
| Cross-tenant resource access | Membership validation and tenant-scoped query |
| Owner removal | Explicit invariant and restricted ownership workflow |
| Concurrent ownership transfer | Transactional row locks |
| Duplicate invitation acceptance | Row lock, invitation state validation, unique membership |
| Invalid webhook signature | Reject before persistence |
| Duplicate webhook event | Unique provider event identifier and safe replay acknowledgement |
| Delayed billing event | Explicit state-transition validation or ignore stale event |
| Two workers claim the same job | `FOR UPDATE SKIP LOCKED` |
| Worker crashes after claim | Stale-lock recovery policy |
| Job executes twice | Idempotent handler and idempotency key |
| Tenant disabled during operation | Central tenant-status policy |
| Role changes while token is valid | Load current membership from PostgreSQL |
| Critical audit write fails | Audit write participates in the transaction |
| Plan limit race | Transactional enforcement and locking where required |

---

## 24. Approved System Design Decisions

1. ClinicOps uses a modular monolith.
2. PostgreSQL is the single source of truth.
3. API and worker run as separate processes from one codebase.
4. Users are global identities.
5. Memberships define tenant roles and access.
6. Login is global.
7. Tenant context is explicit in tenant-owned routes.
8. Authorization reads current membership state from the database.
9. Every tenant has exactly one active owner.
10. Patients are independent tenant-owned records.
11. Professionals are tenant-owned clinical provider profiles, separate from Memberships, with an optional same-tenant one-to-one association.
12. Tenant permissions are centrally defined by role.
13. Complex workflows use application services.
14. Critical workflows use explicit database transactions.
15. Webhook ingestion authenticates, persists, and enqueues processing before acknowledgement.
16. Domain changes and required jobs may commit atomically.
17. Background jobs use PostgreSQL locking.
18. Background execution is at least once.
19. Audit logs and application logs are distinct.
20. Request IDs identify individual executions.
21. Correlation IDs connect broader workflows.
22. Offset and cursor pagination are selected by resource behavior.
23. Microservices, Redis, and external brokers are intentionally deferred.
