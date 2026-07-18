from dataclasses import FrozenInstanceError

import pytest

from clinicops.core.request_context import (
    RequestContext,
    bind_request_context,
    get_correlation_id,
    get_request_context,
    get_request_id,
    reset_request_context,
)


def test_request_context_is_empty_when_not_bound() -> None:
    assert get_request_context() is None
    assert get_request_id() is None
    assert get_correlation_id() is None


def test_bound_request_context_is_available_until_reset() -> None:
    context = RequestContext(
        request_id="request-123",
        correlation_id="correlation-456",
    )

    token = bind_request_context(context)

    try:
        assert get_request_context() == context
        assert get_request_id() == "request-123"
        assert get_correlation_id() == "correlation-456"
    finally:
        reset_request_context(token)

    assert get_request_context() is None


def test_reset_restores_previous_nested_context() -> None:
    outer_context = RequestContext(
        request_id="outer-request",
        correlation_id="outer-correlation",
    )
    inner_context = RequestContext(
        request_id="inner-request",
        correlation_id="inner-correlation",
    )

    outer_token = bind_request_context(outer_context)

    try:
        inner_token = bind_request_context(inner_context)

        try:
            assert get_request_context() == inner_context
        finally:
            reset_request_context(inner_token)

        assert get_request_context() == outer_context
    finally:
        reset_request_context(outer_token)

    assert get_request_context() is None


def test_request_context_is_immutable() -> None:
    context = RequestContext(
        request_id="request-123",
        correlation_id="correlation-456",
    )

    field_name = "request_id"
    with pytest.raises(FrozenInstanceError):
        setattr(context, field_name, "different-request")
