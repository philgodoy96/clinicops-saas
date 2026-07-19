from dataclasses import dataclass
from hashlib import sha256
from secrets import token_urlsafe

INVITATION_TOKEN_BYTES = 32


@dataclass(frozen=True, slots=True)
class InvitationToken:
    """One-time plaintext token and its persistence-safe digest."""

    plaintext: str
    digest: str


def digest_invitation_token(plaintext_token: str) -> str:
    """Return the SHA-256 digest used to resolve an invitation token."""

    return sha256(plaintext_token.encode("utf-8")).hexdigest()


def generate_invitation_token() -> InvitationToken:
    """Generate a high-entropy URL-safe invitation bearer token."""

    plaintext = token_urlsafe(INVITATION_TOKEN_BYTES)

    return InvitationToken(
        plaintext=plaintext,
        digest=digest_invitation_token(plaintext),
    )
