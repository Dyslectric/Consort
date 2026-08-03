"""Phase one staging harness for team-scoped Jitsi conferencing.

Proves the assumption that everything downstream rests on: a token minted for
one tenant and one room cannot open anything else.
"""

from .bosh import BoshProbe, Hosts, Outcome, ProbeResult
from .keys import GeneratedKeypair, generate_keypair, keyfile_name
from .tokens import (
    JitsiFeatures,
    JitsiUser,
    SigningConfig,
    TokenError,
    TokenRequest,
    inspect,
    mint,
    verify,
)

__all__ = [
    "BoshProbe",
    "GeneratedKeypair",
    "Hosts",
    "JitsiFeatures",
    "JitsiUser",
    "Outcome",
    "ProbeResult",
    "SigningConfig",
    "TokenError",
    "TokenRequest",
    "generate_keypair",
    "inspect",
    "keyfile_name",
    "mint",
    "verify",
]
