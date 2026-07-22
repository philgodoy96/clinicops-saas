# Billing Lifecycle

## Purpose

The ClinicOps billing module defines the commercial catalog, subscription
lifecycle rules, durable PostgreSQL persistence, and the tenant-scoped
subscription-creation workflow required by ClinicOps billing.

The implemented billing foundation now establishes:

- stable plan, billing interval, provider, operation, and subscription status
  identifiers;
- immutable server-owned price definitions;
- subscription lifecycle transition rules;
- period-end cancellation behavior;
- period-boundary price changes;
- provider state-version staleness detection;
- opaque client idempotency-key validation;
- deterministic billing command fingerprints;
- tenant-scoped billing persistence;
- durable outbound provider-operation records;
- durable inbound webhook-event records;
- repository locking and known-conflict translation;
- PostgreSQL schema, constraint, and concurrency coverage;
- a synchronous payment-provider port;
- immutable provider request and result contracts;
- deterministic provider operation keys;
- deterministic provider request fingerprints;
- calendar-accurate monthly and yearly billing periods;
- a thread-safe deterministic fake payment provider;
- provider-side idempotent replay;
- controlled retryable, terminal, and ambiguous provider outcomes;
- tenant-scoped billing subscription creation;
- billing mutation authorization through `BILLING_MANAGE`;
- required client `Idempotency-Key` handling;
- durable `ProviderOperation` reservation;
- customer creation and subscription creation as separate outbound operations;
- database commits before provider calls;
- result application in later transactions;
- successful API replay from the persisted `Subscription`;
- retryable and terminal provider failure persistence;
- ambiguous provider outcome recovery with the same provider operation key;
- PostgreSQL integration and concurrency coverage;
- tenant-scoped billing subscription reads;
- the persisted `Subscription` row as the read source of truth;
- `BILLING_READ` authorization;
- provider-independent public billing responses;
- explicit not-found behavior for tenants without a subscription;
- PostgreSQL-backed API integration coverage for tenant isolation;
- scheduled billing plan changes;
- tenant-scoped plan-change mutations;
- `BILLING_MANAGE` authorization;
- required client `Idempotency-Key` handling;
- durable `CHANGE_PLAN` provider operations;
- provider calls outside database transactions;
- persisted `pending_price_code`;
- successful idempotent replay;
- retryable, terminal, and ambiguous provider outcomes;
- PostgreSQL integration and concurrency coverage;
- tenant-scoped scheduled subscription cancellation;
- `BILLING_MANAGE` authorization;
- required client `Idempotency-Key` handling;
- durable `CANCEL_SUBSCRIPTION` provider operations;
- provider calls outside database transactions;
- period-end effective dates derived from persisted subscription state;
- persisted `cancel_at_period_end`;
- persisted `cancellation_requested_at`;
- pending plan-change cleanup;
- successful idempotent replay;
- retryable, terminal, and ambiguous provider outcomes;
- PostgreSQL integration and concurrency coverage;
- authenticated billing webhook ingestion;
- canonical provider event contracts;
- HMAC-SHA256 signature verification;
- signature timestamp tolerance;
- raw payload size enforcement;
- durable webhook receipt before acknowledgement;
- immutable payload hashing;
- provider event deduplication;
- conflicting duplicate detection;
- PostgreSQL concurrency protection;
- unknown local subscription acceptance;
- HTTP integration coverage.

This document distinguishes the implemented domain, persistence,
fake-provider, subscription-creation, subscription-read, scheduled
plan-change, scheduled cancellation, and webhook-ingestion workflow
foundations from the webhook processing, entitlement, worker, and
real-provider capabilities planned for later milestones.

## Module Ownership

The billing module currently owns:

- the supported ClinicOps billing plans;
- the supported billing intervals;
- stable public price codes;
- prices represented in integer minor units;
- immutable price definitions;
- subscription lifecycle status rules;
- scheduled price-change rules;
- period-end cancellation rules;
- provider state-version comparison;
- billing command fingerprinting;
- client idempotency-key validation;
- billing-domain exceptions;
- tenant billing-customer persistence;
- tenant subscription persistence;
- outbound provider-operation persistence;
- inbound billing webhook-event persistence;
- billing repositories and row-locking primitives;
- PostgreSQL constraints for core billing invariants;
- Alembic registration and migration parity;
- the synchronous payment-provider abstraction;
- provider request and result contracts;
- provider operation-key generation and validation;
- provider request fingerprinting;
- calendar-accurate billing-period calculation;
- deterministic fake provider identifiers;
- in-memory fake customer and subscription state;
- provider-side idempotency;
- controlled fake-provider failure simulation;
- fake-provider thread safety;
- tenant-scoped subscription-creation application orchestration;
- FastAPI billing subscription-creation routes;
- FastAPI billing subscription-read routes;
- `BILLING_MANAGE` authorization for billing mutations;
- `BILLING_READ` authorization for billing reads;
- required client `Idempotency-Key` extraction and validation at the HTTP
  boundary;
- durable `CREATE_CUSTOMER` and `CREATE_SUBSCRIPTION` reservation and claim
  orchestration;
- tenant-scoped scheduled plan-change application orchestration;
- FastAPI billing plan-change routes;
- durable `CHANGE_PLAN` reservation and claim orchestration;
- tenant-scoped scheduled cancellation application orchestration;
- FastAPI billing cancellation routes;
- durable `CANCEL_SUBSCRIPTION` reservation and claim orchestration;
- authenticated billing webhook HTTP ingestion;
- canonical provider webhook event validation;
- HMAC-SHA256 webhook signature verification;
- durable webhook-event receipt and deduplication.

The current billing implementation does not own:

- real payment-provider integrations;
- stale in-progress recovery;
- provider retry scheduling;
- background provider execution;
- webhook business processing;
- entitlement enforcement;
- background-job execution;
- audit-log persistence.

Those deferred capabilities are intentionally introduced in later milestones
after their security, transaction, failure, and recovery boundaries are
implemented explicitly.

## Billing Ownership

Billing belongs to a tenant.

A subscription is not owned by:

- a user;
- a membership;
- the current tenant owner.

Tenant ownership determines who may manage billing, but changing the tenant
owner does not transfer, replace, or recreate the tenant subscription.

Subscription creation is authorized through tenant-scoped
`BILLING_MANAGE`. The current policy grants that permission to tenant owners.

## V1 Commercial Catalog

ClinicOps V1 supports two plans:

```text
starter
professional
```

It supports two billing intervals:

```text
monthly
yearly
```

The V1 catalog contains four stable price codes:

| Price code | Plan | Billing interval | Currency | Unit amount |
|---|---|---:|---:|---:|
| `starter_monthly` | `starter` | `monthly` | `USD` | `4900` |
| `starter_yearly` | `starter` | `yearly` | `USD` | `49000` |
| `professional_monthly` | `professional` | `monthly` | `USD` | `9900` |
| `professional_yearly` | `professional` | `yearly` | `USD` | `99000` |

Amounts are stored in integer minor units:

```text
4900 USD minor units = USD 49.00
```

Binary floating-point values are not used for money.

API clients select a stable `price_code`. They do not
submit:

- monetary amounts;
- currency values;
- provider price identifiers;
- billing intervals independently from the selected price;
- tenant-controlled price definitions.

The server-owned catalog resolves those values.

## Immutable Price Definitions

Each catalog entry is represented by an immutable `PriceDefinition`.

Conceptually:

```text
PriceDefinition
├── price_code
├── plan
├── billing_interval
├── currency
├── unit_amount
└── provider_price_code
```

Catalog consumers receive immutable values and immutable collections.

The public ClinicOps price code remains distinct from the provider price
identifier. This prevents provider-specific identifiers from becoming part
of the tenant billing API contract.

The initial fake-provider mappings are stable implementation identifiers.
A future real provider may use different mappings without changing the
public ClinicOps price codes.

## Stable Billing Identifiers

Billing enums use stable lowercase serialized values.

### Plans

```text
starter
professional
```

### Billing intervals

```text
monthly
yearly
```

### Billing providers

```text
fake
```

### Subscription statuses

```text
pending
active
past_due
canceled
```

### Provider operation types

```text
create_customer
create_subscription
change_plan
cancel_subscription
```

Enum members are implementation symbols. Their serialized values are stable
domain contracts and must not be renamed casually after persistence or API
usage is introduced.

## Subscription Lifecycle

The V1 subscription lifecycle uses these confirmed statuses:

```text
pending
active
past_due
canceled
```

The allowed confirmed transitions are:

```text
pending -> active
pending -> canceled

active -> past_due
active -> canceled

past_due -> active
past_due -> canceled
```

The `canceled` state is terminal.

Invalid transitions include:

```text
active -> pending
past_due -> pending
canceled -> active
canceled -> past_due
```

Reapplying the same confirmed status is treated as an idempotent no-op by the
pure transition validator.

Persisted provider events and state versions will later determine whether a
same-status confirmation is a legitimate duplicate, a repeated event, or an
ignored stale event.

## Pure Lifecycle State

The domain foundation represents subscription lifecycle decisions through an
immutable state snapshot.

Conceptually:

```text
SubscriptionLifecycleState
├── status
├── price_code
├── current_period_end
├── pending_price_code
├── cancel_at_period_end
├── cancellation_requested_at
└── canceled_at
```

Lifecycle functions return a new state snapshot instead of mutating the input
object.

The state machine does not:

- access SQLAlchemy sessions;
- call payment providers;
- read FastAPI requests;
- commit transactions;
- calculate monthly or yearly period boundaries.

It receives already-calculated, timezone-aware period timestamps from future
application or provider workflows.

## Price Changes

A subscription may schedule a supported price change while its confirmed
status is:

```text
active
past_due
```

A scheduled change does not immediately replace the confirmed price.

Example:

```text
price_code = starter_monthly
pending_price_code = professional_monthly
```

Until the current period ends, the confirmed subscription price remains:

```text
starter_monthly
```

At the period boundary, a future provider-confirmation workflow may apply the
pending price:

```text
price_code = professional_monthly
pending_price_code = null
```

The pure rule requires:

- a supported target price code;
- a mutable subscription status;
- no scheduled cancellation;
- no existing pending price change;
- a target price different from the confirmed price;
- a timezone-aware confirmation timestamp;
- confirmation at or after `current_period_end`;
- a new period end later than the confirmation timestamp.

The V1 lifecycle does not calculate proration, credits, partial refunds, or
mid-period charges.

Plan and billing-interval changes take effect at the next billing-period
boundary.

## Period-End Cancellation

V1 cancellation is scheduled for the end of the current billing period.

Requesting cancellation produces a state conceptually similar to:

```text
status = active
cancel_at_period_end = true
cancellation_requested_at = <timestamp>
```

The subscription remains active until its current period ends.

A cancellation request clears any pending price change because a future plan
change is no longer relevant after the subscription is scheduled to end.

A new price change is rejected after cancellation has been scheduled.

Cancellation finalization requires:

- a previous cancellation request;
- a timezone-aware cancellation timestamp;
- cancellation at or after `current_period_end`;
- a valid transition to `canceled`.

Finalization produces:

```text
status = canceled
cancel_at_period_end = false
canceled_at = <timestamp>
pending_price_code = null
```

Finalizing an already canceled subscription returns the existing state as an
idempotent no-op.

Immediate cancellation, partial refunds, unused-period credits, and
reactivation are intentionally deferred.

## Provider State Versions

The pure foundation supports detection of stale provider state versions.

Conceptually:

```text
incoming_version < current_version
    -> stale
```

```text
incoming_version == current_version
    -> repeated or equivalent provider state
```

```text
incoming_version > current_version
    -> newer provider state
```

Negative provider state versions are invalid.

The current foundation only defines the comparison rule. A later webhook
processor will use persisted provider state versions to protect subscriptions
from duplicate and out-of-order events.

Example:

```text
Applied provider version 9:
    subscription canceled

Later received provider version 8:
    subscription active
```

Version 8 must not reactivate the canceled subscription.

## Client Idempotency Keys

Billing mutation routes require a client-generated `Idempotency-Key`.

The domain foundation defines the reusable validation contract. The billing
subscription-creation HTTP boundary extracts and requires the header.

A valid key is:

- treated as opaque;
- trimmed at its external boundaries;
- non-empty after trimming;
- no longer than 255 characters;
- case-sensitive;
- not required to be a UUID;
- never generated by the server for a client request.

Examples:

```text
6928ed2b-157f-4cb7-8925-fe81ac33ad22
mobile-app:retry:01
subscription-attempt-client-reference
```

ClinicOps does not extract tenant, billing period, plan, price, or operation
semantics from the key.

The key identifies one logical client operation. It is not a deterministic
monthly key such as:

```text
tenant_id:subscription:month_year
```

Such a key could collide with multiple legitimate operations initiated
during the same billing period.

## Billing Command Fingerprints

An idempotency key identifies a logical attempt. A command fingerprint
identifies the content of that attempt.

The fingerprint contract includes:

- the explicit provider operation type;
- explicit logical command fields;
- sorted JSON keys;
- compact JSON separators;
- UTF-8 encoding;
- SHA-256 hexadecimal output;
- enum serialization through stable `.value` strings.

Conceptual input:

```text
operation_type = create_subscription
price_code = starter_monthly
```

Canonical payload:

```json
{"fields":{"price_code":"starter_monthly"},"operation_type":"create_subscription"}
```

The fingerprint guarantees that:

- identical logical commands produce the same digest;
- dictionary insertion order does not change the digest;
- different operation types produce different digests;
- different command fields produce different digests;
- input mappings are not modified;
- enum values are serialized through their stable public values.

Fingerprints do not include:

- access tokens;
- passwords;
- webhook secrets;
- provider credentials;
- the client idempotency key itself;
- caller-supplied monetary amounts.

The selected `price_code` represents the commercial choice. Currency,
billing interval, and unit amount remain server-owned catalog values.

## Outbound Idempotency Boundary

`ProviderOperation` persistence is implemented. Provider operation-key
construction and provider-side fake replay are also implemented.

Conceptually:

```text
ProviderOperation
├── tenant_id
├── operation_type
├── idempotency_key
├── request_fingerprint
├── status
├── provider_reference
├── result
└── failure details
```

The subscription-creation workflow evaluates:

```text
No stored operation
    -> reserve a new operation
```

```text
Same key and same fingerprint
    -> replay or resume the original operation
```

```text
Same key and different fingerprint
    -> reject idempotency-key reuse
```

Subscription creation coordinates database reservation, provider calls, and
durable response replay. Stale in-progress recovery, scheduled retries, and
background provider execution remain deferred.

## Future Webhook Idempotency Boundary

Outbound command idempotency and inbound webhook idempotency are separate
reliability concerns.

Future webhook processing will use:

```text
provider + provider_event_id
```

as the event-delivery deduplication boundary.

Provider state versions will separately protect the subscription transition
from stale and out-of-order events.

The planned flow is:

```text
Verify raw-body signature
    -> persist or resolve provider event
    -> reject duplicate application of the same event
    -> compare provider state version
    -> apply or ignore subscription transition
```

Webhook event processing, subscription mutation from events, and background
processing services remain intentionally deferred.

## Implemented Billing Webhook Ingestion

ClinicOps exposes one provider-facing ingestion endpoint:

```text
POST /api/v1/billing/webhooks/{provider}
```

The first supported provider is:

```text
POST /api/v1/billing/webhooks/fake
```

The endpoint is not tenant-scoped.

It does not require:

- a Bearer access token;
- a user session;
- tenant membership;
- tenant permissions;
- a client `Idempotency-Key`.

Provider authentication is performed with a timestamped HMAC signature.

## Canonical Webhook Event Envelope

ClinicOps accepts these canonical event types:

```text
subscription.renewed
subscription.canceled
```

Example:

```json
{
  "id": "evt_renewed_01",
  "type": "subscription.renewed",
  "created_at": "2026-08-22T12:00:00Z",
  "data": {
    "provider_subscription_id": "fake_sub_01",
    "provider_state_version": 4,
    "price_code": "professional_monthly",
    "status": "active",
    "current_period_start": "2026-08-22T12:00:00Z",
    "current_period_end": "2026-09-22T12:00:00Z",
    "canceled_at": null
  }
}
```

The contracts require:

- nonempty provider identifiers;
- timezone-aware timestamps;
- `provider_state_version >= 1`;
- `current_period_end > current_period_start`;
- no unknown fields;
- lifecycle consistency between event type, status, and `canceled_at`.

Lifecycle consistency:

```text
subscription.renewed
    -> status = active
    -> canceled_at = null

subscription.canceled
    -> status = canceled
    -> canceled_at != null
```

The provider cannot supply local tenant IDs or local subscription IDs.

## Webhook Signature Model

The fake provider signature header uses:

```text
X-Billing-Signature: t=<unix_timestamp>,v1=<hex_digest>
```

The signed message is:

```text
<timestamp>.<raw_request_body>
```

The digest uses:

```text
HMAC-SHA256
```

Verification order:

```text
read exact request bytes
    -> enforce payload-size limit
    -> parse signature header
    -> validate timestamp tolerance
    -> calculate expected HMAC
    -> compare in constant time
    -> parse canonical JSON event
```

The exact raw request bytes are authenticated before JSON parsing.

The default signature tolerance is:

```text
300 seconds
```

The default payload limit is:

```text
256 KiB
```

The shared secret is provided by:

```text
CLINICOPS_BILLING_WEBHOOK_SECRET
```

Deployed environments must not use the local default secret.

The secret, complete signature, digest, and raw payload are not written to
logs or returned in responses.

## Durable Receipt Semantics

A valid delivery is acknowledged only after its event row commits.

The workflow is:

```text
authenticate raw request bytes
    -> validate canonical event
    -> calculate SHA-256 over exact raw bytes
    -> reserve or replay BillingWebhookEvent
    -> commit
    -> return 202 Accepted
```

The public response is:

```json
{
  "received": true
}
```

Both a new event and an identical duplicate return:

```text
202 Accepted
```

The public response does not expose:

- whether the event was new or duplicate;
- the local webhook event ID;
- processing status;
- processing attempts;
- payload hashes;
- signature timestamps;
- provider state details.

A database failure before commit must not produce a successful receipt.

## Persisted Webhook Event

The existing `BillingWebhookEvent` persistence model stores:

```text
id
provider
provider_event_id
event_type
provider_subscription_id
provider_created_at
provider_state_version
payload
payload_sha256
signature_timestamp
status
processing_attempt_count
processed_at
failure_code
failure_message
correlation_id
created_at
updated_at
```

A newly ingested event is persisted as:

```text
status = received
processing_attempt_count = 0
processed_at = null
failure_code = null
failure_message = null
```

The ingestion workflow does not change the event to `processing`.

That lifecycle transition belongs to webhook processing.

## Webhook Event Idempotency

The durable uniqueness boundary is:

```text
provider + provider_event_id
```

The immutable content boundary is:

```text
SHA-256(exact raw request bytes)
```

Behavior:

```text
same provider
+ same provider event ID
+ same raw-body hash
    -> accepted duplicate
    -> no second row
    -> 202 Accepted

same provider
+ same provider event ID
+ different raw-body hash
    -> webhook event conflict
    -> 409 Conflict
```

The hash is not calculated from a reserialized JSON object.

Whitespace, field ordering, and any other byte-level changes produce a
different hash.

This intentionally treats the provider event ID as identifying one immutable
delivery payload.

## Concurrent Webhook Delivery

Two requests may both observe no existing event before either transaction
commits.

The final protection is the database uniqueness constraint on:

```text
provider + provider_event_id
```

Concurrent identical delivery:

```text
request A inserts
request B inserts
request A commits
request B receives a unique conflict
request B rolls back
request B reloads the committed event
request B compares payload_sha256
request B returns an idempotent acknowledgement
```

Concurrent conflicting delivery:

```text
request A and request B reuse the same provider event ID
with different raw bytes

    -> one row wins
    -> the competing request reloads the winner
    -> hashes differ
    -> 409 Conflict
```

Each concurrent test execution uses an independent SQLAlchemy `Session`.

The implementation does not depend only on a read-before-write check.

## Unknown Local Subscription Handling

Webhook ingestion does not require a matching local `Subscription`.

An authenticated event may be persisted when:

```text
provider_subscription_id is unknown locally
```

Reasons include:

- event delivery before local state creation;
- provider and application timing differences;
- recovery after partial failure;
- data divergence requiring reconciliation;
- manually imported or legacy provider state.

The ingestion layer owns durable receipt, not lifecycle application.

A later processing workflow will resolve the subscription using:

```text
provider + provider_subscription_id
```

and apply ordering and lifecycle rules.

## Ingestion and Processing Separation

Webhook ingestion is intentionally limited to:

- request authentication;
- payload boundary validation;
- canonical event validation;
- immutable payload hashing;
- durable event persistence;
- duplicate replay;
- conflicting duplicate detection;
- durable acknowledgement.

It does not:

- lock or mutate a subscription;
- apply plan changes;
- finalize cancellation;
- validate provider event ordering;
- reject stale provider state versions;
- update entitlements;
- enqueue retries;
- perform reconciliation.

This keeps the provider request lifecycle short and ensures the event is
durable before domain processing begins.

## Time Handling

All lifecycle timestamps must be timezone-aware.

The pure state machine rejects naive datetimes.

The pure lifecycle compares supplied period boundaries but does not calculate
them.

Monthly and yearly period calculation is implemented in the provider
foundation through calendar arithmetic. That layer normalizes timezone-aware
timestamps to UTC and clamps month-end and leap-year anniversaries to valid
calendar days.

No new calendar library is introduced for billing-period calculation.

## Exception Boundary

Billing-domain exceptions derive from the repository-wide application error
contract.

They expose:

- a stable machine-readable `code`;
- a client-safe `public_message`;
- optional structured domain context.

They do not contain:

- HTTP status codes;
- FastAPI response objects;
- database-session concerns;
- provider credentials;
- client secrets.

HTTP Problem Details mappings exist for the implemented billing subscription
mutation routes.

## Dependency Boundaries

The billing module now contains two implemented layers.

The pure domain foundation depends on:

```text
billing catalog
billing enums
billing lifecycle rules
billing idempotency validation
billing command fingerprints
    -> core application exceptions
```

The persistence foundation additionally depends on:

```text
billing models
billing repositories
    -> SQLAlchemy
    -> PostgreSQL
    -> tenant foreign keys
    -> billing-domain exceptions
```

The persistence layer does not depend on:

```text
FastAPI
payment-provider clients
webhook request objects
background workers
```

Application services, provider adapters, API routes, and workers depend on
the domain and persistence layers. The domain and persistence foundations
must not depend on those transports or execution mechanisms.

## Implemented Persistence Boundary

ClinicOps persists four billing entities:

```text
BillingCustomer
Subscription
ProviderOperation
BillingWebhookEvent
```

All billing tables use UUID primary keys generated by the application,
timezone-aware timestamps, explicit foreign keys, named constraints, and
native PostgreSQL enum types.

### BillingCustomer

`BillingCustomer` represents a tenant's durable customer identity at one
payment provider.

Conceptually:

```text
BillingCustomer
├── id
├── tenant_id
├── provider
├── provider_customer_id
├── created_at
└── updated_at
```

The provider customer identifier is nullable while provider-side creation is
pending.

The database enforces:

```text
one customer per tenant and provider
unique non-null provider customer identifier per provider
```

Deleting a tenant cascades to its billing-customer records.

### Subscription

`Subscription` represents the tenant's V1 subscription lifecycle.

Conceptually:

```text
Subscription
├── id
├── tenant_id
├── billing_customer_id
├── provider
├── provider_subscription_id
├── price_code
├── plan
├── billing_interval
├── currency
├── unit_amount
├── pending_price_code
├── status
├── cancel_at_period_end
├── cancellation_requested_at
├── current_period_start
├── current_period_end
├── provider_state_version
├── last_provider_event_at
├── canceled_at
├── created_at
└── updated_at
```

The confirmed catalog values are snapshotted when the local subscription row
is created.

This preserves the commercial terms associated with the subscription even if
a future catalog definition changes.

The database enforces one subscription lifecycle per tenant in V1.

Provider subscription identifiers and period boundaries may remain null while
the subscription is pending provider confirmation.

Core database checks enforce:

- positive integer minor-unit amounts;
- nonnegative provider state versions;
- pending price codes different from the confirmed price code;
- complete and ordered billing-period timestamp pairs;
- active and past-due subscriptions with complete billing periods;
- cancellation flags and timestamps that form a consistent persisted state;
- canceled subscriptions with a cancellation timestamp.

The transition graph and period-boundary timing remain application-domain
rules.

### ProviderOperation

`ProviderOperation` is the durable outbound idempotency boundary.

Conceptually:

```text
ProviderOperation
├── id
├── tenant_id
├── billing_customer_id
├── subscription_id
├── provider
├── operation_type
├── idempotency_key
├── request_fingerprint
├── request_payload
├── status
├── attempt_count
├── provider_reference
├── result_payload
├── failure_code
├── failure_message
├── started_at
├── completed_at
├── created_at
└── updated_at
```

The database scopes client idempotency through:

```text
tenant_id
+
operation_type
+
idempotency_key
```

The request fingerprint is stored separately so the subscription-creation
workflow can distinguish:

```text
same key + same fingerprint
    -> replay or resume

same key + different fingerprint
    -> reject key reuse
```

The operation schema supports these durable states:

```text
pending
in_progress
succeeded
failed_retryable
failed_terminal
```

No retry executor exists yet. The status model preserves the distinction
required by later reliability and worker milestones.

JSONB request and result payloads store structured provider-safe metadata.
They must never contain secrets, credentials, authorization material, or
webhook signatures.

### BillingWebhookEvent

`BillingWebhookEvent` is the durable inbound event identity.

Conceptually:

```text
BillingWebhookEvent
├── id
├── provider
├── provider_event_id
├── event_type
├── provider_subscription_id
├── provider_created_at
├── provider_state_version
├── payload
├── payload_sha256
├── signature_timestamp
├── status
├── processing_attempt_count
├── processed_at
├── failure_code
├── failure_message
├── correlation_id
├── created_at
└── updated_at
```

The database enforces one event per:

```text
provider + provider_event_id
```

The event schema supports:

```text
received
processed
ignored
failed_retryable
failed_terminal
```

The provider state version is nullable because not every future provider event
must necessarily carry an object-state version.

The JSONB payload stores the verified provider event body after signature
verification. Signature headers and secrets are never persisted.

Webhook ingestion, HMAC verification, event deduplication, and durable receipt
are implemented. Event normalization beyond canonical validation and business
processing remain intentionally deferred.

## Repository Boundary

Billing persistence is accessed through four repositories:

```text
BillingCustomerRepository
SubscriptionRepository
ProviderOperationRepository
BillingWebhookEventRepository
```

Repositories:

- receive a SQLAlchemy `Session` per method;
- return billing ORM entities;
- use tenant-scoped or provider-scoped queries;
- expose `SELECT ... FOR UPDATE` primitives where future workflows require
  serialization;
- add and flush entities;
- flush pending updates;
- translate only known uniqueness conflicts into billing-domain exceptions;
- never commit;
- never roll back;
- never call payment providers;
- never implement HTTP behavior.

Transaction ownership remains outside the repository boundary.

API routes own their outer request scope. The subscription-creation
orchestrator is an explicit exception that commits short transactions around
provider calls. Other application services continue to coordinate
repositories and flush without committing unless a later workflow documents
the same exception.

## Persistence Locking Model

The persistence layer exposes locking primitives used by billing workflows.

The tenant billing mutation order is:

```text
Tenant
    -> BillingCustomer
    -> Subscription
    -> ProviderOperation
```

Webhook processing will use a separate entry order:

```text
BillingWebhookEvent
    -> resolve local billing ownership
    -> Subscription
```

Webhook events are not forced into the tenant-initiated mutation lock chain.

Lock orchestration for subscription creation is an application-service
responsibility.

## Persistence Concurrency Guarantees

PostgreSQL integration tests verify that concurrent transactions preserve:

- one billing customer per tenant and provider;
- one subscription lifecycle per tenant;
- one provider operation per tenant, operation type, and idempotency key;
- one webhook event per provider and provider event identifier;
- row-level exclusion through `SELECT ... FOR UPDATE`.

Concurrent tests use independent database sessions and committed fixtures.

Each test scopes verification and cleanup to identifiers owned by that test.
No billing integration assertion assumes the global database is empty.

## Implemented Payment-Provider Boundary

ClinicOps now defines a synchronous `PaymentProvider` port.

The port exposes four billing mutations:

```text
create_customer
create_subscription
change_plan
cancel_subscription
```

Each method accepts an immutable request contract and returns an immutable
provider-confirmed result contract.

The provider boundary does not receive:

- SQLAlchemy sessions;
- repositories;
- FastAPI request objects;
- tenant authorization context;
- client access tokens;
- provider credentials;
- webhook requests;
- background-job records.

Tenant ownership and local transaction coordination remain outside the
provider port.

Provider requests use ClinicOps public `price_code` values. A provider adapter
is responsible for translating those values to its provider-specific price
identifiers.

The current port is synchronous. Background execution may call the same port
later without changing the provider contract.

## Provider Operation Keys

Client idempotency keys and provider operation keys are separate identities.

Client billing mutations use an opaque client-generated key persisted on
`ProviderOperation`.

Provider calls use a stable internal key derived from the persisted provider
operation ID:

```text
clinicops:<provider_operation_uuid>
```

Example:

```text
clinicops:96f33a49-1685-4ae6-aee4-fb5ebddaa94c
```

The provider operation key:

- is generated by ClinicOps;
- uses the canonical persisted operation UUID;
- is globally unique within a provider instance;
- is limited to 255 characters;
- is not exposed as the client idempotency contract;
- remains stable across retries of the same outbound operation.

Provider request fingerprints are calculated separately from the key.

The fingerprint includes:

- the provider operation type;
- explicit provider request fields;
- canonical UUID strings;
- UTC ISO-8601 timestamps;
- stable enum values;
- sorted compact JSON;
- SHA-256 hexadecimal output.

Unsupported fingerprint value types are rejected. Silent serialization
fallbacks are not used.

## Billing-Period Calculation

The provider foundation calculates monthly and yearly periods through calendar
arithmetic.

Monthly periods advance one calendar month.

Yearly periods advance one calendar year.

Month-end and leap-year dates are clamped to the final valid day of the target
month.

Examples:

```text
2026-01-31 -> 2026-02-28
2028-01-31 -> 2028-02-29
2028-02-29 -> 2029-02-28
```

Input timestamps must be timezone-aware and are normalized to UTC before
calendar arithmetic.

The implementation does not approximate:

```text
one month = 30 days
one year = 365 days
```

No external date-arithmetic dependency is required.

## Deterministic Fake Payment Provider

`FakePaymentProvider` is the implemented local payment-provider adapter.

It is intentionally used before a real payment provider to provide:

- deterministic local development;
- repeatable tests;
- provider-side idempotency simulation;
- controlled temporary failures;
- controlled terminal rejections;
- ambiguous timeout simulation;
- subscription state-version behavior;
- calendar-period confirmation;
- zero external credentials;
- zero payment-processing cost.

The fake provider owns in-memory representations of:

```text
provider customers
provider subscriptions
provider operation outcomes
```

It supports one provider subscription per fake customer in V1.

Provider identifiers are derived deterministically from the provider operation
key through SHA-256.

Formats are:

```text
fake_cus_<stable token>
fake_sub_<stable token>
fake_op_<stable token>
```

A successful subscription creation establishes provider state version `1`.

Each successful distinct plan change or cancellation increments the
subscription provider state version exactly once.

The fake provider models provider-confirmed mutation outcomes. It does not
model the complete local subscription lifecycle, tenant authorization, API
transport, database transactions, or entitlement behavior.

## Fake Provider Idempotency

The fake provider stores operation outcomes by provider operation key.

The implemented behavior is:

```text
same provider key + same request
    -> return the stored result
```

```text
same provider key + different request
    -> raise a provider idempotency conflict
```

The provider operation type and request fingerprint are both checked before a
stored result is replayed.

Provider-side replay is independent from the local database behavior that
compares the client idempotency key and the persisted command fingerprint.

## Controlled Provider Outcomes

Failure simulation is configured through a dedicated `FakeProviderControl`.

Test-only outcome controls do not appear in application-facing provider
request contracts.

Outcomes are queued by provider operation type:

```text
success
retryable_failure
terminal_rejection
ambiguous_success
```

### Retryable failure

A retryable failure occurs before provider-side mutation.

It does not:

- create provider state;
- reserve a provider operation outcome;
- prevent a later retry with the same key.

A later retry may execute normally.

### Terminal rejection

A terminal rejection is stored as the provider-side outcome for that key.

A retry with the same key and the same request reproduces the terminal
failure.

A different provider operation key represents a different provider attempt.

### Ambiguous success

An ambiguous success models a timeout after provider-side mutation.

The fake provider:

1. applies the provider mutation;
2. stores the successful idempotent result;
3. raises an ambiguous-outcome exception to the caller.

A retry with the same key and request returns the stored successful result.

The retry does not:

- create another customer;
- create another subscription;
- apply the plan change again;
- apply the cancellation again;
- increment the provider state version again.

This behavior models the reason stable provider-side idempotency is required
for real external integrations.

## Fake Provider Thread Safety

The fake provider protects its in-memory state through one instance-level
lock.

Each idempotency check, state mutation, and result persistence occurs within
the same critical section.

For ambiguous success, the successful result is persisted before the
ambiguous-outcome exception is raised.

The simple single-lock design is intentional. It prevents provider-side
idempotency races without introducing unnecessary lock hierarchies into an
in-memory development adapter.

## Future Real Provider Integrations

The payment-provider port and deterministic fake implementation are now
implemented.

Real provider adapters remain intentionally deferred.

A real adapter will require explicit decisions for:

- credential storage and rotation;
- provider API authentication;
- network timeouts;
- transport retry policy;
- rate limits;
- provider-specific error translation;
- provider price identifier mapping;
- API-version management;
- provider observability;
- secret redaction;
- reconciliation behavior;
- sandbox and production account separation.

A real provider must preserve the same application-facing port and provider
operation-key semantics where the external API supports idempotency.

The fake provider is not presented as a real payment integration.

## Implemented Subscription-Creation Workflow

ClinicOps now exposes one tenant-scoped subscription-creation mutation:

```text
POST /api/v1/tenants/{tenant_id}/billing/subscription
```

The request body is:

```json
{
  "price_code": "starter_monthly"
}
```

The request must also include:

```text
Idempotency-Key: <opaque client-generated key>
```

The client does not control:

- tenant ownership;
- amount;
- currency;
- billing interval;
- provider customer identifiers;
- provider subscription identifiers;
- provider operation keys;
- subscription status;
- provider state versions.

The server resolves pricing from the billing catalog and tenant identity from
the authorized path context.

The mutation requires:

```text
TenantPermission.BILLING_MANAGE
```

The current authorization policy grants billing management to tenant owners.
Administrators have billing read access only, and staff members do not receive
billing administration permissions.

## Implemented Subscription Read API

ClinicOps exposes the current persisted billing subscription for an authorized
tenant:

```text
GET /api/v1/tenants/{tenant_id}/billing/subscription
```

The endpoint returns:

```text
200 OK
application/json
```

The response contains:

- subscription ID;
- tenant ID;
- public price code;
- plan;
- billing interval;
- currency;
- unit amount;
- subscription status;
- current billing-period boundaries;
- cancellation scheduling state;
- pending price-code state;
- creation and update timestamps.

The public response does not contain:

- provider customer IDs;
- provider subscription IDs;
- provider references;
- provider operation IDs;
- client idempotency keys;
- internal failure information;
- provider credentials.

The read route does not require an `Idempotency-Key` because it does not
mutate billing state.

## Subscription Read Source of Truth

The local `Subscription` row is the source of truth for the read API.

The request path is:

```text
HTTP route
    -> authorized tenant context
    -> GetBillingSubscriptionService
    -> SubscriptionRepository.get_by_tenant_id
    -> BillingSubscriptionResponse
```

The read workflow does not consult:

- `FakePaymentProvider` in-memory state;
- external payment-provider APIs;
- `ProviderOperation.result_payload`;
- billing webhook events;
- background-job state.

This keeps the public API independent from a specific provider adapter and
prevents external provider latency from affecting a local billing read.

The query uses the tenant-scoped subscription lookup without:

- database writes;
- `flush`;
- `commit`;
- `rollback`;
- `SELECT FOR UPDATE`;
- provider calls;
- clock access.

## Subscription Read Authorization

The endpoint requires:

```text
TenantPermission.BILLING_READ
```

The current policy grants:

```text
OWNER
    -> billing read allowed

ADMIN
    -> billing read allowed

STAFF
    -> billing read denied
```

The route relies on the existing persisted tenant-context resolution.

The access token identifies the authenticated user and session, but it does
not carry tenant billing authority.

The tenant ID comes from the path and is evaluated against the authenticated
user's current membership.

## Missing Subscription Behavior

A tenant without a persisted subscription receives:

```text
404 Not Found
application/problem+json
```

Stable application error code:

```text
billing_subscription_not_found
```

The API returns an explicit not-found response rather than:

- `null`;
- an empty object;
- a synthetic free subscription;
- provider state;
- a placeholder subscription.

This preserves a clear distinction between:

```text
tenant has no subscription lifecycle
```

and:

```text
tenant has a persisted subscription in a specific state
```

## Implemented Scheduled Plan Changes

ClinicOps exposes one tenant-scoped plan-change mutation:

```text
POST /api/v1/tenants/{tenant_id}/billing/subscription/plan-change
```

The request body is:

```json
{
  "price_code": "professional_monthly"
}
```

The request must include:

```text
Idempotency-Key: <opaque client-generated key>
```

The mutation requires:

```text
TenantPermission.BILLING_MANAGE
```

The current authorization policy grants billing management to tenant owners.

Administrators retain billing read access but cannot schedule billing changes.
Staff members do not receive billing administration permissions.

## Scheduled Plan-Change Semantics

A plan change is scheduled for the current subscription period boundary.

The active local billing state remains unchanged:

```text
price_code
plan
billing_interval
currency
unit_amount
current_period_start
current_period_end
```

The confirmed future target is stored as:

```text
pending_price_code
```

Example:

```text
Before:
    price_code = starter_monthly
    pending_price_code = null

After scheduling:
    price_code = starter_monthly
    pending_price_code = professional_monthly
```

The target price is not promoted to the active price during this workflow.

Application of the pending target at renewal is intentionally deferred to
future webhook or reconciliation processing.

## Plan-Change Eligibility

A plan change may be scheduled only when:

- a persisted subscription exists for the tenant;
- the subscription status is `active`;
- `cancel_at_period_end` is false;
- no `pending_price_code` already exists;
- the target price exists in the billing catalog;
- the target price differs from the active `price_code`.

Conflicts are explicit:

```text
target equals active price
    -> 409 Conflict

subscription is not active
    -> 409 Conflict

subscription is scheduled for cancellation
    -> 409 Conflict

another plan change is already pending
    -> 409 Conflict
```

A new idempotency key cannot replace an existing pending target.

Pending-plan replacement and pending-plan cancellation are intentionally
deferred.

## Durable Plan-Change Operation

Every provider-side plan change is represented by:

```text
ProviderOperationType.CHANGE_PLAN
```

The uniqueness boundary remains:

```text
tenant_id + operation_type + idempotency_key
```

The internal provider key is derived from the persisted operation UUID:

```text
clinicops:<provider_operation_uuid>
```

The command fingerprint includes:

```text
provider_subscription_id
target_price_code
```

Client-idempotency behavior:

```text
same tenant + same client key + same target
    -> resume or replay

same tenant + same client key + different target
    -> 409 Conflict

new client key + existing pending target
    -> 409 Conflict
```

A successful replay is reconstructed from the persisted `Subscription` row.

The public HTTP response does not expose:

- provider operation IDs;
- provider operation keys;
- provider references;
- provider subscription IDs;
- request fingerprints;
- the internal `replayed` flag.

## Plan-Change Transaction Boundaries

The application orchestrator intentionally owns multiple commits.

The workflow is:

```text
Transaction A
    -> lock Subscription
    -> validate lifecycle state
    -> load or reserve CHANGE_PLAN
    -> validate client fingerprint
    -> claim operation as in_progress
    -> commit

Provider call
    -> no database transaction open

Transaction B
    -> reload and lock ProviderOperation
    -> reload and lock Subscription
    -> revalidate local state
    -> validate provider result
    -> persist pending_price_code
    -> persist provider_state_version
    -> mark operation succeeded
    -> commit
```

The provider result must confirm:

- the same provider subscription;
- the requested target price;
- the current local period boundary as the effective date;
- a provider state version greater than the persisted version.

The local active price and current period remain unchanged after the provider
confirms the scheduled target.

## Fake Provider Plan-Change Behavior

The fake provider stores:

```text
pending_price_code
pending_effective_at
```

It preserves the currently active provider price and period.

The same provider operation key replays the same stored result.

A different provider operation key is rejected while another plan change is
pending.

The fake provider supports:

- successful execution;
- retryable pre-mutation failure;
- terminal rejection;
- ambiguous success;
- same-key success replay;
- same-key terminal failure replay.

An ambiguous success mutates provider state once, raises an ambiguous outcome
to the caller, and then replays the stored result when retried with the same
provider operation key.

## Plan-Change Failure Persistence

Retryable and ambiguous provider outcomes are persisted as:

```text
failed_retryable
```

The API returns:

```text
503 Service Unavailable
```

A later request with the same client key may reclaim the operation and retry
using the same provider operation key.

Terminal provider outcomes are persisted as:

```text
failed_terminal
```

The API returns:

```text
409 Conflict
```

The same client key does not call the provider again after a terminal outcome.

Business conflicts discovered before the provider call do not create an
external side effect.

Business conflicts discovered after the provider call are persisted as a
terminal operation failure.

## Plan-Change Concurrency Guarantees

The implementation combines:

- PostgreSQL row-level locks;
- provider-operation uniqueness;
- client command fingerprints;
- committed `in_progress` ownership;
- provider-side idempotency;
- one pending target on the local subscription.

Concurrent same-key requests produce one logical plan change.

The competing request may:

- replay the completed result; or
- receive an operation-in-progress conflict.

Concurrent requests using different keys cannot establish two active pending
targets.

One target wins and the other request is rejected by local lifecycle
validation or provider state validation.

The implementation provides at-least-once provider attempts with idempotent
effect boundaries.

It does not claim exactly-once execution.

## Implemented Scheduled Subscription Cancellation

ClinicOps exposes one tenant-scoped cancellation mutation:

```text
POST /api/v1/tenants/{tenant_id}/billing/subscription/cancellation
```

The request has no body.

It must include:

```text
Idempotency-Key: <opaque client-generated key>
```

The mutation requires:

```text
TenantPermission.BILLING_MANAGE
```

The current authorization policy grants billing management to tenant owners.

Administrators retain billing read access but cannot mutate billing lifecycle
state.

Staff members do not receive billing administration permissions.

## Scheduled Cancellation Semantics

A cancellation is scheduled for the current subscription period boundary.

The active subscription remains usable until:

```text
current_period_end
```

After provider confirmation, ClinicOps persists:

```text
cancel_at_period_end = true
cancellation_requested_at = current application time
pending_price_code = null
```

The workflow preserves:

```text
status = active
canceled_at = null
price_code
plan
billing_interval
currency
unit_amount
current_period_start
current_period_end
```

Example:

```text
Before:
    status = active
    cancel_at_period_end = false
    cancellation_requested_at = null
    canceled_at = null

After scheduling:
    status = active
    cancel_at_period_end = true
    cancellation_requested_at = application timestamp
    canceled_at = null
```

The subscription is not promoted to `canceled` during this workflow.

Final cancellation is intentionally deferred to future webhook or
reconciliation processing after provider confirmation that the paid period
has ended.

## Cancellation Precedence Over Pending Plan Changes

Cancellation takes precedence over a pending plan change.

When a subscription has:

```text
pending_price_code = professional_monthly
```

and cancellation is successfully scheduled, ClinicOps persists:

```text
pending_price_code = null
cancel_at_period_end = true
```

The active `price_code` remains unchanged.

The fake provider clears its corresponding pending plan-change state as part
of the cancellation transition.

This prevents contradictory period-boundary instructions such as:

```text
change plan at period end
and
cancel subscription at period end
```

A pending plan change does not block cancellation.

Cancellation removes that pending target only after the provider confirms the
scheduled cancellation.

## Cancellation Eligibility

A cancellation may be scheduled only when:

- a persisted subscription exists for the tenant;
- the subscription status is `active`;
- `canceled_at` is null;
- `cancel_at_period_end` is false;
- `provider_subscription_id` exists;
- `current_period_end` exists.

Lifecycle conflicts are explicit:

```text
subscription is not active
    -> 409 Conflict

subscription is already canceled
    -> 409 Conflict

cancellation is already pending
    -> 409 Conflict
```

A pending plan change does not produce a conflict because cancellation takes
precedence.

## Durable Cancellation Operation

Every provider-side cancellation is represented by:

```text
ProviderOperationType.CANCEL_SUBSCRIPTION
```

The uniqueness boundary remains:

```text
tenant_id + operation_type + idempotency_key
```

The provider-side operation key is derived from the persisted operation UUID:

```text
clinicops:<provider_operation_uuid>
```

The application command fingerprint includes:

```text
provider_subscription_id
effective_at
```

The effective date is derived from:

```text
Subscription.current_period_end
```

The client cannot select or override the effective date.

Client-idempotency behavior:

```text
same tenant + same client key + same effective boundary
    -> resume or replay

same tenant + same client key + changed effective boundary
    -> 409 Conflict

new client key + existing pending cancellation
    -> 409 Conflict
```

A successful replay is reconstructed from the persisted `Subscription` row.

The public HTTP response does not expose:

- provider operation IDs;
- provider operation keys;
- provider references;
- provider subscription IDs;
- request fingerprints;
- provider result payloads;
- the internal `replayed` flag.

## Cancellation Transaction Boundaries

The cancellation orchestrator intentionally owns multiple commits.

The workflow is:

```text
Transaction A
    -> lock Subscription
    -> validate lifecycle state
    -> load or reserve CANCEL_SUBSCRIPTION
    -> validate client fingerprint
    -> claim operation as in_progress
    -> commit

Provider call
    -> no database transaction open

Transaction B
    -> reload and lock ProviderOperation
    -> reload and lock Subscription
    -> revalidate local state
    -> validate provider result
    -> set cancel_at_period_end
    -> set cancellation_requested_at
    -> clear pending_price_code
    -> persist provider_state_version
    -> mark operation succeeded
    -> commit
```

The provider result must confirm:

- the same provider subscription;
- the persisted current period boundary as the effective cancellation date;
- a provider state version greater than the current local version.

The provider result field named `canceled_at` represents the provider-confirmed
future effective cancellation date for this command boundary.

ClinicOps does not copy that field into the local `Subscription.canceled_at`
column during scheduling.

## Fake Provider Cancellation Behavior

The fake provider stores:

```text
pending_cancellation_at
```

It preserves the subscription as not yet finally canceled.

A successful cancellation request:

- validates the current period boundary;
- stores the pending cancellation date;
- clears pending plan-change state;
- increments `provider_state_version`;
- stores the result for same-key replay.

A new cancellation operation key is rejected while cancellation is pending.

A new plan-change request is rejected after cancellation is pending.

The fake provider supports:

- successful cancellation scheduling;
- retryable pre-mutation failure;
- terminal rejection;
- ambiguous success;
- same-key success replay;
- same-key terminal failure replay.

An ambiguous success mutates provider state once, reports an ambiguous outcome,
and then replays the stored result when the same provider operation key is
retried.

## Cancellation Failure Persistence

Retryable and ambiguous provider outcomes are persisted as:

```text
failed_retryable
```

The API returns:

```text
503 Service Unavailable
```

A later request with the same client key may reclaim the operation and retry
with the same provider operation key.

Terminal provider outcomes are persisted as:

```text
failed_terminal
```

The API returns:

```text
409 Conflict
```

The same client key does not call the provider again after a terminal outcome.

Business conflicts discovered before the provider call do not create an
external side effect.

Business conflicts discovered after provider success are persisted as a
terminal local operation failure.

## Cancellation Concurrency Guarantees

The implementation combines:

- PostgreSQL row-level locks;
- provider-operation uniqueness;
- client command fingerprints;
- committed `in_progress` ownership;
- provider-side idempotency;
- monotonic provider-state versions;
- one local pending cancellation flag.

Concurrent requests with the same client key produce one logical
cancellation.

The competing request may:

- replay the completed result; or
- receive an operation-in-progress conflict.

Concurrent requests with different keys cannot establish two independent
pending cancellations.

One request succeeds and the competitor is rejected by local lifecycle
validation or provider state validation.

The implementation provides at-least-once provider attempts with idempotent
effect boundaries.

It does not claim exactly-once execution.

## Durable Workflow Decomposition

A single client subscription request may create two durable outbound
operations:

```text
CREATE_CUSTOMER
CREATE_SUBSCRIPTION
```

When a tenant already has a linked billing customer, only
`CREATE_SUBSCRIPTION` is required.

The same validated client idempotency key is stored on both operation types.
The database uniqueness boundary remains:

```text
tenant_id + operation_type + idempotency_key
```

Each persisted provider operation has its own UUID and therefore its own
provider-side operation key:

```text
clinicops:<provider_operation_uuid>
```

Customer creation is never performed as an untracked provider side effect.

This preserves recovery after partial success, including the case where
provider customer creation succeeds but subscription creation has not started
or completed.

## Transaction Boundaries

Subscription creation intentionally uses multiple short database
transactions.

The full path without an existing billing customer is:

```text
Transaction A
    -> validate or reserve CREATE_CUSTOMER
    -> validate or reserve CREATE_SUBSCRIPTION
    -> claim CREATE_CUSTOMER as in_progress
    -> commit

Provider customer call
    -> no database transaction open

Transaction B
    -> persist BillingCustomer
    -> mark CREATE_CUSTOMER succeeded
    -> claim CREATE_SUBSCRIPTION as in_progress
    -> commit

Provider subscription call
    -> no database transaction open

Transaction C
    -> persist active Subscription
    -> mark CREATE_SUBSCRIPTION succeeded
    -> commit
```

When the billing customer already exists:

```text
Transaction A
    -> validate or reserve CREATE_SUBSCRIPTION
    -> claim CREATE_SUBSCRIPTION as in_progress
    -> commit

Provider subscription call
    -> no database transaction open

Transaction B
    -> persist active Subscription
    -> mark CREATE_SUBSCRIPTION succeeded
    -> commit
```

The application orchestrator owns these commits as an explicit exception to
the normal single-transaction service convention.

This is intentional because provider calls must never execute while a
database transaction remains open.

After every commit, the orchestrator reloads and locks durable records before
applying provider results.

## Client Idempotency Behavior

For subscription creation:

```text
same tenant + same operation type + same client key + same fingerprint
    -> resume or replay the same logical workflow
```

```text
same tenant + same operation type + same client key + different fingerprint
    -> 409 Conflict
```

```text
new client key + existing tenant subscription
    -> 409 Conflict
    -> provider is not called
```

The `CREATE_SUBSCRIPTION` command fingerprint includes the server-validated
`price_code`.

The `CREATE_CUSTOMER` command fingerprint contains no client-controlled
fields.

A successful replay reconstructs the HTTP response from the persisted
`Subscription` row rather than trusting only provider response JSON.

First successful execution returns:

```text
201 Created
```

A successful idempotent replay returns:

```text
200 OK
```

## ProviderOperation Statuses in the Synchronous Workflow

The implemented transitions are:

```text
pending -> in_progress
failed_retryable -> in_progress

in_progress -> succeeded
in_progress -> failed_retryable
in_progress -> failed_terminal
```

`in_progress` is committed before the provider call so competing requests can
observe that another execution currently owns the operation.

A succeeded operation is replayed without another provider call.

A failed terminal operation is not reclaimed.

A failed retryable operation may be claimed again by the same logical
workflow.

Stale `in_progress` reclamation is intentionally deferred to later reliability
and background-job work.

## Provider Result Persistence

Successful customer creation persists:

```json
{
  "provider_customer_id": "fake_cus_...",
  "provider_reference": "fake_op_..."
}
```

Successful subscription creation persists:

```json
{
  "provider_subscription_id": "fake_sub_...",
  "provider_state_version": 1,
  "current_period_start": "<UTC ISO-8601>",
  "current_period_end": "<UTC ISO-8601>",
  "provider_reference": "fake_op_..."
}
```

The provider reference is also stored in its dedicated operation column.

The payload does not contain:

- client secrets;
- provider credentials;
- bearer tokens;
- payment-card data;
- raw exception traces.

Public HTTP responses do not expose provider customer or subscription
identifiers.

## Provider Failure Handling

Retryable provider errors are persisted as:

```text
failed_retryable
```

The API returns:

```text
503 Service Unavailable
```

Ambiguous provider outcomes use the same local retryable status. A later
request retries with the same stable provider operation key, allowing the fake
provider to replay its already-stored successful result without duplicating
the mutation.

Terminal provider rejections are persisted as:

```text
failed_terminal
```

The API returns:

```text
409 Conflict
```

The same client key does not trigger another provider attempt after a terminal
outcome.

## Concurrency Guarantees

Subscription creation does not claim exactly-once execution.

The implementation combines:

- PostgreSQL unique constraints;
- row-level locks;
- committed `in_progress` ownership;
- provider-side idempotency;
- one subscription row per tenant;
- one fake provider subscription per fake customer.

For concurrent requests using the same client key, one execution owns the
provider operation. Another request may replay the completed result or receive
an operation-in-progress conflict.

For concurrent requests using different client keys, the local subscription
uniqueness constraint remains the final source of truth.

At-least-once provider attempts remain possible under competing distinct
keys. Provider idempotency and local uniqueness constrain the effect but do
not create an exactly-once guarantee.

## Planned API Boundary

Implemented tenant billing routes currently provide:

```text
create subscription
read subscription
schedule price change
schedule period-end cancellation
```

Future tenant billing routes are expected to provide additional lifecycle
mutations as later milestones land.

Mutation routes require an `Idempotency-Key`.

Read routes do not require an `Idempotency-Key`.

The authorization model is:

```text
OWNER
    -> read billing
    -> manage billing

ADMIN
    -> read billing

STAFF
    -> no billing administration permission
```

Immediate cancellation, undo cancellation, and reactivation API surfaces
remain deferred.

## Planned Background Processing Boundary

Future background jobs will execute durable billing work such as:

```text
execute provider operation
process billing webhook
reconcile subscription
```

The billing domain rules are intentionally independent of HTTP so the same
application services can later be invoked by API routes and workers.

The background-job milestone will define:

- durable job records;
- concurrent claiming;
- retries;
- exponential backoff;
- jitter;
- terminal failures;
- worker leases;
- correlation propagation.

The current billing foundation does not include a billing-specific queue,
worker, or task runner.

## Testing Strategy

The domain foundation is verified through pure unit tests.

Coverage includes:

- stable enum values;
- catalog contents;
- catalog lookup;
- immutable price definitions;
- integer minor-unit amounts;
- supported and invalid subscription transitions;
- provider state-version comparison;
- price-change scheduling;
- cancellation and pending-price interactions;
- period-boundary confirmation;
- immutable lifecycle state;
- timezone-aware datetime requirements;
- idempotency-key validation;
- stable idempotency validation reasons;
- deterministic command fingerprints;
- exact canonical fingerprint output;
- insertion-order independence;
- operation-type separation.

The provider foundation is verified through pure unit tests.

Coverage includes:

- immutable provider request and result contracts;
- timezone validation and UTC normalization;
- provider operation-key validation;
- exact provider request fingerprints;
- calendar anniversary calculation;
- month-end and leap-year behavior;
- deterministic provider identifiers;
- successful provider replay;
- one subscription per fake customer;
- provider state-version increments;
- retryable failures;
- terminal rejection replay;
- ambiguous success recovery;
- idempotency conflicts;
- operation-scoped failure scripting;
- thread-safe concurrent retries.

The persistence foundation is verified through PostgreSQL integration tests.

Coverage includes:

- model persistence;
- native enum defaults;
- JSONB payload persistence;
- named uniqueness constraints;
- named check constraints;
- nullable provider identifiers;
- one subscription lifecycle per tenant;
- outbound idempotency uniqueness;
- inbound event uniqueness;
- repository reads and row locks;
- known constraint translation;
- repository flush behavior without commits;
- concurrent uniqueness races;
- bounded row-lock contention;
- cleanup scoped to test-owned data;
- persisted BillingCustomer and Subscription assertions;
- persisted CREATE_CUSTOMER and CREATE_SUBSCRIPTION operations;
- successful idempotent replay;
- same-key fingerprint conflicts;
- new-key existing-subscription conflicts;
- ambiguous customer result recovery;
- terminal subscription rejection persistence;
- independent PostgreSQL sessions per concurrent thread;
- concurrent same-key requests;
- concurrent different-key requests;
- scoped cleanup by test-owned tenant IDs;
- persisted subscription API reads;
- exact public subscription fields;
- provider identifier exclusion;
- reads without an `Idempotency-Key`;
- tenant-scoped repository lookup;
- cross-tenant isolation;
- missing-subscription Problem Details;
- exact `billing_subscription_not_found` error code;
- PostgreSQL-backed HTTP integration coverage;
- persisted `pending_price_code`;
- active price and current period preservation;
- durable `CHANGE_PLAN` success;
- successful same-key replay;
- same-key fingerprint conflict;
- new-key pending-target conflict;
- inactive subscription rejection;
- cancellation-pending rejection;
- ambiguous provider recovery;
- terminal provider failure replay;
- PostgreSQL-backed workflow integration;
- HTTP integration;
- tenant-scoped mutation;
- independent database sessions per concurrent thread;
- concurrent same-key requests;
- concurrent different-key requests;
- scoped cleanup by test-owned tenant IDs;
- persisted `cancel_at_period_end`;
- persisted `cancellation_requested_at`;
- preserved `status = active`;
- preserved `canceled_at = null`;
- active price and current period preservation;
- pending plan-change cleanup;
- durable `CANCEL_SUBSCRIPTION` success;
- successful same-key replay;
- changed-period fingerprint conflict;
- new-key pending-cancellation conflict;
- inactive subscription rejection;
- already-canceled subscription rejection;
- ambiguous provider recovery;
- terminal provider failure replay;
- PostgreSQL-backed workflow integration;
- HTTP integration;
- tenant-scoped mutation;
- independent database sessions per concurrent thread;
- concurrent same-key requests;
- concurrent different-key requests;
- scoped cleanup by test-owned tenant IDs;
- canonical renewed and canceled event validation;
- lifecycle consistency validation;
- timezone validation;
- exact raw-byte HMAC verification;
- tampered body rejection;
- malformed signature rejection;
- stale and future timestamp rejection;
- tolerance-boundary acceptance;
- payload-size rejection;
- webhook configuration validation;
- deployed-environment default-secret rejection;
- authenticated event persistence;
- unknown local subscription acceptance;
- identical duplicate replay;
- conflicting duplicate rejection;
- concurrent identical delivery;
- concurrent conflicting delivery;
- independent sessions per concurrent thread;
- HTTP receipt after commit;
- no Bearer requirement;
- signature rejection before JSON parsing;
- valid signature with malformed JSON;
- no persistence after failed authentication or schema validation;
- scoped cleanup by provider event ID.

Migration parity is checked through:

```text
alembic upgrade head
alembic check
```

Real-provider execution and background-worker tests are added only when those
implementation boundaries exist.

## Invariants

The domain foundation establishes these invariants:

1. Billing prices are selected through stable server-owned price codes.

2. Monetary values use integer minor units.

3. Price definitions and catalog results are immutable.

4. A canceled subscription cannot return to an active or past-due state.

5. Price changes do not replace the confirmed price before the period
   boundary.

6. A subscription cannot have more than one pending price change in the pure
   lifecycle state.

7. Scheduling cancellation clears a pending price change.

8. A price change cannot be scheduled after period-end cancellation has been
   requested.

9. Period-end transitions cannot be confirmed before the current period ends.

10. Lifecycle timestamps must be timezone-aware.

11. Older provider state versions are stale.

12. Client idempotency keys are opaque and cannot be interpreted as business
    state.

13. Command fingerprints are deterministic and include the operation type.

14. Secrets and caller-supplied monetary values do not enter fingerprint
    payloads.

15. Pure billing rules do not perform database, provider, HTTP, or worker
    operations.

16. A tenant has at most one billing customer for each provider.

17. A non-null provider customer identifier is unique within its provider.

18. A tenant has at most one subscription lifecycle in V1.

19. A non-null provider subscription identifier is unique within its
    provider.

20. A provider operation idempotency scope is unique by tenant, operation
    type, and client idempotency key.

21. A webhook event identity is unique by provider and provider event ID.

22. Provider operation and webhook payloads are stored as structured JSONB
    without secrets.

23. Repositories flush but never commit or roll back.

24. Persistence-level concurrency relies on row locks and database
    constraints rather than process-local synchronization.

25. Provider operation keys are derived from persisted provider operation UUIDs
    using the stable `clinicops:<uuid>` format.

26. Provider request fingerprints exclude the provider operation key and
    deterministically represent the requested mutation.

27. Provider request datetimes are timezone-aware and normalized to UTC.

28. Monthly and yearly billing periods use calendar arithmetic rather than
    fixed-day approximations.

29. The same provider key and request replay the same provider outcome.

30. The same provider key with a different request is rejected.

31. Retryable fake-provider failures do not mutate provider state or reserve
    an operation outcome.

32. Terminal fake-provider rejections are replayable outcomes for the same
    key and request.

33. Ambiguous fake-provider success persists the successful result before
    reporting uncertainty.

34. Retrying an ambiguous success does not duplicate the mutation or increment
    provider state again.

35. Fake-provider in-memory mutations and idempotency decisions are
    thread-safe.

36. Billing subscription creation requires an authorized tenant context with
    `BILLING_MANAGE`.

37. Every billing subscription mutation requires a validated client
    `Idempotency-Key`.

38. Provider customer creation is represented by a durable
    `CREATE_CUSTOMER` operation.

39. Provider subscription creation is represented by a durable
    `CREATE_SUBSCRIPTION` operation.

40. Provider calls occur only after the transaction that claims the operation
    has committed.

41. Provider results are applied only after reloading the operation under a
    database lock.

42. The same successful client request replays the persisted subscription
    without another provider call.

43. A client key reused with a different command fingerprint is rejected.

44. A new client key cannot create a second subscription lifecycle for the
    same tenant.

45. Provider identifiers are not exposed by the public subscription response.

46. The synchronous workflow does not reclaim stale `in_progress` operations.

47. The workflow provides at-least-once attempts with idempotent boundaries,
    not exactly-once execution.

48. Billing subscription reads require an authorized tenant context with
    `BILLING_READ`.

49. The subscription read endpoint is tenant-scoped by the path tenant ID.

50. The persisted `Subscription` row is the read source of truth.

51. Subscription reads do not call the payment provider.

52. Subscription reads do not acquire row locks or mutate persistence.

53. Provider identifiers are excluded from the public billing response.

54. A tenant without a subscription receives an explicit not-found error.

55. Billing reads do not require an idempotency key.

56. Scheduled plan changes require `BILLING_MANAGE`.

57. Every plan-change request requires a validated client
    `Idempotency-Key`.

58. Every provider-side plan change is represented by a durable
    `CHANGE_PLAN` operation.

59. The active subscription price is not modified when a future plan change
    is scheduled.

60. The confirmed target is persisted in `pending_price_code`.

61. A subscription may have at most one pending price change.

62. A plan change cannot target the currently active price.

63. A plan change cannot be scheduled for an inactive subscription.

64. A plan change cannot be scheduled while cancellation is pending.

65. Provider calls occur only after the durable operation claim commits.

66. The provider result must advance `provider_state_version`.

67. A successful replay does not call the provider again.

68. A client key reused for another target is rejected.

69. A new client key cannot replace an existing pending target.

70. The workflow provides at-least-once attempts, not exactly-once execution.

71. Scheduled cancellation requires `BILLING_MANAGE`.

72. Every cancellation request requires a validated client
    `Idempotency-Key`.

73. Every provider-side cancellation is represented by a durable
    `CANCEL_SUBSCRIPTION` operation.

74. The cancellation effective date is derived from the persisted
    `current_period_end`.

75. The client cannot supply provider identities or an effective date.

76. Scheduling cancellation preserves `status = active`.

77. Scheduling cancellation preserves `canceled_at = null`.

78. Successful cancellation sets `cancel_at_period_end = true`.

79. Successful cancellation sets `cancellation_requested_at` from the
    application clock.

80. Successful cancellation clears `pending_price_code`.

81. A pending plan change does not block cancellation.

82. An inactive or already-canceled subscription cannot be scheduled for
    cancellation.

83. A new idempotency key cannot replace an existing pending cancellation.

84. Provider calls occur only after the durable operation claim commits.

85. The provider result must confirm the persisted period boundary.

86. The provider result must advance `provider_state_version`.

87. A successful replay does not call the provider again.

88. A client key reused after the effective boundary changes is rejected.

89. The workflow provides at-least-once attempts, not exactly-once execution.

90. Webhook authentication covers the exact raw request bytes.

91. JSON parsing occurs only after signature verification succeeds.

92. Signature comparison uses constant-time comparison.

93. Signature timestamps must fall within the configured tolerance.

94. Payloads larger than the configured limit are rejected before ingestion.

95. A successful provider acknowledgement occurs only after database commit.

96. Billing webhook ingestion does not require Bearer authentication.

97. Billing webhook ingestion does not depend on tenant membership.

98. Provider event uniqueness is scoped by provider and provider event ID.

99. Duplicate equality is determined by the SHA-256 of exact raw bytes.

100. An identical duplicate cannot create another event row.

101. Reusing an event ID with different raw bytes is a conflict.

102. A newly ingested event starts in `received`.

103. A newly ingested event has zero processing attempts.

104. Ingestion does not mutate subscription state.

105. A missing local subscription does not block durable event receipt.

106. Webhook secrets, full signatures, and raw payloads are not exposed.

107. Concurrent deduplication relies on the database uniqueness constraint.

108. Webhook processing remains a separate lifecycle boundary.

## Intentionally Deferred

The following capabilities are intentionally deferred:

- subscription history;
- invoice history;
- payment history;
- immediate plan changes;
- proration;
- credits;
- refunds;
- invoice generation;
- pending-plan replacement;
- pending-plan cancellation as a separate workflow;
- automatic renewal application;
- final cancellation application at period end;
- undo cancellation;
- webhook event processing;
- subscription mutation from events;
- event ordering and stale-version handling;
- background processing;
- automatic retries;
- stale `processing` recovery;
- dead-letter handling;
- provider reconciliation;
- secret rotation;
- multiple active signing secrets;
- real provider-specific adapters;
- entitlement updates;
- rate limiting;
- edge firewall configuration;
- stale `in_progress` recovery;
- entitlement revocation;
- background jobs;
- live provider lookup during billing reads;
- background provider execution;
- payment credentials;
- immediate cancellation;
- actual monetary processing;
- payment-method collection;
- hosted checkout;
- credit-card processing;
- taxes;
- coupons;
- trials;
- partial-period credits;
- usage-based pricing;
- seat-based pricing;
- semiannual billing;
- multiple subscriptions per tenant;
- multiple currencies;
- reactivation;
- production notification delivery.

These capabilities are not required to establish the current domain,
persistence, fake-provider, subscription-creation, subscription-read,
scheduled plan-change, scheduled cancellation, and webhook-ingestion workflow
foundations. They will be added only when their data ownership, transaction,
security, failure, and testing boundaries are implemented explicitly.
