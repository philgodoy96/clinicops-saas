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
- production provider adapters;
- webhook processing authorization;
- dead-letter and retry controls.
