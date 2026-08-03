"""
RS256 keypair generation for Jitsi's ASAP key server.

Rev 3 section 2.7 is the reason this file exists. `asap_key_server` is *not* a
JWKS endpoint. It is a base URL under which Prosody expects to find a file named

    sha256(<kid>).pem

containing the PEM-encoded public key. Pointing it at an identity provider's
JWKS URL does not work, and the failure is confusing because the configuration
looks correct. This module computes the filename for you so that nobody has to
remember it at 2am.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

DEFAULT_KEY_SIZE = 2048


def keyfile_name(key_id: str) -> str:
    """Return the filename Prosody will request for a given `kid`.

    >>> keyfile_name("zulip-jitsi-2026-07")[-4:]
    '.pem'
    """
    if not key_id:
        raise ValueError("key_id must not be empty")
    return hashlib.sha256(key_id.encode("utf-8")).hexdigest() + ".pem"


@dataclass(frozen=True)
class GeneratedKeypair:
    key_id: str
    private_key_path: Path
    public_key_path: Path
    private_key_pem: bytes
    public_key_pem: bytes


def generate_keypair(
    key_id: str,
    private_key_dir: Path,
    keyserver_dir: Path,
    *,
    key_size: int = DEFAULT_KEY_SIZE,
    overwrite: bool = False,
) -> GeneratedKeypair:
    """Generate an RS256 keypair and lay it out the way Prosody expects.

    The private key is written to `private_key_dir/<kid>.key` with 0600
    permissions. The public key is written to `keyserver_dir/sha256(kid).pem`,
    which is the path the ASAP key server must serve.
    """
    private_key_dir.mkdir(parents=True, exist_ok=True)
    keyserver_dir.mkdir(parents=True, exist_ok=True)

    private_path = private_key_dir / f"{key_id}.key"
    public_path = keyserver_dir / keyfile_name(key_id)

    if not overwrite:
        for path in (private_path, public_path):
            if path.exists():
                raise FileExistsError(
                    f"{path} already exists; pass overwrite=True to replace it. "
                    "Rotating a key means minting a new kid, not reusing one."
                )

    key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)

    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    private_path.write_bytes(private_pem)
    private_path.chmod(0o600)
    public_path.write_bytes(public_pem)
    public_path.chmod(0o644)

    return GeneratedKeypair(
        key_id=key_id,
        private_key_path=private_path,
        public_key_path=public_path,
        private_key_pem=private_pem,
        public_key_pem=public_pem,
    )
