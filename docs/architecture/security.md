# Security

## Billing Webhook Authentication

Billing webhook requests are machine-to-machine provider callbacks.

They do not use the user authentication model.

The implemented endpoint is:

```text
POST /api/v1/billing/webhooks/{provider}
```

Provider requests are authenticated with a shared-secret HMAC signature.

Header:

```text
X-Billing-Signature: t=<unix_timestamp>,v1=<digest>
```

Signed bytes:

```text
<timestamp>.<exact raw request body>
```

Algorithm:

```text
HMAC-SHA256
```

## Authentication Order

Webhook authentication occurs before JSON parsing.

The security boundary is:

```text
receive request
    -> read exact raw bytes
    -> enforce payload-size limit
    -> validate signature header
    -> validate timestamp freshness
    -> calculate expected HMAC
    -> constant-time digest comparison
    -> parse canonical JSON
```

This prevents unauthenticated payloads from entering the application event
contract boundary.

The payload-size check occurs before HMAC work to reduce resource exposure from
oversized requests.

## Replay Window

The signature timestamp is compared against the application clock.

The default tolerance is:

```text
300 seconds
```

Requests outside the configured window are rejected.

This limits replay exposure for intercepted signed requests.

The provider event uniqueness boundary provides an additional durable replay
control:

```text
provider + provider_event_id
```

A request inside the signature tolerance still cannot create another event row
when its provider event ID has already been received.

## Constant-Time Signature Comparison

Expected and received HMAC digests are compared with a constant-time
comparison function.

Normal string equality must not be used for signature verification.

The verification response does not reveal whether failure came from:

- a missing signature;
- a malformed timestamp;
- an invalid digest;
- an expired timestamp;
- a mismatched payload.

All authentication failures use one public application error code:

```text
billing_webhook_authentication_failed
```

Internal reasons remain available only to controlled application logging.

## Secret Configuration

The webhook shared secret is configured with:

```text
CLINICOPS_BILLING_WEBHOOK_SECRET
```

Requirements:

- represented in application settings as `SecretStr`;
- minimum of 32 UTF-8 bytes;
- local default allowed only for local and test environments;
- local default rejected in staging and production;
- never committed as a real deployment secret;
- never logged;
- never returned through API responses;
- never embedded in persisted webhook rows.

Operational deployments should inject the secret through the deployment
secret-management mechanism.

Secret rotation and multiple active signing secrets are intentionally deferred.

## Raw Payload Integrity

The HMAC authenticates the exact raw bytes received over HTTP.

After authentication, ClinicOps calculates:

```text
SHA-256(exact raw request bytes)
```

and persists the hexadecimal digest as:

```text
payload_sha256
```

This hash supports immutable duplicate detection.

ClinicOps does not calculate duplicate equality from reserialized JSON.

Consequences:

- whitespace changes alter the hash;
- field-order changes alter the hash;
- any byte-level payload change alters the hash;
- the same provider event ID cannot silently represent different bytes.

The raw payload itself is persisted as validated JSON, not logged.

## Durable Acknowledgement

ClinicOps returns `202 Accepted` only after the webhook event row and its
processing job commit together.

Security and reliability boundary:

```text
valid signature
    -> valid canonical event
    -> durable event persistence
    -> durable processing-job enqueueing
    -> durable database commit
    -> acknowledgement
```

A provider must not receive a successful acknowledgement for an event that was
not stored durably with its semantic processing job.

An identical duplicate receives the same public acknowledgement without
creating another event row or another semantic job.

## Webhook Authorization Boundary

The webhook route does not depend on:

- user passwords;
- access JWTs;
- persisted login sessions;
- tenant membership;
- RBAC permissions.

This is intentional because the caller is an external payment provider, not a
ClinicOps user.

The route cannot be used to select a local tenant or local subscription ID.

The provider supplies only provider-owned identities inside an authenticated
canonical event.

Local ownership resolution is deferred to webhook processing.

## Webhook Data Exposure

Webhook responses and public errors do not expose:

- shared secrets;
- complete signature headers;
- expected or received HMAC digests;
- raw request bodies;
- payload hashes;
- persisted webhook IDs;
- background job IDs;
- processing states;
- internal database errors;
- provider payloads;
- tenant identities;
- local subscription identities.

Logs may include controlled metadata such as:

- request ID;
- correlation ID;
- provider;
- provider event ID;
- event type;
- payload size;
- ingestion outcome.

Logs must not include the secret, complete signature, or full payload.

## Billing Webhook Processing Trust Boundary

Webhook authentication proves that a request was signed with the configured
provider secret.

It does not make every provider-supplied state transition automatically valid.

Processing revalidates persisted provider-owned data before applying local
billing state.

Validation includes:

- canonical event payload integrity;
- consistency between persisted event columns and payload;
- provider identity;
- provider subscription identity;
- event type;
- provider state version;
- price code;
- local billing period;
- lifecycle compatibility;
- cancellation timestamps;
- scheduled cancellation boundaries.

Successful ingestion is permission to persist an event.

It is not permission to bypass local billing invariants.

## Internal Processing Boundary

Webhook processing is an internal application operation.

It is not exposed through:

- a public API endpoint;
- tenant-scoped user routes;
- an administrative replay endpoint.

The processing command accepts only:

```text
webhook_event_id
```

It does not accept:

- webhook shared secrets;
- HTTP signatures;
- raw HTTP request objects;
- tenant IDs;
- local subscription IDs;
- local billing customer IDs;
- arbitrary target records.

The persisted event supplies provider-owned identity.

The application resolves local ownership from trusted database relationships.

## Provider and Local Data Ownership

The provider owns facts such as:

- provider event ID;
- provider subscription ID;
- provider state version;
- provider event timestamp;
- provider lifecycle status;
- renewal period;
- final cancellation timestamp;
- provider price code.

ClinicOps owns:

- local subscription identity;
- tenant ownership;
- local billing customer relationships;
- supported price catalog;
- commercial price attributes;
- local lifecycle invariants;
- processing status;
- processing attempts;
- failure classification;
- permissions and entitlements.

The provider price code is treated as an identifier.

ClinicOps resolves:

- plan;
- billing interval;
- currency;
- unit amount;

from the local catalog.

## Persisted Event Integrity Validation

Processing revalidates that the canonical payload matches the immutable event
metadata stored during ingestion.

The following values must remain consistent:

```text
payload.id
    == provider_event_id

payload.type
    == event_type

payload.created_at
    == provider_created_at

payload.data.provider_subscription_id
    == provider_subscription_id

payload.data.provider_state_version
    == provider_state_version
```

A mismatch is a terminal processing failure.

This protects processing from accidental or unauthorized database mutation of
either the indexed metadata or canonical payload.

## Processing Authorization Through Identity Resolution

The processing service does not trust a tenant identity from the provider.

It resolves and locks a subscription using:

```text
provider
+
provider_subscription_id
```

After resolution, the handler verifies that the locked subscription still
matches the event identity.

The provider cannot redirect an event to another tenant by including a local
identifier in the payload.

Tenant ownership remains derived from the local subscription row.

## Monotonic State Protection

Provider state versions prevent out-of-order events from regressing local
billing state.

After locking the subscription:

```text
event version <= local version
    -> ignore event

event version > local version
    -> validate and apply transition
```

The ordering check occurs after row locking.

This is required because another worker may update the subscription between
event ingestion and processing.

Provider timestamps are retained for traceability but are not the primary
ordering authority.

## Transaction and Concurrency Safety

Processing separates claim ownership from lifecycle application.

```text
Transaction A
    -> lock event
    -> persist processing claim
    -> commit

Transaction B
    -> lock event
    -> lock subscription
    -> validate ordering and lifecycle
    -> update subscription
    -> complete event
    -> commit
```

Security and integrity properties:

- one event cannot be claimed twice as a new attempt;
- completed events are replayed without mutation;
- subscription and event completion are atomic;
- different events for one subscription serialize on the subscription row;
- stale events cannot overwrite newer provider state;
- a failed Transaction B cannot partially mutate billing state.

A Transaction B failure leaves the durable `processing` claim for later
recovery.

Durable background-job queue infrastructure provides stale-claim recovery for
queue rows. Billing webhook processing executes through the worker runtime after
authenticated ingestion enqueues `billing.webhook.process`.

## Processing Failure Data

Processing persists stable failure classifications.

Examples:

```text
billing_webhook_subscription_not_found
invalid_persisted_payload
persisted_event_metadata_mismatch
provider_subscription_mismatch
unsupported_price_code
invalid_period_transition
cancellation_pending
cancellation_boundary_mismatch
invalid_cancellation_timestamp
```

Failure messages are controlled internal diagnostics.

They must not contain:

- webhook secrets;
- complete signature headers;
- raw webhook bodies;
- payload hashes;
- user credentials;
- access tokens;
- database connection details;
- stack traces;
- unrelated tenant data.

Failure messages are length-limited before persistence.

The processing boundary currently has no public HTTP error surface.

## Retryable and Terminal Failure Security

Retryable failures represent conditions that may become valid later.

Implemented example:

```text
provider subscription not yet available locally
```

Terminal failures represent invariant violations or incompatible state.

Examples:

- persisted event metadata mismatch;
- provider subscription identity mismatch;
- unsupported price;
- invalid renewal period;
- renewal during pending cancellation;
- cancellation boundary mismatch;
- final cancellation inconsistency.

Terminal failures are not automatically retried.

This prevents repeatedly applying events that violate local billing rules.

Manual recovery and reconciliation require separate controlled operational
workflows.

## Cancellation Safety

Renewal processing cannot implicitly:

- reverse a scheduled cancellation;
- reactivate a canceled subscription;
- replace a local price with an unrelated provider price.

Scheduled cancellation confirmation must match the local period boundary.

Provider-authoritative cancellation is permitted without a prior local request
only when:

- the provider version advances;
- the event is internally consistent;
- the provider identity matches;
- the cancellation timestamp belongs to the event period;
- the billing period does not regress.

`cancellation_requested_at` is preserved as an audit fact.

## Billing Reconciliation Trust Boundary

Billing reconciliation reads current subscription state from a payment
provider and may repair local billing state.

A provider response is authoritative for provider-owned facts, but it is not
allowed to bypass local ownership, catalog, lifecycle, or concurrency
invariants.

The reconciliation boundary validates:

- trusted local subscription identity;
- configured provider identity;
- provider subscription identity;
- provider snapshot contract;
- provider state version;
- provider lifecycle consistency;
- billing-period consistency;
- provider price relationship;
- local catalog support;
- scheduled cancellation boundaries;
- local state after row locking.

## Reconciliation Command Boundary

The reconciliation command accepts only:

```text
subscription_id
```

It does not accept:

- tenant ID from the provider;
- provider name from an API caller;
- provider subscription ID from an API caller;
- arbitrary provider URLs;
- provider credentials;
- raw provider payloads;
- local billing customer IDs;
- target price amounts;
- lifecycle overrides.

The local subscription record resolves the provider identity.

This prevents a caller or provider payload from selecting another tenant's
subscription.

## External Call Transaction Isolation

The reconciliation service intentionally ends its initial database transaction
before calling the provider.

```text
Transaction A
    -> read local provider identity
    -> commit

External provider call
    -> no row lock
    -> no database transaction held

Transaction B
    -> reload and lock subscription
    -> revalidate identity
    -> compare and repair
    -> commit
```

Security and reliability properties:

- external latency does not extend a database row lock;
- provider outages do not hold subscription locks;
- database connections are not reserved while awaiting the provider;
- the service cannot rely on stale pre-call local state;
- identity and version are checked again before mutation.

## Provider Identity Revalidation

Before the external call, the service captures:

```text
subscription_id
provider
provider_subscription_id
```

The provider adapter must match the local provider.

The returned snapshot must match:

```text
snapshot.provider
snapshot.provider_subscription_id
```

After the provider call, the locked subscription must still match the captured
identity.

A mismatch produces:

```text
billing_reconciliation_provider_identity_mismatch
```

This protects against:

- incorrect provider routing;
- provider adapter defects;
- stale subscription identity;
- administrative identity changes during the external call;
- accidental cross-subscription repair.

## Provider Snapshot Validation

The provider snapshot is a typed, immutable contract.

Validation requires:

- non-empty identifiers;
- positive provider state version;
- timezone-aware timestamps;
- valid billing periods;
- active lifecycle without `canceled_at`;
- canceled lifecycle with `canceled_at`;
- canceled lifecycle without pending cancellation;
- cancellation timestamp inside the provider billing period.

Malformed snapshots are rejected as:

```text
billing_reconciliation_invalid_snapshot
```

The service does not attempt to infer missing provider data.

## Reconciliation Data Ownership

The provider owns:

- provider subscription identity;
- provider state version;
- provider price code;
- lifecycle status;
- provider billing period;
- scheduled cancellation state;
- final cancellation timestamp;
- observation timestamp.

ClinicOps owns:

- local subscription identity;
- tenant ownership;
- billing customer ownership;
- supported price catalog;
- commercial price attributes;
- local workflow state;
- cancellation request audit history;
- repair policy;
- permissions and operational controls.

The provider price code is treated only as an identifier.

ClinicOps resolves:

```text
plan
billing_interval
currency
unit_amount
```

from the local catalog.

## Monotonic Reconciliation Protection

The provider snapshot version is compared after locking the local
subscription.

```text
snapshot version < local version
    -> ignore snapshot

snapshot version >= local version
    -> validate and compare state
```

An older snapshot cannot:

- reduce local provider state version;
- reactivate a canceled subscription;
- restore an obsolete billing period;
- overwrite a newer webhook result;
- clear a newer final cancellation.

Provider timestamps are traceability data.

They are not the primary ordering authority.

## Reconciliation Price Safety

Reconciliation accepts a provider price only when it matches:

```text
local price_code
```

or:

```text
local pending_price_code
```

An unrelated provider price is rejected as:

```text
unexpected_provider_price
```

Even a recognized catalog price is not adopted automatically when it is
unrelated to the local workflow.

This prevents reconciliation from silently converting an unexpected provider
change into an authorized ClinicOps plan change.

## Reconciliation Cancellation Safety

Reconciliation may repair provider-authoritative cancellation, but it does not
discard local audit history.

```text
cancellation_requested_at
```

is preserved.

The service never invents a local request timestamp.

When a local scheduled cancellation already exists, a final provider snapshot
must confirm the same period boundary.

A mismatch produces:

```text
scheduled_cancellation_boundary_mismatch
```

An active provider snapshot cannot automatically reactivate a local
subscription that is already canceled.

Reactivation remains a separate explicit workflow.

## Reconciliation Concurrency Safety

Reconciliation and webhook processing share the same local subscription lock.

This establishes one serialization boundary for provider-driven state changes.

Security and integrity properties:

- concurrent reconciliations cannot apply independent conflicting repairs;
- only one worker repairs a specific drift;
- later workers observe repaired state;
- reconciliation cannot overwrite a newer webhook-applied provider version;
- stale snapshots are ignored after the lock is acquired;
- local lifecycle state remains monotonic.

## Reconciliation Failure Data

Reconciliation errors use stable public classifications.

Examples:

```text
billing_reconciliation_subscription_not_found
billing_reconciliation_provider_state_not_found
billing_reconciliation_provider_identity_mismatch
billing_reconciliation_unsupported_price
billing_reconciliation_invalid_snapshot
billing_reconciliation_conflict
```

Internal conflict codes may include:

```text
provider_subscription_identity_missing
invalid_local_provider_state_version
reactivation_not_supported
unsupported_local_subscription_status
incomplete_local_billing_period
provider_period_regression
unexpected_provider_price
scheduled_cancellation_boundary_mismatch
```

Public messages must not expose:

- provider credentials;
- full provider payloads;
- database connection details;
- unrelated tenant identifiers;
- stack traces;
- access tokens;
- webhook secrets;
- internal payment-provider URLs.

Reconciliation is currently an internal application operation and has no
public HTTP error surface.

## Reconciliation Operational Access

No public or administrative reconciliation endpoint is implemented.

The service is prepared for controlled execution by future worker or operator
tooling that builds on the durable background-job queue foundation.

Periodic reconciliation scheduling remains intentionally deferred.

Future operational entry points must provide:

- authenticated operator identity;
- explicit authorization;
- tenant-aware access controls;
- correlation IDs;
- audit logging;
- rate limiting;
- safe retry behavior;
- observability for provider failures;
- protections against repeated manual execution.

## Background Job Queue Security

The durable background-job queue stores execution coordination state in
PostgreSQL. Job rows coordinate asynchronous work; they are not a second source
of business truth. Detailed lifecycle semantics live in
`docs/architecture/background-jobs.md`.

Job payloads must not contain:

- passwords;
- access tokens;
- refresh tokens;
- provider credentials;
- webhook-signing secrets;
- raw credential material;
- arbitrary provider URLs;
- serialized ORM objects;
- duplicated full webhook payloads;
- raw webhook bodies.

Payloads should reference durable local identifiers, such as a billing webhook
event ID or subscription ID, rather than copying domain records or credential
material into the queue.

Failure metadata stored on job rows is normalized, bounded, and retained for
operations. Persisted error messages must remain suitable for operational
diagnosis and must exclude secrets, raw provider bodies, credentials, and
unrelated tenant data.

Traceability requirements:

- `correlation_id` is required for end-to-end operational linking;
- `origin_request_id` is optional because some jobs originate from scheduled or
  recovery workflows rather than HTTP requests;
- structured worker logs may include worker ID, job ID, job type, claim token,
  correlation ID, and request ID, but must not include raw payloads.

Claim ownership is enforced on completion and failure transitions. Mutation
requires:

```text
job ID
+
worker ID
+
claim token
```

A stale worker cannot complete or fail a job after recovery or after another
worker has claimed it with a new claim token.

## Worker Runtime Security

The worker process executes registered handlers only.

Security properties:

- an explicit handler registry prevents arbitrary dynamic handler loading;
- job payload data cannot select import paths, class names, or executable code;
- webhook authentication occurs before event persistence and job enqueueing;
- the worker does not expose a public HTTP interface;
- the worker does not expose a job-administration endpoint;
- API and worker use the same application configuration model and PostgreSQL
  database;
- local Compose defaults are development-only;
- real secrets remain outside version control.

Billing webhook jobs carry only the local webhook-event ID. The durable
`BillingWebhookEvent` remains the source of truth for authenticated provider
content.

The current queue and worker runtime do not expose a public job-administration
API, manual replay endpoint, or administrative state-mutation surface.

## Audit Log Security

Durable audit entries record committed historical facts for exactly one tenant.
They are distinct from operational application logs and do not reconstruct
domain state.

Security properties of the implemented audit milestone:

- every `AuditLogEntry` is tenant-scoped;
- every read starts with an authorized tenant ID;
- no unscoped repository query is exposed;
- user actors require a user ID;
- system actors cannot carry user attribution or tenant-role snapshots;
- authenticated HTTP mutations use trusted user attribution with a historical
  role snapshot that does not grant future permissions;
- public invitation acceptance is system-attributed over HTTP and does not treat
  the invitation token as proof of an authenticated user actor;
- billing webhook worker processing is system-attributed with durable
  correlation propagation and optional originating request ID;
- metadata must be explicit, bounded, and JSON-native;
- secrets, tokens, credentials, authorization headers, webhook signatures, raw
  request bodies, raw webhook payloads, payment data, provider response
  snapshots, exception tracebacks, ORM objects, and internal audit idempotency
  keys must not be stored in metadata or exposed publicly;
- resource IDs are historical references, not authorization grants;
- no public audit-write endpoint exists;
- no application update or delete path exists;
- request and correlation identifiers support end-to-end traceability;
- optional globally unique idempotency keys protect equivalent worker replay
  without providing exactly-once guarantees.

Audit persistence participates in the same local transaction as the
corresponding domain mutation. Audit persistence failure must prevent that
mutation from committing, and rolled-back mutations must not leave durable
audit rows.

Tenant-scoped audit reads require authentication, membership, and
`audit_log:read` permission:

```text
OWNER -> allowed
ADMIN -> allowed
STAFF -> denied
```

Runtime database roles should be restricted from `UPDATE` and `DELETE` on audit
rows. That restriction is deployment hardening and is not encoded as a
migration trigger. A database superuser remains outside the application-level
append-only guarantee.

Retention is currently indefinite. Export, retention automation,
platform-wide audit history, and security-attempt event persistence remain
intentional follow-up work.

Detailed audit architecture lives in `docs/architecture/audit-logs.md`.

## Security Invariants

- Webhook requests are authenticated before event parsing.
- Signature timestamp and raw body are authenticated together.
- Digest comparison is constant-time.
- Oversized requests are rejected before ingestion.
- Deployed environments cannot use the local webhook secret.
- Provider event IDs are unique within a provider.
- Identical duplicates do not create additional rows.
- Conflicting duplicates are rejected.
- Successful acknowledgement follows durable commit.
- Webhook authentication does not reuse Bearer authentication semantics.
- HMAC failures do not return a Bearer challenge.
- Ingestion does not mutate subscription state.
- Unknown local subscriptions may still produce authenticated durable events.
- Authenticated provider events remain subject to local domain validation.
- Processing commands cannot select tenants or subscriptions directly.
- Provider payloads cannot contain trusted local ownership identifiers.
- Persisted event metadata must match the canonical payload.
- Subscription identity is resolved from provider-owned identifiers.
- Resolved subscription identity is revalidated before mutation.
- Provider state checks occur while the subscription row is locked.
- Stale provider events cannot overwrite newer state.
- Commercial price attributes come from the local catalog.
- Renewal cannot bypass a pending cancellation.
- Scheduled cancellation must match the local period boundary.
- Subscription and event completion are committed atomically.
- Terminal failures are not automatically retried.
- Processing failure messages exclude secrets and raw payloads.
- Processing is not exposed as a public API operation.
- Reconciliation accepts only a trusted local subscription UUID.
- Provider identity is resolved from the local database.
- Provider calls occur without a database row lock.
- Local identity is revalidated after the provider call.
- Provider snapshots cannot select a local tenant or subscription.
- Provider snapshots must satisfy the canonical snapshot contract.
- Provider state ordering is checked while the subscription row is locked.
- Older snapshots cannot overwrite newer local provider state.
- Provider prices must match the current or pending local price.
- Commercial price attributes come from the local catalog.
- Reconciliation cannot automatically reactivate canceled subscriptions.
- Cancellation request audit timestamps are preserved.
- Reconciliation does not invent provider or local audit facts.
- Scheduled cancellation boundaries must match before final repair.
- Reconciliation and webhook processing share a row-lock serialization
  boundary.
- Reconciliation failure messages exclude secrets and raw provider payloads.
- Reconciliation is not exposed as a public API operation.
- Background job payloads exclude secrets, credentials, and duplicated webhook
  bodies.
- Background job payloads prefer durable local identifiers.
- Background job failure metadata is bounded and sanitized.
- Background job claim mutation requires job ID, worker ID, and claim token.
- Stale claim tokens cannot mutate recovered or newly claimed jobs.
- Background job administration is not exposed as a public API.
- Worker handlers are resolved only through an explicit registry.
- Job payloads cannot select import paths or executable code.
- The worker process exposes no public HTTP or administration surface.
- Webhook authentication precedes durable event persistence and job enqueueing.
- Audit entries belong to exactly one tenant.
- User actors require a user ID.
- System actors cannot carry user attribution or tenant-role snapshots.
- Authenticated HTTP mutations use trusted user attribution.
- Public invitation acceptance is system-attributed.
- Billing webhook worker processing is system-attributed.
- Actor-role snapshots do not grant permissions.
- Audit metadata is explicit, bounded, and JSON-native.
- Audit metadata excludes secrets, tokens, credentials, authorization headers,
  webhook signatures, raw request bodies, raw webhook payloads, payment data,
  provider response snapshots, exception tracebacks, ORM objects, and internal
  audit idempotency keys.
- Audit resource IDs do not grant authorization.
- Audit queries always start with the authorized tenant ID.
- OWNER and ADMIN may read tenant audit history; STAFF may not.
- No public audit-write API exists.
- No application update or delete path exists for audit rows.
- Audit request and correlation identifiers support traceability.
- Audit idempotency keys protect equivalent replay without exactly-once
  guarantees.

## Intentionally Deferred Security Controls

The following controls are future capabilities appropriate to production
deployment needs. They are not part of the current authenticated ingestion
boundary:

- secret rotation;
- multiple active webhook secrets;
- provider-specific asymmetric signatures;
- mTLS;
- IP allowlisting;
- edge rate limiting;
- WAF rules;
- production provider adapters.

The durable background-job queue and worker runtime now provide stale-claim
recovery, retry scheduling, attempt limits, dead-letter state, explicit handler
registration, and asynchronous billing webhook job execution. The following
operational controls still build on the implemented internal processing and
transaction boundaries but are not yet part of the current release:

- authenticated operational replay tooling;
- operator RBAC for manual processing;
- structured security event alerts;
- multi-provider processing policies;
- entitlement propagation;
- production worker isolation.

The following operational controls build on the implemented internal
reconciliation, identity, transaction, concurrency, and queue-foundation
boundaries but are not yet part of the current reconciliation boundary:

- background reconciliation scheduling;
- operator-triggered reconciliation;
- administrative reconciliation endpoints;
- operator RBAC;
- reconciliation audit history;
- persisted reconciliation attempts;
- provider rate-limit coordination;
- worker-backed reconciliation execution;
- structured reconciliation alerts;
- provider-state dashboards;
- multi-provider reconciliation policy;
- batch tenant reconciliation;
- invoice and payment reconciliation;
- entitlement correction;
- production worker isolation.

Durable Audit Logs now provide tenant-scoped persistence, trusted actor
attribution, domain and worker emission, idempotent recording, append-only
application behavior, and a tenant-scoped OWNER/ADMIN read API. The following
audit controls remain intentional follow-up work:

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
- Patients audit integration;
- Professionals audit integration;
- runtime database-role restrictions that deny `UPDATE` and `DELETE` on audit
  rows.