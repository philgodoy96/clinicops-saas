# Request Tracing Model

## Purpose

ClinicOps assigns trace identifiers to every HTTP request so application logs, API responses, database failures, and future background operations can be connected during investigation.

The tracing model currently provides:

- one request identifier for each HTTP execution;
- one correlation identifier for a related workflow;
- structured request lifecycle logs;
- trace identifiers in response headers;
- trace identifiers in public error responses;
- concurrency-safe context propagation.

These identifiers improve operational traceability. They are not a replacement for distributed tracing, spans, metrics, or audit logs.

## Identifiers

### Request ID

The request ID identifies one HTTP request execution.

Header:

```text
X-Request-ID
```

Behavior:

| Incoming value | Result |
| --- | --- |
| Valid UUID | Preserved in canonical lowercase form |
| Missing | A UUIDv4 is generated |
| Invalid | A UUIDv4 is generated |

Each independently executed HTTP request receives its own request ID.

### Correlation ID

The correlation ID connects operations that belong to the same workflow.

Header:

```text
X-Correlation-ID
```

Behavior:

| Incoming value | Result |
| --- | --- |
| Valid UUID | Preserved in canonical lowercase form |
| Missing | The request ID is reused |
| Invalid | The request ID is reused |

A caller may preserve one correlation ID across multiple HTTP requests while allowing every request to receive a distinct request ID.

## Response Headers

Every HTTP response includes:

```text
X-Request-ID
X-Correlation-ID
```

This applies to:

- successful responses;
- validation failures;
- missing routes;
- unsupported HTTP methods;
- expected application failures;
- unexpected internal failures.

Clients should include these identifiers when reporting an operational problem.

## Context Propagation

Request identifiers are stored in a `ContextVar` through an immutable request context object.

This allows application code to read the current identifiers without receiving a FastAPI `Request` object as a dependency.

The middleware follows this lifecycle:

```text
Resolve identifiers
        |
        v
Bind request context
        |
        v
Execute downstream application
        |
        v
Add response headers
        |
        v
Reset request context
```

The reset operation restores the previous context instead of assigning a generic empty value. This preserves correct behavior for nested contexts and prevents identifiers from leaking between requests.

The middleware also stores the identifiers in the ASGI request state. This provides a defensive fallback for error handlers that may execute after an exception crosses the installed application middleware.

## Concurrency Isolation

The request context uses execution-local state rather than global mutable state or thread-local storage.

Automated coverage forces concurrent requests to overlap and verifies that:

- each request retains its own request ID;
- each request retains its own correlation ID;
- lifecycle logs contain the correct identifiers;
- response headers contain the correct identifiers;
- no request context remains after completion.

## Structured Request Logs

The HTTP middleware emits the following lifecycle events.

### Request Started

```text
request_started
```

Fields:

```text
event
request_id
correlation_id
http_method
http_path
```

### Request Finished

```text
request_finished
```

Fields:

```text
event
request_id
correlation_id
http_method
http_path
status_code
duration_ms
```

### Request Failed

```text
request_failed
```

Fields:

```text
event
request_id
correlation_id
http_method
http_path
duration_ms
exception
```

Duration is measured with a monotonic performance counter and reported in milliseconds.

The application lifecycle events are the authoritative HTTP access logs. The default Uvicorn access log is disabled to avoid duplicate request records. Uvicorn startup and error logs remain enabled.

## Log Privacy

The request middleware does not record:

- request bodies;
- response bodies;
- authorization headers;
- cookies;
- query strings;
- invalid incoming trace identifiers;
- database credentials;
- patient data.

Only the URL path is logged.

For example:

```text
/api/v1/health/live
```

The following query data is intentionally excluded:

```text
/api/v1/health/live?email=patient@example.com
```

Application modules must continue to avoid placing secrets or sensitive business data in log messages or structured fields.

## Public Error Responses

API errors use a consistent top-level structure:

```json
{
  "error": {
    "code": "not_found",
    "message": "The requested resource was not found.",
    "request_id": "934ef0d2-72a5-4b15-b807-dce8797be013",
    "correlation_id": "934ef0d2-72a5-4b15-b807-dce8797be013"
  }
}
```

Validation failures may include sanitized details:

```json
{
  "error": {
    "code": "request_validation_error",
    "message": "The request could not be validated.",
    "request_id": "934ef0d2-72a5-4b15-b807-dce8797be013",
    "correlation_id": "934ef0d2-72a5-4b15-b807-dce8797be013",
    "details": [
      {
        "location": ["path", "item_id"],
        "message": "Input should be a valid integer.",
        "type": "int_parsing"
      }
    ]
  }
}
```

Validation responses intentionally exclude the raw rejected value.

Unexpected failures return a generic public message:

```json
{
  "error": {
    "code": "internal_server_error",
    "message": "An unexpected error occurred.",
    "request_id": "934ef0d2-72a5-4b15-b807-dce8797be013",
    "correlation_id": "934ef0d2-72a5-4b15-b807-dce8797be013"
  }
}
```

Stack traces, exception classes, SQL statements, filesystem paths, and internal exception messages are not exposed to clients.

## Error Ownership

Application exceptions do not contain HTTP status codes.

The dependency direction is:

```text
Application or infrastructure failure
                |
                v
API exception mapping
                |
                v
Public HTTP response
```

This keeps the core and database layers independent from FastAPI transport concerns.

The database readiness flow follows the same rule:

```text
SQLAlchemy failure
        |
        v
DatabaseUnavailableError
        |
        v
API error handler
        |
        v
HTTP 503 database_unavailable
```

## Operational Usage

When investigating a reported API failure:

1. obtain the `X-Request-ID` or `X-Correlation-ID` from the client response;
2. search structured logs using the identifier;
3. locate the matching `request_started`, `request_finished`, or `request_failed` event;
4. follow the correlation ID across related operations;
5. inspect internal exception information only in trusted operational logs.

Request IDs should not be reused across independent HTTP requests.

Correlation IDs may be reused when multiple operations belong to the same business workflow.

## Intentionally Deferred

The current model intentionally defers:

- user and tenant identifiers in logging context;
- background job execution context;
- payment event context;
- distributed trace IDs and span IDs;
- OpenTelemetry instrumentation;
- external log aggregation;
- metrics and tracing exporters;
- a generalized PII redaction framework.

These capabilities will be introduced when the corresponding application workflows and operational requirements exist.