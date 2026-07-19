import re
from uuid import UUID, uuid4

import pytest

from clinicops.authentication.exceptions import (
    RefreshTokenInvalidError,
)
from clinicops.authentication.refresh_tokens import (
    REFRESH_TOKEN_SECRET_BYTES,
    REFRESH_TOKEN_SECRET_LENGTH,
    digest_refresh_token,
    generate_refresh_token,
    parse_refresh_token,
    refresh_token_matches,
)

CANONICAL_TOKEN_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def test_generated_refresh_token_has_selector_and_entropy() -> None:
    token_id = uuid4()
    generated = generate_refresh_token(token_id)
    selector, secret = generated.plaintext.split(".", maxsplit=1)

    assert REFRESH_TOKEN_SECRET_BYTES == 32
    assert REFRESH_TOKEN_SECRET_LENGTH == 43
    assert selector == str(token_id)
    assert len(secret) == REFRESH_TOKEN_SECRET_LENGTH
    assert re.fullmatch(r"[A-Za-z0-9_-]+", secret)
    assert generated.token_id == token_id
    assert len(generated.digest) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", generated.digest)


def test_refresh_token_round_trip_returns_selector_and_digest() -> None:
    generated = generate_refresh_token()

    parsed = parse_refresh_token(generated.plaintext)

    assert parsed.token_id == generated.token_id
    assert parsed.digest == generated.digest
    assert refresh_token_matches(
        generated.plaintext,
        generated.digest,
    )


def test_generated_refresh_tokens_are_unique() -> None:
    first_token = generate_refresh_token()
    second_token = generate_refresh_token()

    assert first_token.token_id != second_token.token_id
    assert first_token.plaintext != second_token.plaintext
    assert first_token.digest != second_token.digest


def test_refresh_token_digest_uses_full_plaintext() -> None:
    token_id = uuid4()
    first_token = generate_refresh_token(token_id)
    second_token = generate_refresh_token(token_id)

    assert first_token.token_id == second_token.token_id
    assert first_token.plaintext != second_token.plaintext
    assert first_token.digest != second_token.digest
    assert first_token.digest == digest_refresh_token(first_token.plaintext)


def test_refresh_token_digest_uses_sha256() -> None:
    assert digest_refresh_token("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_refresh_token_comparison_rejects_different_secret() -> None:
    first_token = generate_refresh_token()
    second_token = generate_refresh_token(first_token.token_id)

    assert (
        refresh_token_matches(
            second_token.plaintext,
            first_token.digest,
        )
        is False
    )


def test_refresh_token_repr_hides_plaintext_and_digest() -> None:
    generated = generate_refresh_token()
    parsed = parse_refresh_token(generated.plaintext)

    assert generated.plaintext not in repr(generated)
    assert generated.digest not in repr(generated)
    assert parsed.digest not in repr(parsed)


@pytest.mark.parametrize(
    "plaintext_token",
    [
        "",
        "missing-separator",
        "too.many.separators",
        "not-a-uuid.secret",
        f"{uuid4()}.",
        f"{uuid4()}.short",
        f"{uuid4()}.{'a' * 42}",
        f"{uuid4()}.{'a' * 44}",
        f"{uuid4()}.{'a' * 42}!",
        f"{str(CANONICAL_TOKEN_ID).upper()}.{'a' * 43}",
        f"{CANONICAL_TOKEN_ID.hex}.{'a' * 43}",
    ],
)
def test_refresh_token_parser_rejects_malformed_values(
    plaintext_token: str,
) -> None:
    with pytest.raises(RefreshTokenInvalidError):
        parse_refresh_token(plaintext_token)
