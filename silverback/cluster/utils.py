# NOTE: Copied from
# https://github.com/fief-dev/fief-python/blob/main/fief_client/pkce.py

import base64
import hashlib
import secrets
from typing import Literal


def get_validation_hash(value: str) -> str:
    """
    Return the validation hash of a value.

    Useful to check the validity `c_hash` and `at_hash` claims.
    """
    hasher = hashlib.sha256()
    hasher.update(value.encode("utf-8"))
    hash = hasher.digest()

    half_hash = hash[0 : int(len(hash) / 2)]  # noqa: E203
    # Remove the Base64 padding "==" at the end
    base64_hash = base64.urlsafe_b64encode(half_hash)[:-2]

    return base64_hash.decode("utf-8")


def is_valid_hash(value: str, hash: str) -> bool:
    """
    Check if a hash corresponds to the provided value.

    Useful to check the validity `c_hash` and `at_hash` claims.
    """
    value_hash = get_validation_hash(value)
    return secrets.compare_digest(value_hash, hash)


def get_code_verifier() -> str:
    """
    Generate a code verifier suitable for PKCE.
    """
    return secrets.token_urlsafe(96)


Method = Literal["plain", "S256"]


def get_code_challenge(code: str, method: Method = "S256") -> str:
    """
    Generate the PKCE code challenge for the given code and method.

    :param code: The code to generate the challenge for.
    :param method: The method to use for generating the challenge. Either `plain` or `S256`.
    """
    if method == "plain":
        return code

    if method == "S256":
        hasher = hashlib.sha256()
        hasher.update(code.encode("ascii"))
        digest = hasher.digest()
        b64_digest = base64.urlsafe_b64encode(digest).decode("ascii")
        return b64_digest[:-1]  # Remove the padding "=" at the end
