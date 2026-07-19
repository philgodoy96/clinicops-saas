import re

from clinicops.invitations.tokens import (
    INVITATION_TOKEN_BYTES,
    digest_invitation_token,
    generate_invitation_token,
)


def test_generated_invitation_token_is_url_safe_and_high_entropy() -> None:
    token = generate_invitation_token()

    assert INVITATION_TOKEN_BYTES == 32
    assert len(token.plaintext) == 43
    assert re.fullmatch(r"[A-Za-z0-9_-]+", token.plaintext)
    assert len(token.digest) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", token.digest)


def test_generated_invitation_token_contains_matching_digest() -> None:
    token = generate_invitation_token()

    assert token.digest == digest_invitation_token(token.plaintext)
    assert token.plaintext not in token.digest


def test_generated_invitation_tokens_are_unique() -> None:
    first_token = generate_invitation_token()
    second_token = generate_invitation_token()

    assert first_token.plaintext != second_token.plaintext
    assert first_token.digest != second_token.digest


def test_invitation_token_digest_uses_sha256() -> None:
    assert digest_invitation_token("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_invitation_token_repr_hides_plaintext_and_digest() -> None:
    token = generate_invitation_token()
    representation = repr(token)

    assert token.plaintext not in representation
    assert token.digest not in representation
