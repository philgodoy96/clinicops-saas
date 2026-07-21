# Local User Bootstrap

## Purpose

ClinicOps uses invitation-based onboarding and does not currently expose
public self-service registration.

A local environment therefore needs one initial global user that can
authenticate, create the first tenant, and become that tenant's owner. The
repository provides an explicit command-line bootstrap boundary for this
purpose.

The bootstrap command creates only the global identity and password
credential. It does not create a tenant, assign a platform-administrator
role, or start an authenticated session.

After the command succeeds, the user completes the normal application flow:

```text
Bootstrap global user
    -> log in
    -> create first tenant
    -> become tenant owner
    -> invite tenant members
```

## Why registration is not exposed

Public registration is intentionally deferred.

A production self-service registration flow requires product and security
decisions beyond creating a database row, including:

- email verification;
- account activation;
- abuse and rate controls;
- account-enumeration behavior;
- consent and terms tracking;
- whether registration also creates a tenant;
- interaction between registration and pending invitations.

ClinicOps currently focuses on invitation-based tenant onboarding. The
bootstrap command provides the initial local identity without introducing an
incomplete public registration contract.

## Configuration

Add the following values to the local `.env` file:

```env
CLINICOPS_BOOTSTRAP_USER_EMAIL=owner@northstar.example.com
CLINICOPS_BOOTSTRAP_USER_PASSWORD=replace-with-a-local-password
```

The password must satisfy the identity password policy:

- minimum length: 12 characters;
- maximum length: 128 characters.

Do not commit real bootstrap credentials.

The bootstrap variables are CLI-specific. They are not fields on the main
application `Settings` model and are not required for API startup.

## Prerequisites

Install dependencies and apply all database migrations:

```powershell
uv sync
uv run alembic upgrade head
uv run alembic current
```

The configured PostgreSQL database must be reachable before running the
command.

## Run the command

Execute:

```powershell
uv run python -m clinicops.cli.bootstrap_user
```

A successful first execution prints safe operational information:

```text
Local bootstrap user created successfully. user_id=<uuid> email=<email>
```

The command does not print the plaintext password or the stored password
hash.

## Log in after bootstrap

Start the API and authenticate with the same configured email and password:

```http
POST /api/v1/auth/login
Content-Type: application/json
```

```json
{
  "email": "owner@northstar.example.com",
  "password": "replace-with-a-local-password"
}
```

The login response returns the access token, refresh token, and
authentication-session identifiers.

The authenticated user can then create the first tenant through:

```http
POST /api/v1/tenants
Authorization: Bearer <access-token>
Content-Type: application/json
```

## Idempotency

The command is safe to execute repeatedly for the same canonical email.

When the user already exists:

```text
Local bootstrap user already exists; no database changes were made.
user_id=<uuid> email=<email>
```

The existing user is returned as an idempotent success.

The command does not:

- create a duplicate user;
- create an additional password credential;
- compare the supplied password with the existing credential;
- replace or reset the existing password;
- create a tenant;
- create an authentication session.

Changing the bootstrap password in `.env` after the user already exists does
not update the stored credential.

## Transaction ownership

The command is an operational transport boundary.

`CreateUserService` owns identity creation rules and flushes the new user and
password credential without committing. The CLI owns the outer transaction:

```text
CLI
    -> open database session
    -> resolve canonical email
    -> detect an existing user
    -> execute CreateUserService when creation is required
    -> commit after success
    -> roll back after failure
```

PostgreSQL's unique email constraint remains the definitive protection
against concurrent duplicate creation. A concurrent duplicate is treated as
idempotent only after the losing transaction rolls back and the committed
user can be resolved.

## Security boundaries

The bootstrap command:

- runs only when explicitly invoked;
- is not executed during API startup;
- uses the existing email canonicalization and password hashing;
- stores only the Argon2 password hash;
- never prints credential material;
- does not reset existing credentials;
- does not grant a tenant role until the user creates a tenant;
- does not grant a platform-administrator role.

The command is intended for local development, portfolio demonstrations, and
controlled test environments.

Production identity provisioning requires an explicit operational model and
is intentionally outside this command's scope.

## Troubleshooting

### Missing configuration

When one or both CLI variables are missing, the command exits with a
configuration error.

Confirm that `.env` contains:

```env
CLINICOPS_BOOTSTRAP_USER_EMAIL=
CLINICOPS_BOOTSTRAP_USER_PASSWORD=
```

### Existing user cannot log in with the current `.env` password

An idempotent bootstrap execution does not replace an existing password.

Use the password that was supplied when the user was created. Credential
reset is a separate identity workflow and is not performed by this command.

### Database connection failure

Confirm that PostgreSQL is running and that `CLINICOPS_DATABASE_URL` points
to the intended database.

Then verify:

```powershell
uv run alembic current
```