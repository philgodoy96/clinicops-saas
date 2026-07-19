# Authentication Sessions

## Purpose

ClinicOps uses first-party global authentication to establish user identity independently from tenant authorization.

Authentication answers:

```text
Who is the global user?
Which authentication session is being used?
Is that session still active?
```

Authentication does not answer:

```text
Which tenant is selected?
Which membership is active?
Which tenant role does the user have?
Which tenant permissions are granted?
```

Tenant context and authorization are resolved separately from trusted membership state.

## Ownership Boundaries

The authentication module owns:

- persistent authentication sessions;
- access token issuance and validation;
- refresh token generation and parsing;
- refresh token digesting;
- refresh token rotation;
- refresh token reuse detection;
- session compromise transitions;
- single-session revocation;
- authentication-specific concurrency handling.

The authentication module depends on:

```text
identity
    -> canonical email policy
    -> global user persistence
    -> password credential persistence
    -> Argon2id password verification
    -> global user lifecycle state

core
    -> injectable clock

database
    -> transactions
    -> row locking
    -> lifecycle constraints
    -> partial unique indexes
```

The authentication module does not own:

- tenant selection;
- membership authorization;
- tenant role evaluation;
- permission mapping;
- HTTP cookie transport;
- CSRF protection;
- OAuth providers;
- password reset;
- email verification;
- multi-factor authentication;
- session listing;
- logout from all devices.

## Authentication Model

### AuthSession

An authentication session represents one persistent login context for a global user.

Fields:

```text
id
user_id
status
expires_at
last_rotated_at
revoked_at
compromised_at
created_at
updated_at
```

Lifecycle states:

```text
active
revoked
compromised
```

A global user may have multiple active sessions.

Examples include:

```text
desktop browser
mobile browser
mobile application
another device
```

### RefreshToken

A refresh token row stores only digest and lifecycle state.

Fields:

```text
id
session_id
token_digest
status
expires_at
consumed_at
revoked_at
replaced_by_token_id
created_at
```

Lifecycle states:

```text
active
consumed
revoked
```

A refresh token belongs to exactly one authentication session.

A consumed token may reference the token that replaced it.

## Access Tokens

Access tokens are signed JWTs.

Properties:

```text
format
    -> JWT

algorithm
    -> HS256

lifetime
    -> 15 minutes

persistence
    -> not persisted
```

Claims:

```text
iss
aud
sub
sid
jti
iat
exp
typ
```

Meaning:

```text
sub
    -> global user ID

sid
    -> authentication session ID

jti
    -> unique access token ID

typ
    -> access
```

Access tokens intentionally contain no tenant authorization state.

They do not contain:

```text
tenant_id
membership_id
role
permissions
```

This avoids stale authorization claims after:

- membership disabling;
- role changes;
- ownership transfer;
- tenant disabling;
- permission-policy changes.

Tenant authorization will be resolved from current server-side state.

## JWT Validation

The access token codec validates:

- signature;
- allowed algorithm;
- issuer;
- audience;
- required claims;
- token type;
- UUID claim structure;
- issued-at timestamp;
- expiration ordering;
- absolute expiration.

The validation call explicitly allows only:

```text
HS256
```

The algorithm is not selected from token-controlled input.

The codec receives the current time explicitly so expiration behavior remains deterministic in tests and compatible with the shared clock abstraction.

A token is expired when:

```text
current_time >= expiration_time
```

## Why HS256

ClinicOps currently uses a modular monolith in which the same trusted backend owns both access token issuance and validation.

HS256 is appropriate for this boundary because:

- there is one signing authority;
- there is one validation authority;
- public-key distribution is not required;
- operational complexity remains low.

Asymmetric signing may be introduced when independent services require token verification without access to the signing secret.

## Refresh Token Format

Refresh tokens are opaque bearer secrets.

Format:

```text
<token_id>.<random_secret>
```

Components:

```text
token_id
    -> canonical UUID
    -> database selector

random_secret
    -> 32 cryptographically random bytes
    -> URL-safe encoded
```

The token ID alone is not sufficient for authentication.

The random secret must match the stored digest.

## Refresh Token Persistence

The database stores only:

```text
SHA-256(full plaintext refresh token)
```

The plaintext refresh token is never persisted.

The digest covers both:

```text
token UUID selector
random secret
```

This prevents a valid selector combined with an unrelated secret from authenticating.

Digest comparison uses constant-time comparison.

## Why SHA-256 Is Appropriate

Refresh tokens are generated by the system with high entropy.

They are not human-selected passwords.

```text
Human password
    -> Argon2id

Random refresh bearer token
    -> SHA-256 digest
```

Argon2id protects low-entropy human secrets against offline guessing.

SHA-256 provides deterministic indexed verification for high-entropy random tokens without persisting the bearer secret.

## Representation Safety

Sensitive fields are excluded from default object representations.

Examples:

```text
AuthenticationTokenConfig
    -> signing key excluded from repr

IssuedAccessToken
    -> access token excluded from repr

GeneratedRefreshToken
    -> plaintext and digest excluded from repr

ParsedRefreshToken
    -> digest excluded from repr

AuthenticateUserCommand
    -> password excluded from repr

AuthenticatedSession
    -> access token and refresh token excluded from repr

RefreshAuthenticationCommand
    -> refresh token excluded from repr

RefreshedAuthentication
    -> access token and refresh token excluded from repr
```

This reduces accidental exposure through diagnostic logs.

It does not replace explicit secret-safe logging practices.

## Persistence Invariants

### Authentication Session Expiration

The database enforces:

```text
expires_at > created_at
```

Constraint:

```text
ck_auth_sessions_expires_after_created_at
```

### Authentication Session State Consistency

The database enforces:

```text
active
    -> revoked_at is null
    -> compromised_at is null

revoked
    -> revoked_at is not null
    -> compromised_at is null

compromised
    -> compromised_at is not null
    -> revoked_at is null
```

Constraint:

```text
ck_auth_sessions_status_consistency
```

### Refresh Token Expiration

The database enforces:

```text
expires_at > created_at
```

Constraint:

```text
ck_refresh_tokens_expires_after_created_at
```

### Refresh Token State Consistency

The database enforces:

```text
active
    -> consumed_at is null
    -> revoked_at is null
    -> replaced_by_token_id is null

consumed
    -> consumed_at is not null
    -> revoked_at is null
    -> replaced_by_token_id is not null

revoked
    -> revoked_at is not null
    -> consumed_at is null
    -> replaced_by_token_id is null
```

Constraint:

```text
ck_refresh_tokens_status_consistency
```

### Unique Token Digest

The database enforces:

```text
UNIQUE (token_digest)
```

Constraint:

```text
uq_refresh_tokens_token_digest
```

### One Active Refresh Token per Session

The database enforces a partial unique index equivalent to:

```sql
UNIQUE (session_id)
WHERE status = 'active'
```

Index:

```text
uq_refresh_tokens_one_active_per_session
```

Consumed and revoked history may coexist with one active token.

### Replacement Integrity

A refresh token cannot reference itself as its replacement.

Constraint:

```text
ck_refresh_tokens_not_self_replaced
```

The replacement foreign key is:

```text
DEFERRABLE INITIALLY DEFERRED
```

Constraint:

```text
fk_refresh_tokens_replaced_by_token_id_refresh_tokens
```

This allows rotation to:

```text
mark current token consumed
link replacement UUID
flush
insert replacement token
flush
commit
```

The database still validates the final replacement relationship at transaction completion.

## Password Authentication

The login service accepts:

```text
AuthenticateUserCommand
- email
- password
```

It returns:

```text
AuthenticatedSession
- user_id
- session_id
- access_token
- access_token_expires_at
- refresh_token
- session_expires_at
```

### Login Flow

```text
Canonicalize email
        |
        v
Load global user and password credential
        |
        v
Verify password
        |
        v
Reject invalid credentials generically
        |
        v
Lock and refresh global user
        |
        v
Require active user
        |
        v
Create 30-day authentication session
        |
        v
Generate initial refresh token
        |
        v
Persist only refresh-token digest
        |
        v
Issue 15-minute access token
        |
        v
Flush without commit
```

### Generic Credential Failure

These cases produce the same public failure:

```text
unknown email
incorrect password
missing password credential
disabled user
```

Public error:

```text
InvalidCredentialsError
```

The response does not reveal which condition failed.

### Dummy Password Verification

When the email is unknown or no password credential exists, the service still performs Argon2id verification against a valid dummy hash.

This reduces obvious timing differences between:

```text
unknown email
incorrect password
```

The dummy hash:

- is not attached to a user;
- cannot authenticate;
- cannot create a session;
- is used only to preserve comparable password-verification cost.

## Absolute Session Expiration

Authentication sessions use an absolute lifetime:

```text
30 days
```

Refresh rotation does not extend the session.

Every replacement refresh token receives:

```text
expires_at = auth_session.expires_at
```

This prevents indefinite session extension through continuous token rotation.

Sliding expiration is intentionally deferred.

## Refresh Rotation

The refresh service accepts:

```text
RefreshAuthenticationCommand
- refresh_token
```

A successful rotation returns:

```text
RefreshedAuthentication
- user_id
- session_id
- access_token
- access_token_expires_at
- refresh_token
- session_expires_at
```

### Lock Discovery

The token UUID selector is used for an initial metadata query.

The query returns only:

```text
token_id
session_id
user_id
```

It does not load mutable ORM entities.

This allows the service to establish a stable lock order.

### Lock Order

```text
User
    |
    v
AuthSession
    |
    v
RefreshToken
```

Locked ORM entities are refreshed using:

```text
populate_existing = true
```

This prevents stale SQLAlchemy identity-map state after a transaction waits for another operation to release a row lock.

### Successful Rotation Flow

```text
Parse token
        |
        v
Resolve metadata
        |
        v
Lock and refresh user
        |
        v
Lock and refresh session
        |
        v
Lock and refresh supplied token
        |
        v
Validate digest
        |
        v
Require active user
        |
        v
Require active session
        |
        v
Require token and session not expired
        |
        v
Require active refresh token
        |
        v
Generate replacement token
        |
        v
Mark current token consumed
        |
        v
Link replacement UUID
        |
        v
Flush current state
        |
        v
Insert active replacement
        |
        v
Flush replacement
        |
        v
Issue new access token
        |
        v
Return bearer tokens without commit
```

## Refresh Token Reuse Detection

A consumed refresh token cannot be used again.

When a consumed token with a valid secret is presented:

```text
Lock user
        |
        v
Lock session
        |
        v
Lock consumed token
        |
        v
Verify digest
        |
        v
Lock current active replacement
        |
        v
Mark session compromised
        |
        v
Revoke active replacement
        |
        v
Flush without commit
```

Result:

```text
CompromisedAuthenticationSession
- session_id
- compromised_at
- reason = refresh_token_reuse
```

This is returned as a security transition result rather than raised as a normal exception.

The caller must commit the compromise transition.

After commit:

```text
session status = compromised
active refresh tokens = 0
```

### Wrong-Secret Protection

A token ID combined with an incorrect secret does not compromise the session.

The digest is validated before the token lifecycle state is used for reuse detection.

```text
valid token UUID + incorrect secret
    -> RefreshTokenInvalidError
    -> no session state change
```

## Concurrent Refresh of the Same Token

Two transactions may present the same active token concurrently.

Expected sequence:

```text
Transaction A
    -> rotates active token
    -> commits replacement

Transaction B
    -> waits for locks
    -> refreshes token state
    -> observes consumed token
    -> compromises session
    -> revokes replacement
```

Final state:

```text
session = compromised
original token = consumed
replacement token = revoked
active token count = 0
```

This behavior protects the token family against replay.

Clients must serialize refresh requests and replace stored refresh tokens atomically.

## Old-Token Replay During Current Rotation

Given:

```text
Token 1 = consumed
Token 2 = active
```

The system supports two valid interleavings.

### Rotation First

```text
Token 2 consumed
Token 3 active
Old Token 1 replayed
Session compromised
Token 3 revoked
```

### Replay First

```text
Old Token 1 replayed
Session compromised
Token 2 revoked
Token 2 rotation observes inactive session
```

Both outcomes preserve:

```text
session = compromised
active token count = 0
```

## Session Revocation

The logout service accepts:

```text
RevokeAuthenticationSessionCommand
- user_id
- session_id
```

It returns:

```text
RevokedAuthenticationSession
- session_id
- revoked_at
```

### Ownership Boundary

The session query requires:

```text
session.id = session_id
session.user_id = user_id
```

A user cannot revoke another user's session by supplying its ID.

Missing users, missing sessions, and sessions owned by another user produce the same unavailable-session failure.

### Revocation Flow

```text
Lock and refresh user
        |
        v
Lock and refresh user-owned session
        |
        v
Require active session
        |
        v
Lock active refresh token
        |
        v
Mark session revoked
        |
        v
Revoke active refresh token
        |
        v
Flush without commit
```

Consumed token history remains unchanged.

### Session Without Active Token

An active session may still be revoked when no active refresh token exists.

The session is the persistent security boundary.

Token presence is not required for administrative or user-driven invalidation.

## Refresh Versus Logout

Refresh and logout use the same initial lock order:

```text
User
    |
    v
AuthSession
    |
    v
RefreshToken
```

Two outcomes are valid.

### Refresh First

```text
current token consumed
replacement created
logout acquires locks
session revoked
replacement revoked
```

### Logout First

```text
session revoked
active token revoked
refresh acquires locks
refresh observes inactive session
```

Final invariant:

```text
session = revoked
active token count = 0
```

## Transaction Ownership

Authentication services call:

```text
flush()
```

but do not call:

```text
commit()
```

The caller owns the final transaction decision.

### Login Contract

```text
AuthenticatedSession
    -> commit before returning bearer tokens
```

### Successful Refresh Contract

```text
RefreshedAuthentication
    -> commit before returning replacement tokens
```

### Reuse Detection Contract

```text
CompromisedAuthenticationSession
    -> commit compromise transition
    -> return authentication failure
```

Rolling back a compromise result would restore the stolen token family and lose the security transition.

### Logout Contract

```text
RevokedAuthenticationSession
    -> commit revocation
```

Tests verify rollback behavior explicitly for:

- login;
- successful refresh rotation;
- session compromise;
- session revocation.

## Failure Model

Expected authentication failures include:

```text
AuthenticationConfigurationError
InvalidCredentialsError
AuthenticationSessionNotFoundError
AuthenticationSessionInactiveError
AccessTokenInvalidError
AccessTokenExpiredError
RefreshTokenInvalidError
RefreshTokenExpiredError
```

Identity lifecycle failures may propagate where appropriate:

```text
UserDisabledError
InvalidEmailError
```

These failures:

- do not import FastAPI;
- do not contain HTTP status codes;
- do not expose passwords;
- do not expose access tokens;
- do not expose refresh tokens;
- do not expose token digests;
- do not expose signing keys.

A future API boundary will map application failures to transport-specific responses.

## Security Boundaries

The authentication foundation establishes these rules:

- passwords use Argon2id;
- unknown email and incorrect password share one public failure;
- unknown users still incur dummy Argon2id verification;
- plaintext refresh tokens are never persisted;
- access tokens and refresh tokens are excluded from object representations;
- signing keys are excluded from object representations;
- refresh token digest comparison is constant-time;
- access tokens are short-lived;
- access tokens contain no tenant authorization claims;
- refresh tokens rotate after each successful use;
- consumed token reuse compromises the session;
- compromise revokes the current active replacement;
- revoked sessions cannot refresh;
- compromised sessions cannot refresh;
- disabled users cannot log in or refresh;
- session ownership is checked server-side;
- one user cannot revoke another user's session;
- one session may have at most one active refresh token;
- database constraints remain the final concurrency authority;
- transaction callers must commit security transitions explicitly.

## Indexing and Scaling

Authentication persistence includes:

```text
INDEX auth_sessions(user_id, status)
INDEX refresh_tokens(session_id, status)
UNIQUE refresh_tokens(token_digest)
PARTIAL UNIQUE refresh_tokens(session_id) WHERE status = active
```

These support:

- user-session queries;
- active session lookup;
- token-family queries;
- active-token lookup;
- token digest uniqueness;
- active-token concurrency enforcement.

Row locking and database constraints remain valid across:

- multiple API processes;
- worker processes;
- multiple application instances;
- concurrent database sessions.

No in-process mutex is required for correctness.

## Intentionally Deferred

The following capabilities are intentionally deferred:

- authentication HTTP endpoints;
- cookie transport;
- CSRF protection;
- CORS policy;
- refresh token delivery policy;
- OAuth providers;
- external identity providers;
- multi-factor authentication;
- password reset;
- email verification;
- session listing;
- logout from all devices;
- device naming;
- device fingerprints;
- IP address tracking;
- user-agent tracking;
- suspicious-login detection;
- session cleanup jobs;
- sliding session expiration;
- tenant selection;
- tenant-context resolution;
- tenant permission evaluation;
- asymmetric JWT signing;
- signing-key rotation;
- token revocation lists for access JWTs.

The architecture keeps these concerns outside the core session lifecycle so they can be introduced through explicit boundaries rather than coupled into token persistence.