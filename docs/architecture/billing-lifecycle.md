# Billing Lifecycle

## Purpose

The ClinicOps billing module defines the commercial catalog and the pure
subscription lifecycle rules used by future billing application workflows.

The current foundation establishes:

- stable plan, billing interval, provider, operation, and subscription status
  identifiers;
- immutable server-owned price definitions;
- subscription lifecycle transition rules;
- period-end cancellation behavior;
- period-boundary price changes;
- provider state-version staleness detection;
- opaque client idempotency-key validation;
- deterministic billing command fingerprints.

This document distinguishes the implemented domain foundation from the
persistence, provider, API, webhook, and worker capabilities planned for
later milestones.

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
- billing-domain exceptions.

The current billing foundation does not own:

- SQLAlchemy billing models;
- database migrations;
- tenant subscription persistence;
- outbound provider execution;
- provider-side idempotency storage;
- webhook signature verification;
- webhook event persistence;
- FastAPI billing routes;
- entitlement enforcement;
- background-job execution;
- audit-log persistence.

Those capabilities are intentionally introduced in later milestones after
their boundaries and failure modes are implemented explicitly.

## Billing Ownership

Billing belongs to a tenant.

A subscription is not owned by:

- a user;
- a membership;
- the current tenant owner.

Tenant ownership determines who may eventually manage billing, but changing
the tenant owner does not transfer, replace, or recreate the tenant
subscription.

The persistence and authorization boundaries for this rule are planned for
later milestones.

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

API clients will eventually select a stable `price_code`. They will not
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

Future billing mutation routes will require a client-generated
`Idempotency-Key`.

The current domain foundation defines its reusable validation contract.

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

The HTTP boundary that extracts and requires the header is intentionally
deferred to the billing API milestone.

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

## Future Outbound Idempotency Boundary

A later persistence milestone will introduce a durable provider operation.

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

The future application workflow will evaluate:

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

The current foundation validates keys and computes fingerprints but does not
persist or compare operations.

Provider-side idempotency is also deferred until the payment-provider
boundary exists.

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

The current implementation compares supplied period boundaries but does not
calculate them.

Monthly and yearly period calculation will be introduced when the provider
and application workflows define:

- subscription activation timestamps;
- provider-confirmed billing periods;
- anniversary behavior;
- yearly rollover behavior;
- retry and reconciliation semantics.

No new calendar library is introduced in the domain foundation.

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

HTTP Problem Details mappings will be added when billing API routes exist.

## Dependency Boundaries

The implemented domain foundation has no dependency on:

```text
FastAPI
SQLAlchemy
Alembic
PostgreSQL sessions
payment-provider clients
webhook requests
background workers
```

The conceptual dependency flow is:

```text
billing catalog
billing enums
billing lifecycle rules
billing idempotency validation
billing command fingerprints
    -> core application exceptions
```

Future infrastructure will depend on these rules. The pure domain foundation
must not depend on future infrastructure.

## Planned Persistence Boundary

A later milestone will introduce:

```text
BillingCustomer
Subscription
ProviderOperation
BillingWebhookEvent
```

That milestone will define:

- tenant ownership constraints;
- one current subscription lifecycle per tenant;
- unique provider references;
- outbound idempotency uniqueness;
- webhook event uniqueness;
- SQLAlchemy repositories;
- Alembic migrations;
- PostgreSQL concurrency tests.

The present document does not claim those persistence capabilities are
implemented.

## Planned Provider Boundary

A later milestone will introduce a payment-provider port and a deterministic
fake provider.

The fake provider is planned to support controlled scenarios such as:

```text
success
temporary failure
terminal rejection
timeout after provider-side success
repeated provider idempotency key
```

The fake provider is an intentional reliability-testing boundary. It avoids
real credentials, monetary cost, and uncontrolled external dependencies while
preserving the failure modes required for production-minded billing design.

## Planned API Boundary

Future tenant billing routes are expected to provide:

```text
create subscription
read subscription
schedule price change
schedule period-end cancellation
```

Mutation routes will require an `Idempotency-Key`.

The future authorization model is planned as:

```text
OWNER
    -> read billing
    -> manage billing

ADMIN
    -> read billing

STAFF
    -> no billing administration permission
```

These API and authorization capabilities are not part of the current domain
foundation.

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

The current foundation is verified through pure unit tests.

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

Database, HTTP, provider, webhook, and concurrency tests are added only when
their corresponding implementation boundaries exist.

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

## Intentionally Deferred

The following capabilities are intentionally deferred:

- SQLAlchemy billing models;
- billing migrations;
- subscription repositories;
- provider-operation persistence;
- API-level idempotent response replay;
- real payment providers;
- payment-method collection;
- hosted checkout;
- credit-card processing;
- invoices;
- taxes;
- refunds;
- coupons;
- trials;
- proration;
- partial-period credits;
- usage-based pricing;
- seat-based pricing;
- semiannual billing;
- multiple subscriptions per tenant;
- multiple currencies;
- immediate cancellation;
- subscription reactivation;
- entitlement enforcement;
- webhook ingestion;
- webhook HMAC verification;
- webhook persistence;
- reconciliation jobs;
- worker infrastructure;
- production notification delivery.

These capabilities are not required to establish the current domain
foundation. They will be added only when their data ownership, transaction,
security, failure, and testing boundaries are implemented explicitly.
