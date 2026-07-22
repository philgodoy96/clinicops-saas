# HTTP API Transport Model

## Purpose

ClinicOps exposes its application capabilities through a versioned FastAPI
transport boundary.

The HTTP layer is responsible for:

- validating and serializing transport contracts;
- resolving request-scoped dependencies;
- translating HTTP input into application commands;
- translating application results into public responses;
- defining transaction completion at the request boundary;
- mapping expected and unexpected failures into a stable error contract;
- propagating request and correlation identifiers;
- declaring authentication requirements through OpenAPI.

The HTTP layer does not own domain rules, persistence rules, password
verification, token rotation, session-state transitions, tenant authorization,
or business invariants.

## API versioning

The current public API is mounted under:

```text
/api/v1
```

Versioning is applied at the router-composition boundary rather than repeated
inside individual route modules.

Current system and authentication routes include:

```text
GET  /api/v1/health/live
GET  /api/v1/health/ready

POST /api/v1/auth/login
POST /api/v1/auth/refresh
GET  /api/v1/auth/me
POST /api/v1/auth/logout
```

The implemented billing HTTP surface distinguishes two API surfaces:

```text
Tenant-scoped billing API:
    GET  /api/v1/tenants/{tenant_id}/billing/subscription
    POST /api/v1/tenants/{tenant_id}/billing/subscription
    POST /api/v1/tenants/{tenant_id}/billing/subscription/plan-change
    POST /api/v1/tenants/{tenant_id}/billing/subscription/cancellation

Provider-facing billing API:
    POST /api/v1/billing/webhooks/{provider}
```

The tenant-scoped API uses Bearer authentication and permissions.

The provider-facing webhook API uses HMAC authentication.

Supported webhook provider:

```text
fake
```

```text
GET subscription
    -> BILLING_READ
    -> no Idempotency-Key
    -> 200

POST subscription
    -> BILLING_MANAGE
    -> Idempotency-Key required
    -> 201 first success
    -> 200 successful replay

POST plan-change
    -> BILLING_MANAGE
    -> Idempotency-Key required
    -> request body required
    -> 200 first success
    -> 200 successful replay

POST cancellation
    -> BILLING_MANAGE
    -> Idempotency-Key required
    -> no request body
    -> 200 first success
    -> 200 successful replay
```

A new API version can be introduced through a separate router without changing
the internal application services.

## Application composition

`clinicops.main` is the application composition root.

It is responsible for:

- resolving validated application settings;
- configuring logging;
- creating the FastAPI application;
- storing settings on `application.state`;
- storing the application-scoped fake payment provider on `application.state`;
- registering exception handlers;
- registering request-context middleware;
- mounting the root API router.

The application-scoped fake provider is resolved through request application
state. A new provider instance must not be created per request because
provider-side idempotency and ambiguous-outcome recovery depend on state
surviving across requests within the process.

The API package provides transport components, but it does not create a second
application instance or application factory.

## Transport schemas

HTTP request and response contracts are defined with Pydantic and remain
separate from application commands and application results.

This separation allows the API to evolve its public representation without
forcing transport concerns into application services.

For example:

```text
LoginRequest
    -> AuthenticateUserCommand
    -> AuthenticateUserService
    -> AuthenticatedSession
    -> TokenPairResponse
```

Authentication transport models reject unknown fields.

This prevents caller-controlled fields such as tenant identifiers, roles, or
session identifiers from being silently accepted by global authentication
operations.

Sensitive values such as passwords, access tokens, refresh tokens, and signing
keys are excluded from object representations where applicable.

They remain serializable only when the public HTTP contract intentionally
requires them.

## Authentication model

ClinicOps uses short-lived bearer access tokens and opaque refresh tokens.

Protected requests send the access token through:

```http
Authorization: Bearer <access-token>
```

The bearer extraction dependency uses `HTTPBearer(auto_error=False)` so missing
or malformed credentials are rendered through the ClinicOps Problem Details
contract instead of a framework-default response.

Bearer extraction alone does not establish a trusted principal.

The request flow is:

```text
Authorization header
    -> bearer token extraction
    -> ResolveAuthenticatedPrincipalCommand
    -> access-token validation
    -> persisted user and session validation
    -> AuthenticatedPrincipal
```

Principal resolution validates both cryptographic claims and current persisted
state.

A structurally valid token is rejected when:

- the access token is expired;
- the token signature, issuer, audience, type, or claims are invalid;
- the authentication session does not exist;
- the session is revoked or compromised;
- the persisted session is expired;
- the user is disabled.

Access tokens contain global identity and session claims only.

Tenant identifiers, membership identifiers, roles, and permissions are not
embedded in the token. Tenant authorization is resolved separately from current
persisted membership state.

## Authentication endpoints

### Login

```http
POST /api/v1/auth/login
```

The login route validates the HTTP payload and translates it into an
`AuthenticateUserCommand`.

The application service owns:

- canonical email handling;
- password verification;
- dummy password verification for unknown identities;
- active-user validation;
- authentication-session creation;
- refresh-token creation;
- access-token issuance;
- persistence flushing.

The route owns the final transaction commit.

Credentials are returned only after the commit succeeds.

Invalid email, invalid password, missing password credentials, and disabled
users share the same public authentication failure contract. This reduces the
risk of account enumeration.

### Refresh

```http
POST /api/v1/auth/refresh
```

The refresh route accepts an opaque refresh token and delegates rotation to the
application service.

Successful rotation:

```text
validate current refresh token
    -> consume current token
    -> create active replacement
    -> issue new access token
    -> flush
    -> commit
    -> return replacement credentials
```

The authentication session keeps its original absolute expiration. Refresh
rotation does not extend the session lifetime.

Refresh-token reuse is treated as a security transition:

```text
detect consumed-token reuse
    -> mark authentication session compromised
    -> revoke the active replacement token
    -> flush
    -> commit
    -> return a generic unauthorized response
```

The commit occurs before the public failure is returned. This preserves the
security transition instead of rolling it back with the request error.

### Current principal

```http
GET /api/v1/auth/me
```

This protected route returns the trusted global principal established for the
current request.

The response includes:

- user ID;
- authentication session ID;
- authentication timestamp;
- access-token expiration;
- session expiration.

It intentionally excludes:

- access-token ID;
- access token;
- refresh token;
- tenant ID;
- membership ID;
- role;
- permissions;
- internal session status.

The route is read-only and does not commit a transaction.

### Logout

```http
POST /api/v1/auth/logout
```

Logout revokes the session represented by the current authenticated principal.

The client does not provide a session ID or refresh token in the request body.

The route builds the revocation command from:

```text
AuthenticatedPrincipal.user_id
AuthenticatedPrincipal.session_id
```

The application service revokes the active session and its active refresh token
inside the caller's transaction.

The route commits the transition and returns:

```http
204 No Content
```

Previously issued access tokens are subsequently rejected because protected
requests validate the persisted session state.

## Billing subscription read

```http
GET /api/v1/tenants/{tenant_id}/billing/subscription
Authorization: Bearer <access-token>
```

The request has:

- no body;
- no query parameters;
- no `Idempotency-Key`;
- no provider identifier;
- no client-controlled tenant ID outside the path.

Successful response:

```text
200 OK
application/json
```

Example:

```json
{
  "id": "subscription-uuid",
  "tenant_id": "tenant-uuid",
  "price_code": "starter_monthly",
  "plan": "starter",
  "billing_interval": "monthly",
  "currency": "USD",
  "unit_amount": 4900,
  "status": "active",
  "current_period_start": "2026-07-22T12:00:00Z",
  "current_period_end": "2026-08-22T12:00:00Z",
  "cancel_at_period_end": false,
  "cancellation_requested_at": null,
  "canceled_at": null,
  "pending_price_code": null,
  "created_at": "2026-07-22T12:00:00Z",
  "updated_at": "2026-07-22T12:00:00Z"
}
```

The local `Subscription` row is the read source of truth.

The route does not perform a live provider lookup.

When authorization succeeds but no persisted subscription exists for the
tenant:

```text
404 Not Found
application/problem+json
```

Stable code:

```text
billing_subscription_not_found
```

Tenant and membership authorization errors remain governed by the existing
tenant-context behavior.

## Billing Subscription Read Transport

The GET route is a read-only transport boundary.

It:

- resolves the authenticated principal;
- resolves the tenant context from `{tenant_id}`;
- requires `BILLING_READ`;
- invokes `GetBillingSubscriptionService`;
- maps `BillingSubscriptionDetails` to
  `BillingSubscriptionResponse`;
- returns the persisted local subscription state.

It does not:

- commit or roll back a database transaction;
- acquire a row lock;
- call the payment provider;
- require an idempotency key;
- expose provider identifiers;
- return provider operation payloads.

## Billing subscription creation

```http
POST /api/v1/tenants/{tenant_id}/billing/subscription
```

Request body:

```json
{
  "price_code": "starter_monthly"
}
```

Required header:

```text
Idempotency-Key: <opaque client-generated key>
```

Successful response status codes:

```text
201 Created
    -> first successful execution

200 OK
    -> successful idempotent replay
```

The response does not expose provider customer IDs, provider subscription IDs,
provider operation keys, or provider request payloads.

## Billing scheduled plan change

```http
POST /api/v1/tenants/{tenant_id}/billing/subscription/plan-change
Authorization: Bearer <access-token>
Idempotency-Key: <opaque client-generated key>
Content-Type: application/json
```

Request body:

```json
{
  "price_code": "professional_monthly"
}
```

The request does not accept:

- tenant ID in the body;
- provider subscription ID;
- provider operation key;
- effective date;
- currency;
- amount;
- billing interval;
- provider state version.

Successful response:

```text
200 OK
application/json
```

Example:

```json
{
  "id": "subscription-uuid",
  "tenant_id": "tenant-uuid",
  "price_code": "starter_monthly",
  "plan": "starter",
  "billing_interval": "monthly",
  "currency": "USD",
  "unit_amount": 4900,
  "status": "active",
  "current_period_start": "2026-07-22T12:00:00Z",
  "current_period_end": "2026-08-22T12:00:00Z",
  "cancel_at_period_end": false,
  "cancellation_requested_at": null,
  "canceled_at": null,
  "pending_price_code": "professional_monthly",
  "created_at": "2026-07-22T12:00:00Z",
  "updated_at": "2026-07-22T13:00:00Z"
}
```

The active plan fields remain unchanged.

The scheduled future target appears only in:

```text
pending_price_code
```

Both the first successful execution and a successful idempotent replay return:

```text
200 OK
```

The internal `replayed` flag is not part of the public response.

Success and replay semantics:

```text
first successful execution
    -> 200 OK

same key + same target after success
    -> 200 OK
    -> persisted subscription replay
    -> no provider call

same key + another target
    -> 409 Conflict

new key + existing pending target
    -> 409 Conflict
```

## Scheduled Plan-Change Transport

The route:

- resolves the authenticated principal;
- resolves tenant context from `{tenant_id}`;
- requires `BILLING_MANAGE`;
- validates `Idempotency-Key`;
- validates the request schema;
- creates `ScheduleBillingPlanChangeCommand`;
- invokes `ScheduleBillingPlanChangeService`;
- maps the result to `BillingSubscriptionResponse`.

The route does not:

- derive provider operation keys;
- accept provider identifiers;
- choose the effective date;
- apply the target as the current price;
- own provider-specific behavior;
- expose the replay flag.

The application orchestrator owns the multi-transaction workflow.

The plan-change provider call occurs without an open database transaction.

The route remains thin and does not own commit boundaries.

## Billing subscription cancellation

```http
POST /api/v1/tenants/{tenant_id}/billing/subscription/cancellation
Authorization: Bearer <access-token>
Idempotency-Key: <opaque client-generated key>
```

The request has no JSON body.

The client does not provide:

- tenant ID in a body;
- provider subscription ID;
- provider operation key;
- effective date;
- cancellation timestamp;
- subscription status;
- provider state version.

Successful response:

```text
200 OK
application/json
```

Example:

```json
{
  "id": "subscription-uuid",
  "tenant_id": "tenant-uuid",
  "price_code": "starter_monthly",
  "plan": "starter",
  "billing_interval": "monthly",
  "currency": "USD",
  "unit_amount": 4900,
  "status": "active",
  "current_period_start": "2026-07-22T12:00:00Z",
  "current_period_end": "2026-08-22T12:00:00Z",
  "cancel_at_period_end": true,
  "cancellation_requested_at": "2026-07-24T15:00:00Z",
  "canceled_at": null,
  "pending_price_code": null,
  "created_at": "2026-07-22T12:00:00Z",
  "updated_at": "2026-07-24T15:00:00Z"
}
```

The active subscription remains active until the current period ends.

Cancellation success clears any public `pending_price_code` value while
preserving the active `price_code`.

Both the first successful execution and a successful replay return:

```text
200 OK
```

The internal `replayed` flag is not part of the public response.

Success and replay semantics:

```text
first successful execution
    -> 200 OK

same key + same persisted period boundary after success
    -> 200 OK
    -> persisted subscription replay
    -> no provider call

same key + changed persisted period boundary
    -> 409 Conflict

new key + existing pending cancellation
    -> 409 Conflict
```

## Scheduled Cancellation Transport

The route:

- resolves the authenticated principal;
- resolves tenant context from `{tenant_id}`;
- requires `BILLING_MANAGE`;
- validates `Idempotency-Key`;
- creates `ScheduleBillingSubscriptionCancellationCommand`;
- invokes `ScheduleBillingSubscriptionCancellationService`;
- maps the result to `BillingSubscriptionResponse`.

The route does not:

- accept a request body;
- derive provider operation keys;
- accept provider identifiers;
- select the effective date;
- mark the subscription as finally canceled;
- own provider-specific behavior;
- expose the replay flag.

The application orchestrator owns the multi-transaction workflow.

The cancellation provider call occurs without an open database transaction.

The route remains thin and does not own commit boundaries.

## Billing webhook ingestion

```http
POST /api/v1/billing/webhooks/fake
Content-Type: application/json
X-Billing-Signature: t=<unix_timestamp>,v1=<hmac_sha256_digest>
X-Correlation-ID: <optional-correlation-id>
```

The route does not require:

```text
Authorization: Bearer ...
Idempotency-Key: ...
```

## Billing Webhook Ingestion Transport

The route:

- reads the exact request body bytes;
- resolves the provider from the path;
- authenticates the request with HMAC-SHA256;
- validates signature freshness;
- enforces the configured payload limit;
- forwards the authenticated bytes to the ingestion service;
- commits the event receipt;
- returns `202 Accepted`.

The route does not:

- parse JSON before signature verification;
- require a user identity;
- resolve tenant context;
- mutate subscription state;
- process the event;
- expose duplicate status.

### Webhook signature contract

```text
Header:
    X-Billing-Signature

Value:
    t=<unix_timestamp>,v1=<64-character lowercase hexadecimal digest>

Signed message:
    <timestamp>.<exact raw request bytes>

Algorithm:
    HMAC-SHA256
```

The timestamp and body are both authenticated.

The default accepted clock difference is:

```text
300 seconds
```

### Successful webhook response

```text
202 Accepted
application/json
```

```json
{
  "received": true
}
```

This response means:

```text
the authenticated event has been committed durably
```

It does not mean:

```text
the event has been processed
the subscription has been changed
the provider state has been applied
```

Both new and identical duplicate events receive the same response.

### Webhook request ordering

```text
payload-size check
    -> provider signature authentication
    -> canonical JSON validation
    -> durable event reservation or replay
    -> commit
    -> 202
```

Examples:

```text
malformed JSON + invalid signature
    -> 401
    -> signature rejection happens first

malformed JSON + valid signature
    -> 400
    -> canonical payload validation fails
```

### Webhook duplicate behavior

```text
same provider + same event ID + same raw bytes
    -> 202
    -> one persisted event

same provider + same event ID + different raw bytes
    -> 409
```

The response does not include:

- `duplicate`;
- local webhook event ID;
- processing status;
- payload hash;
- signature timestamp.

## Transaction ownership

Application services flush database changes but do not commit.

The HTTP route owns the final transaction outcome for write operations.

This rule makes transaction boundaries explicit:

```text
Application service
    -> validate invariants
    -> apply state transition
    -> flush

HTTP route
    -> commit successful write
    -> return public response
```

The request-scoped SQLAlchemy dependency:

- opens one session;
- yields it to the dependency graph;
- rolls back any unfinished transaction during cleanup;
- closes the session;
- never commits automatically.

This design prevents a generic infrastructure dependency from committing
partial or unintended work.

Read-only requests may open an implicit database transaction. Cleanup rolls that
transaction back before closing the session.

## Multi-Transaction Application Workflows

Most existing write routes call a flush-only application service and then own
one final `session.commit()`.

Billing subscription creation, scheduled plan change, and scheduled
cancellation are intentional exceptions.

Their application orchestrators own multiple short commits because the
workflows must guarantee:

```text
database reservation committed
    -> provider call with no open database transaction
    -> provider result applied in a later transaction
```

The route remains thin. It validates transport concerns, resolves
authorization and dependencies, invokes the orchestrator, and maps the result
to the public response.

For scheduled plan change and scheduled cancellation, the provider call occurs
without an open database transaction. The route does not own commit
boundaries.

This exception does not change the default rule for ordinary domain services.

## Billing Idempotency Header

`Idempotency-Key` is required for billing mutation requests.

The header value:

- is client generated;
- is opaque;
- is trimmed;
- must not be empty;
- is limited to 255 characters;
- is case-sensitive;
- is separate from the internal provider operation key.

Missing or invalid headers return:

```text
400 Bad Request
application/problem+json
```

Reusing a key for a different billing command returns:

```text
409 Conflict
application/problem+json
```

## Problem Details

ClinicOps exposes errors using an RFC 9457-compatible Problem Details contract.

Media type:

```text
application/problem+json
```

Example:

```json
{
  "type": "urn:clinicops:problem:invalid_credentials",
  "title": "Authentication failed",
  "status": 401,
  "detail": "The email or password is invalid.",
  "code": "invalid_credentials",
  "request_id": "9dbef48a-4cea-4eb3-8a2d-d63f98840fc4",
  "correlation_id": "9dbef48a-4cea-4eb3-8a2d-d63f98840fc4"
}
```

The standard fields are:

| Field | Purpose |
|---|---|
| `type` | Stable ClinicOps problem URI |
| `title` | Human-readable failure category |
| `status` | HTTP status code |
| `detail` | Safe public explanation |
| `code` | Stable machine-readable application code |
| `request_id` | Identifier for the individual request |
| `correlation_id` | Identifier shared across related operations |

Validation failures may also include a sanitized `errors` collection containing:

- location;
- message;
- validation type.

Raw request input and internal validation context are not exposed.

Unexpected failures return a generic `500` response. Internal exception details
and stack traces are logged but are not included in the client response.

Authentication failures return:

```http
401 Unauthorized
WWW-Authenticate: Bearer
```

Framework headers such as `Allow` for `405 Method Not Allowed` are preserved.

Billing subscription-creation failures map to:

```text
400
    missing idempotency key
    invalid idempotency key
    unsupported price code

409
    subscription already exists
    billing idempotency conflict
    provider operation already in progress
    terminal provider rejection

503
    retryable provider failure
    ambiguous provider outcome
```

Scheduled plan-change failures map to:

```text
400
    missing idempotency key
    invalid idempotency key
    unsupported price code

404
    billing subscription not found

409
    target price already active
    plan change already pending
    subscription not active
    subscription cancellation pending
    idempotency fingerprint conflict
    provider operation in progress
    terminal provider rejection

503
    retryable provider failure
    ambiguous provider outcome
```

Scheduled cancellation failures map to:

```text
400
    missing idempotency key
    invalid idempotency key

404
    billing subscription not found

409
    subscription not active
    subscription already canceled
    cancellation already pending
    idempotency fingerprint conflict
    provider operation in progress
    terminal provider rejection

503
    retryable provider failure
    ambiguous provider outcome
```

Billing webhook ingestion failures map to:

```text
400
    invalid canonical webhook payload
    malformed authenticated JSON
    unsupported canonical event type

401
    missing webhook signature
    malformed webhook signature
    invalid webhook signature
    stale or future signature timestamp

404
    unsupported billing webhook provider

409
    provider event ID reused with different raw bytes

413
    payload exceeds configured limit

503
    temporary database or infrastructure failure
```

Webhook HMAC failures must not include:

```text
WWW-Authenticate: Bearer
```

## Request and correlation identifiers

Every HTTP request receives:

```text
X-Request-ID
X-Correlation-ID
```

When a caller supplies a valid UUID request ID, ClinicOps preserves its canonical
UUID representation. Invalid values are replaced with a generated UUID.

When a caller supplies a valid correlation ID, ClinicOps preserves it. Otherwise,
the correlation ID defaults to the resolved request ID.

The identifiers are:

- stored in request state;
- bound to request-scoped context variables;
- included in lifecycle logs;
- returned as response headers;
- included in Problem Details responses;
- isolated across concurrent requests.

The request ID identifies one HTTP request.

The correlation ID is intended to connect related requests, background jobs,
events, and external integration operations.

## OpenAPI contract

The FastAPI application exposes:

```text
/openapi.json
/docs
```

OpenAPI remains the current source of truth for the HTTP contract.

Protected operations declare an HTTP bearer security scheme.

Request and response schemas are generated from Pydantic transport models.

`204 No Content` responses do not declare a JSON response body.

Problem Details is the standard runtime error contract. Route-level OpenAPI
response declarations may be expanded as the public API surface grows.

This document does not claim that these endpoints or behaviors are
implemented:

- immediate plan change;
- pending-plan replacement;
- pending-plan cancellation;
- subscription renewal application;
- immediate cancellation;
- undo cancellation;
- reactivation;
- final cancellation application;
- automatic entitlement revocation;
- webhook event processing;
- event ordering;
- stale event rejection;
- background jobs;
- automatic retries;
- reconciliation;
- dead-letter queues;
- provider-specific production adapters;
- refunds;
- proration.

## Security decisions

The current authentication transport is API-first.

Access tokens are returned in JSON and sent through the `Authorization` header.
Refresh tokens are also returned and submitted through JSON.

This design supports backend integration testing, explicit transport contracts,
and non-browser clients while the core authentication lifecycle is established.

Browser-specific credential transport is intentionally deferred until a browser
client is introduced.

A browser-facing implementation is expected to evaluate:

- `HttpOnly` refresh-token cookies;
- `Secure` cookie enforcement;
- `SameSite` policy;
- CSRF protection;
- refresh-token cookie scope;
- browser logout semantics;
- cross-origin credential policy;
- trusted frontend origins.

These concerns are deferred rather than partially implemented because they depend
on the deployment topology and browser-client architecture.

## CORS

The API does not require permissive cross-origin access by default.

Wildcard origins must not be combined with credentialed browser requests.

CORS policy should be configured only when a concrete frontend origin and
credential model exist.

## Intentional boundaries

The HTTP transport intentionally does not:

- embed tenant authorization claims in JWTs;
- accept caller-controlled tenant context during login;
- commit inside ordinary application services;
- auto-commit in the database dependency;
- expose internal exception details;
- expose refresh tokens through protected principal responses;
- expose provider customer IDs, provider subscription IDs, provider operation
  keys, or provider request payloads in billing responses;
- allow logout to target arbitrary session IDs;
- implement browser cookies before a browser client exists;
- add tenant selection to global authentication routes.

Billing subscription creation, scheduled plan change, and scheduled
cancellation remain the documented multi-transaction exceptions to flush-only
application services. Those exceptions do not change the default rule for
ordinary domain services.

These boundaries keep identity establishment, session lifecycle, tenant
authorization, and transport concerns explicit and independently testable.

## Verification strategy

The HTTP authentication boundary is covered by:

- schema unit tests;
- dependency-construction tests;
- bearer extraction tests;
- route transaction-ownership tests;
- OpenAPI contract tests;
- PostgreSQL-backed HTTP integration tests;
- service-level authentication tests;
- session-state and refresh-token reuse tests;
- concurrency and race-condition tests.

The end-to-end API security tests verify:

```text
login
    -> current principal
    -> refresh rotation
    -> consumed-token replay
    -> session compromise
    -> replacement credential rejection
    -> logout
    -> persisted revocation
```

This layered test strategy keeps transport coordination tests focused while
preserving detailed service-level coverage of authentication invariants.