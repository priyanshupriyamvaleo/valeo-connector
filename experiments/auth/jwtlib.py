"""RS256 JSON Web Tokens with no third-party packages.

Real identity providers (Cognito, Auth0, Entra) sign tokens with RS256 and publish the
matching public key at a JWKS endpoint. Verifiers fetch that key and check the signature.
This module does the same thing, so the experiment exercises the real code path rather
than a symmetric shortcut that would hide work you still have to do later.

Signing needs a private key operation, which is delegated to the `openssl` binary that
ships with macOS. Verification is a public key operation - which is nothing more than
modular exponentiation, and Python's built-in pow() does that natively. So the verifying
side, the side you will actually ship inside the MCP server, has zero dependencies.
"""

import base64
import hashlib
import hmac
import json
import subprocess
import time
import urllib.request

# DER prefix for a PKCS#1 v1.5 signature over a SHA-256 digest (RFC 8017, section 9.2).
SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def b64url_encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(text):
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def int_to_b64url(value):
    return b64url_encode(value.to_bytes((value.bit_length() + 7) // 8, "big"))


def b64url_to_int(text):
    return int.from_bytes(b64url_decode(text), "big")


# --------------------------------------------------------------------------- keys

def generate_key(path):
    """Create a 2048-bit RSA private key if one is not already there."""
    subprocess.run(["openssl", "genrsa", "-out", path, "2048"],
                   check=True, capture_output=True)


def public_numbers(key_path):
    """Pull (modulus, exponent) out of a private key. 65537 is openssl's default exponent."""
    out = subprocess.run(["openssl", "rsa", "-in", key_path, "-noout", "-modulus"],
                         check=True, capture_output=True, text=True).stdout
    return int(out.strip().split("=", 1)[1], 16), 65537


def jwks_document(key_path, kid):
    modulus, exponent = public_numbers(key_path)
    return {"keys": [{
        "kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid,
        "n": int_to_b64url(modulus), "e": int_to_b64url(exponent),
    }]}


# --------------------------------------------------------------------------- sign

def sign(claims, key_path, kid):
    header = {"alg": "RS256", "typ": "JWT", "kid": kid}
    signing_input = "%s.%s" % (
        b64url_encode(json.dumps(header, separators=(",", ":")).encode()),
        b64url_encode(json.dumps(claims, separators=(",", ":")).encode()),
    )
    signature = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", key_path],
        input=signing_input.encode(), check=True, capture_output=True).stdout
    return "%s.%s" % (signing_input, b64url_encode(signature))


# ------------------------------------------------------------------------- verify

class InvalidToken(Exception):
    """Raised for every verification failure, with a reason safe to log."""


_JWKS_CACHE = {}


def fetch_jwks(jwks_url, max_age=300):
    cached = _JWKS_CACHE.get(jwks_url)
    if cached and time.time() - cached[0] < max_age:
        return cached[1]
    with urllib.request.urlopen(jwks_url, timeout=10) as response:
        document = json.load(response)
    _JWKS_CACHE[jwks_url] = (time.time(), document)
    return document


def _verify_signature(signing_input, signature, modulus, exponent):
    """PKCS#1 v1.5 verification: recover the padded digest and compare it to ours.

    pow(signature, e, n) is the entire public key operation. Everything else is checking
    that what comes back has the exact padded shape a correct signature would have.
    """
    size = (modulus.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    recovered = pow(int.from_bytes(signature, "big"), exponent, modulus).to_bytes(size, "big")
    digest = hashlib.sha256(signing_input).digest()
    padding_length = size - 3 - len(SHA256_DIGEST_INFO) - len(digest)
    if padding_length < 8:
        return False
    expected = (b"\x00\x01" + b"\xff" * padding_length + b"\x00"
                + SHA256_DIGEST_INFO + digest)
    return hmac.compare_digest(recovered, expected)


def verify(token, jwks_url, issuer, audience, required_scopes=()):
    """Verify a token and return its claims, or raise InvalidToken.

    Checks, in order: the token is well formed, the algorithm is the one we expect, the
    signature is genuine, it has not expired, it was issued by the right authorization
    server, it was minted for this resource, and it carries the scopes being asked for.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise InvalidToken("not a JWT")
    header_raw, claims_raw, signature_raw = parts

    try:
        header = json.loads(b64url_decode(header_raw))
        claims = json.loads(b64url_decode(claims_raw))
        signature = b64url_decode(signature_raw)
    except Exception:
        raise InvalidToken("malformed token")

    # Refuse "alg": "none" and algorithm substitution outright.
    if header.get("alg") != "RS256":
        raise InvalidToken("unexpected algorithm %r" % header.get("alg"))

    keys = fetch_jwks(jwks_url)["keys"]
    matching = [k for k in keys if k.get("kid") == header.get("kid")] or keys
    signing_input = ("%s.%s" % (header_raw, claims_raw)).encode()
    if not any(_verify_signature(signing_input, signature,
                                 b64url_to_int(k["n"]), b64url_to_int(k["e"]))
               for k in matching):
        raise InvalidToken("signature does not verify")

    now = time.time()
    if claims.get("exp", 0) <= now:
        raise InvalidToken("token expired")
    if claims.get("iss") != issuer:
        raise InvalidToken("wrong issuer")

    allowed = claims.get("aud")
    allowed = allowed if isinstance(allowed, list) else [allowed]
    if audience not in allowed:
        raise InvalidToken("token was not issued for this resource")

    granted = set((claims.get("scope") or "").split())
    missing = set(required_scopes) - granted
    if missing:
        raise InvalidToken("missing scope: %s" % ", ".join(sorted(missing)))

    return claims
