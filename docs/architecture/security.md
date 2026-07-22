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

ClinicOps returns `202 Accepted` only after the webhook event row commits.

Security and reliability boundary:

```text
valid signature
    -> valid canonical event
    -> durable database commit
    -> acknowledgement
```

A provider must not receive a successful acknowledgement for an event that was
not stored durably.

An identical duplicate receives the same public acknowledgement without
creating another row.

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

Stale claim recovery is intentionally delegated to future operational job
infrastructure.

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

The following operational controls build on the implemented internal
processing and transaction boundaries but are not yet part of the current
processing boundary:

- authenticated operational replay tooling;
- operator RBAC for manual processing;
- stale claim recovery;
- retry scheduling;
- maximum retry attempts;
- dead-letter workflows;
- provider reconciliation;
- provider API state verification;
- structured security event alerts;
- multi-provider processing policies;
- entitlement propagation;
- production worker isolation.
