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
- PostgreSQL integration and concurrency coverage.

This document distinguishes the implemented domain, persistence,
fake-provider, and subscription-creation workflow foundations from the
webhook ingestion, entitlement, worker, plan-change, cancellation, and
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
- `BILLING_MANAGE` authorization for billing mutations;
- required client `Idempotency-Key` extraction and validation at the HTTP
  boundary;
- durable `CREATE_CUSTOMER` and `CREATE_SUBSCRIPTION` reservation and claim
  orchestration.

The current billing implementation does not own:

- plan-change application services and API;
- subscription-cancellation application services and API;
- real payment-provider integrations;
- stale in-progress recovery;
- provider retry scheduling;
- background provider execution;
- webhook HMAC verification;
- webhook HTTP ingestion;
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

Webhook signature verification, event persistence, event status enums, and
processing services are intentionally deferred.

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
├── provider_created_at
├── provider_state_version
├── payload
├── status
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

The JSONB payload stores the verified provider event body after future
signature verification. Signature headers and secrets are never persisted.

The persistence schema exists now. HMAC verification, HTTP ingestion, event
normalization, and business processing remain intentionally deferred.

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
```

Future tenant billing routes are expected to provide:

```text
read subscription
schedule price change
schedule period-end cancellation
```

Mutation routes require an `Idempotency-Key`.

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

Plan-change and cancellation API surfaces remain deferred.

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
- scoped cleanup by test-owned tenant IDs.

Migration parity is checked through:

```text
alembic upgrade head
alembic check
```

Real-provider execution, webhook ingestion, plan-change, cancellation, and
background-worker tests are added only when those implementation boundaries
exist.

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

## Intentionally Deferred

The following capabilities are intentionally deferred:

- plan-change application service and API;
- subscription cancellation application service and API;
- stale in-progress recovery;
- scheduled retries;
- background provider execution;
- webhook ingestion;
- webhook HMAC verification;
- webhook business processing;
- entitlement enforcement;
- real provider adapters;
- payment credentials;
- refunds;
- proration;
- immediate cancellation;
- actual monetary processing;
- payment-method collection;
- hosted checkout;
- credit-card processing;
- invoices;
- taxes;
- coupons;
- trials;
- partial-period credits;
- usage-based pricing;
- seat-based pricing;
- semiannual billing;
- multiple subscriptions per tenant;
- multiple currencies;
- subscription reactivation;
- reconciliation jobs;
- production notification delivery.

These capabilities are not required to establish the current domain,
persistence, fake-provider, and subscription-creation workflow foundations.
They will be added only when their data ownership, transaction, security,
failure, and testing boundaries are implemented explicitly.
