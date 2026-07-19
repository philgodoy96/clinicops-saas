# Global Identity Model

## Purpose

ClinicOps models users as global platform identities.

A user may later belong to multiple tenants through independent membership records. Authentication identifies the global user, while tenant membership and role-based authorization determine what that user may access within a selected tenant.

The identity module therefore remains independent from clinics, tenants, memberships, roles, subscriptions, patients, and billing state.

## Ownership Boundary

The identity module owns:

- global users;
- password credentials;
- canonical email validation;
- password policy validation;
- password hashing and verification;
- global identity creation.

The identity module does not own:

- tenant membership;
- tenant roles;
- tenant ownership;
- invitation lifecycle;
- authentication sessions;
- access or refresh tokens;
- password reset;
- email verification;
- OAuth providers.

These responsibilities remain separate so identity creation can be reused safely by registration, invitation acceptance, administrative provisioning, and future authentication workflows.

## Persistence Model

### User

The `users` table stores one global platform identity.

Fields:

```text
id
email
status
created_at
updated_at
disabled_at
```

Properties:

- `id` is a UUID;
- `email` stores the canonical email address;
- `email` is globally unique;
- `status` is either `active` or `disabled`;
- disabling a user preserves the identity record;
- tenant and role information are not stored on the user.

### Password Credential

The `password_credentials` table stores password authentication material separately from the user identity.

Fields:

```text
user_id
password_hash
created_at
updated_at
```

`user_id` is both the primary key and a foreign key to `users.id`.

This enforces at most one password credential per user.

The conceptual relationship is:

```text
User 1 ---- 0..1 PasswordCredential
```

The relationship remains optional at the persistence level so future identities may use non-password authentication providers. The current creation service always creates a user and password credential together.

## Identity Invariants

The global identity model enforces the following invariants:

- one canonical email belongs to at most one global user;
- user uniqueness is enforced by PostgreSQL;
- user and password credential creation succeed or fail atomically;
- plaintext passwords are never persisted;
- plaintext passwords are never logged;
- password hashes are not included in application result objects;
- tenant roles never belong to `User`;
- repositories do not commit transactions;
- the application service does not expose SQLAlchemy models as its public result;
- database failures unrelated to the email uniqueness constraint are not translated into duplicate-email failures.

## Email Canonicalization

Email input follows this pipeline:

```text
Raw input
    |
    v
Trim surrounding whitespace
    |
    v
Validate email syntax
    |
    v
Use the normalized library representation
    |
    v
Convert to lowercase
    |
    v
Persist the canonical email
```

Examples:

```text
USER@EXAMPLE.COM
user@example.com
```

Both values represent the same global identity.

DNS deliverability checks are intentionally excluded from identity creation. Email ownership and deliverability will belong to a future verification workflow.

The database stores only the canonical representation. A second normalized-email column is intentionally avoided because two persisted representations could diverge.

## Password Policy

Passwords used to create credentials must satisfy:

```text
Minimum length: 12 characters
Maximum length: 128 characters
Unicode: allowed
Whitespace: allowed
Composition rules: not required
Silent truncation: forbidden
```

The policy supports long passphrases without requiring arbitrary uppercase, lowercase, numeric, or symbol combinations.

Values outside the accepted length range are rejected before hashing.

## Password Hashing

ClinicOps uses Argon2id through a dedicated password hashing boundary.

The boundary supports:

```text
hash
verify
needs_rehash
```

The encoded Argon2id value contains the algorithm, version, parameters, salt, and derived hash. Salt is not stored in a separate database column.

Each hash operation uses a random salt, so hashing the same password multiple times produces different encoded values.

Verification behavior distinguishes between:

```text
Valid encoded hash + incorrect password -> false
Malformed or corrupted encoded hash -> internal failure
```

A malformed persisted hash is not treated as an ordinary authentication mismatch because it may indicate data corruption or an operational defect.

Rehash detection allows future authentication flows to upgrade stored hashes when security parameters change.

## User Creation Flow

The application service accepts:

```text
CreateUserCommand
- email
- password
```

It returns:

```text
CreatedUser
- id
- email
- status
- created_at
```

The flow is:

```text
Canonicalize email
        |
        v
Validate password policy
        |
        v
Check for an existing canonical email
        |
        v
Hash the plaintext password
        |
        v
Create User and PasswordCredential
        |
        v
Add and flush the unit of work
        |
        v
Return CreatedUser
```

The result intentionally excludes the plaintext password, password hash, credential model, and SQLAlchemy entity.

## Transaction Ownership

The creation service calls `flush()` but does not call `commit()`.

This allows a future workflow to execute multiple operations atomically:

```text
Create User
Create Membership
Accept Invitation
Write Audit Log
Commit
```

The caller owns the final transaction decision.

If any operation fails, the caller may roll back the complete unit of work.

## Duplicate Email Concurrency

An application pre-check improves the normal duplicate-email path but cannot enforce uniqueness under concurrency.

Two transactions may both observe that an email is absent:

```text
Transaction A: SELECT -> no user
Transaction B: SELECT -> no user
```

Both may then attempt insertion.

The definitive guarantee is the PostgreSQL constraint:

```text
uq_users_email
```

The repository translates only a violation of this named constraint into:

```text
EmailAlreadyRegisteredError
```

Other integrity failures remain visible as infrastructure failures.

Automated integration coverage forces this race and verifies that exactly one transaction succeeds.

## Atomic Credential Creation

`User` and `PasswordCredential` are created in the same database transaction.

If credential persistence fails, the transaction is rolled back and the user does not remain stored without authentication material.

Automated integration coverage verifies this behavior by forcing the credential insert to fail after the user insert has entered the unit of work.

## Failure Model

Expected identity failures include:

```text
InvalidEmailError
PasswordPolicyViolationError
EmailAlreadyRegisteredError
```

These errors:

- do not import FastAPI;
- do not contain HTTP status codes;
- do not expose passwords or hashes;
- may be mapped by a future API layer.

Unexpected hashing failures, database availability failures, corrupted hashes, and unrelated integrity errors remain internal failures rather than being converted into misleading identity errors.

## Security Boundaries

The current implementation protects the following boundaries:

- passwords are validated before hashing;
- only encoded Argon2id hashes are persisted;
- password material is separated from the main user record;
- duplicate-email detection does not reveal database details;
- public creation results exclude credential data;
- user identifiers are UUIDs;
- email uniqueness is global rather than tenant-scoped;
- disabled users remain available for audit and historical references.

Logging code must never include plaintext passwords, password hashes, or complete credential objects.

## Scaling Considerations

The global unique email constraint provides an indexed lookup path for future authentication.

Global users are stored once even when they later belong to multiple tenants. Membership growth therefore does not duplicate identity records.

Password hashing is intentionally expensive. Authentication capacity must be benchmarked against production CPU and memory limits before deployment.

Password hashing is currently synchronous. Future asynchronous HTTP flows must ensure hashing does not block an event loop.

Caching is intentionally deferred because current identity operations do not yet present a read pattern that justifies cache invalidation and consistency complexity.

## Intentionally Deferred

The following capabilities are intentionally deferred:

- registration endpoints;
- login and logout;
- access tokens;
- refresh tokens;
- server-side sessions;
- email verification;
- password reset;
- multi-factor authentication;
- OAuth providers;
- tenant memberships;
- tenant roles;
- invitation acceptance;
- email change workflows;
- account deletion;
- login attempt tracking;
- authentication rate limiting;
- compromised-password screening.

These capabilities will build on the global identity foundation without moving tenant authorization or session state into the user model.