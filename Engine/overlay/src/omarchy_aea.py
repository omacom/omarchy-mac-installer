# SPDX-License-Identifier: MIT
"""Public FCS key unwrap for AEA1, followed by macOS aea and manifest verification.

Only RFC 9180 base mode (P-256 / HKDF-SHA256 / AES-256-GCM) is needed.
The small stdlib implementation handles public release keys, not secrets;
archive contents are decrypted by /usr/bin/aea, never by Python.
"""

import base64
import hashlib
import hmac
import json
import os
import struct
import subprocess
import tempfile
import urllib.parse
import urllib.request


class DigestMismatchError(ValueError):
    pass


def verify_sha384(path, digest):
    if not isinstance(digest, bytes) or len(digest) != 48:
        raise ValueError("missing SHA-384 BuildManifest digest")
    value = hashlib.sha384()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    if not hmac.compare_digest(value.digest(), digest):
        raise DigestMismatchError("decrypted SHA-384 does not match BuildManifest")


def _auth_data(path):
    with open(path, "rb") as source:
        header = source.read(12)
        if len(header) != 12 or header[:4] != b"AEA1":
            raise ValueError("invalid AEA1 header")
        if int.from_bytes(header[4:7], "little") != 1:
            raise ValueError("unsupported AEA profile")
        size = int.from_bytes(header[8:12], "little")
        if size > 1024 * 1024:
            raise ValueError("AEA auth data too large")
        data = source.read(size)
    if len(data) != size:
        raise ValueError("truncated AEA auth data")
    fields = {}
    while data:
        if len(data) < 4:
            raise ValueError("truncated AEA auth field")
        length = int.from_bytes(data[:4], "little")
        if not 4 < length <= len(data):
            raise ValueError("invalid AEA auth field length")
        name, separator, value = data[4:length].partition(b"\0")
        if not separator or name in fields:
            raise ValueError("invalid or duplicate AEA auth field")
        fields[name] = value
        data = data[length:]
    return fields


def _key_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in (
        "wkms-public.apple.com", "fcs-keys-pub-prod.cdn-apple.com"
    ) or parsed.username or parsed.password or parsed.port not in (None, 443)
        or not parsed.path.startswith("/fcs-keys/") or parsed.query or parsed.fragment):
        raise ValueError("invalid Apple public FCS key URL")
    return url


class _AppleRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return super().redirect_request(
            request, fp, code, message, headers, _key_url(newurl)
        )


def _fetch_key(url):
    opener = urllib.request.build_opener(_AppleRedirect)
    with opener.open(_key_url(url), timeout=30) as response:
        _key_url(response.geturl())
        pem = response.read(16385)
    if len(pem) > 16384:
        raise ValueError("FCS key response too large")
    return pem


def _der(data, tag):
    """Read one definite-length DER value, returning its contents and remainder."""
    if len(data) < 2 or data[0] != tag:
        raise ValueError("invalid FCS private key DER")
    size, offset = data[1], 2
    if size & 128:
        count = size & 127
        if not 1 <= count <= 4 or len(data) < 2 + count:
            raise ValueError("invalid FCS private key DER length")
        size = int.from_bytes(data[2:2 + count], "big")
        offset += count
    if offset + size > len(data):
        raise ValueError("truncated FCS private key DER")
    return data[offset:offset + size], data[offset + size:]


def _private_scalar(pem):
    lines = pem.strip().splitlines()
    if (not lines or lines[0] != b"-----BEGIN PRIVATE KEY-----"
        or lines[-1] != b"-----END PRIVATE KEY-----"):
        raise ValueError("expected a PKCS#8 FCS private key")
    sequence, tail = _der(base64.b64decode(b"".join(lines[1:-1]), validate=True), 0x30)
    version, sequence = _der(sequence, 2)
    algorithm, sequence = _der(sequence, 0x30)
    # id-ecPublicKey, prime256v1
    if tail or version != b"\0" or algorithm != bytes.fromhex(
        "06072a8648ce3d020106082a8648ce3d030107"
    ):
        raise ValueError("expected a P-256 PKCS#8 FCS private key")
    private, sequence = _der(sequence, 4)
    if sequence:
        raise ValueError("unexpected PKCS#8 fields")
    private, tail = _der(private, 0x30)
    version, private = _der(private, 2)
    scalar, private = _der(private, 4)
    value = int.from_bytes(scalar, "big")
    if tail or version != b"\1" or len(scalar) != 32 or not 0 < value < _N:
        raise ValueError("invalid P-256 private scalar")
    return value


_P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
_N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
_B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b
_G = (0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296,
      0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5)


def _add(left, right):
    if left is None:
        return right
    if right is None:
        return left
    x, y = left
    u, v = right
    if x == u and (y + v) % _P == 0:
        return None
    slope = ((3 * x * x - 3) * pow(2 * y, -1, _P) if left == right
             else (v - y) * pow(u - x, -1, _P)) % _P
    result = (slope * slope - x - u) % _P
    return result, (slope * (x - result) - y) % _P


def _multiply(scalar, point):
    result = None
    while scalar:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _serialize(point):
    return b"\4" + b"".join(value.to_bytes(32, "big") for value in point)


def _extract(suite, salt, label, value):
    return hmac.new(salt, b"HPKE-v1" + suite + label + value, hashlib.sha256).digest()


def _expand(suite, secret, label, info, size):
    # All outputs used here fit in one SHA-256 block.
    data = size.to_bytes(2, "big") + b"HPKE-v1" + suite + label + info
    return hmac.new(secret, data + b"\1", hashlib.sha256).digest()[:size]


# AES S-box (FIPS 197, table 4).
_SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16"
)


def _xtime(value):
    return ((value << 1) ^ (0x11b if value & 128 else 0)) & 255


def _round_keys(key):
    words = [list(key[offset:offset + 4]) for offset in range(0, 32, 4)]
    constant = 1
    for index in range(8, 60):
        word = words[-1][:]
        if index % 8 == 0:
            word = [_SBOX[value] for value in word[1:] + word[:1]]
            word[0] ^= constant
            constant = _xtime(constant)
        elif index % 8 == 4:
            word = [_SBOX[value] for value in word]
        words.append([a ^ b for a, b in zip(words[index - 8], word)])
    return [sum(words[index:index + 4], []) for index in range(0, 60, 4)]


def _aes(block, keys):
    state = list(block)
    for round_number, key in enumerate(keys):
        if round_number:
            state = [_SBOX[state[(index + 4 * (index % 4)) % 16]] for index in range(16)]
            if round_number != 14:
                for offset in range(0, 16, 4):
                    column = state[offset:offset + 4]
                    total = column[0] ^ column[1] ^ column[2] ^ column[3]
                    state[offset:offset + 4] = [
                        column[index] ^ total ^ _xtime(column[index] ^ column[(index + 1) % 4])
                        for index in range(4)
                    ]
        state = [a ^ b for a, b in zip(state, key)]
    return bytes(state)


def _gcm_open(key, nonce, sealed, aad=b""):
    if len(key) != 32 or len(nonce) != 12 or len(sealed) < 16:
        raise ValueError("invalid AES-256-GCM input")
    keys = _round_keys(key)
    ciphertext, tag = sealed[:-16], sealed[-16:]
    h = int.from_bytes(_aes(bytes(16), keys), "big")
    blocks = (aad + bytes(-len(aad) % 16) + ciphertext + bytes(-len(ciphertext) % 16)
              + struct.pack(">QQ", len(aad) * 8, len(ciphertext) * 8))
    accumulator = 0
    for offset in range(0, len(blocks), 16):
        value = accumulator ^ int.from_bytes(blocks[offset:offset + 16], "big")
        accumulator, factor = 0, h
        for bit in range(127, -1, -1):
            if value & (1 << bit):
                accumulator ^= factor
            factor = (factor >> 1) ^ ((0xe1 << 120) if factor & 1 else 0)
    mask = int.from_bytes(_aes(nonce + b"\0\0\0\1", keys), "big")
    if not hmac.compare_digest((accumulator ^ mask).to_bytes(16, "big"), tag):
        raise ValueError("FCS wrapped key authentication failed")
    output = bytearray()
    for offset in range(0, len(ciphertext), 16):
        stream = _aes(nonce + (2 + offset // 16).to_bytes(4, "big"), keys)
        output.extend(a ^ b for a, b in zip(ciphertext[offset:offset + 16], stream))
    return bytes(output)


def _hpke_open(scalar, encapsulated, sealed, info=b"", aad=b""):
    if len(encapsulated) != 65 or encapsulated[0] != 4 or not 0 < scalar < _N:
        raise ValueError("invalid HPKE P-256 input")
    x = int.from_bytes(encapsulated[1:33], "big")
    y = int.from_bytes(encapsulated[33:], "big")
    if x >= _P or y >= _P or (y * y - x * x * x + 3 * x - _B) % _P:
        raise ValueError("invalid HPKE P-256 point")
    shared_point = _multiply(scalar, (x, y))
    kem = b"KEM\x00\x10"
    secret = _extract(kem, b"", b"eae_prk", shared_point[0].to_bytes(32, "big"))
    shared = _expand(kem, secret, b"shared_secret", encapsulated + _serialize(_multiply(scalar, _G)), 32)
    suite = b"HPKE\x00\x10\x00\x01\x00\x02"
    context = (b"\0" + _extract(suite, b"", b"psk_id_hash", b"")
               + _extract(suite, b"", b"info_hash", info))
    secret = _extract(suite, shared, b"secret", b"")
    key = _expand(suite, secret, b"key", context, 32)
    nonce = _expand(suite, secret, b"base_nonce", context, 12)
    return _gcm_open(key, nonce, sealed, aad)


def archive_key(path):
    fields = _auth_data(path)
    try:
        response = json.loads(fields[b"com.apple.wkms.fcs-response"])
        encapsulated = base64.b64decode(response["enc-request"], validate=True)
        sealed = base64.b64decode(response["wrapped-key"], validate=True)
        url = fields[b"com.apple.wkms.fcs-key-url"].decode("ascii")
    except (KeyError, TypeError, UnicodeError) as error:
        raise ValueError("invalid AEA FCS key metadata") from error
    if len(sealed) != 48:
        raise ValueError("invalid FCS wrapped archive key length")
    return _hpke_open(_private_scalar(_fetch_key(url)), encapsulated, sealed)


def decrypt_verified(source, destination, digest):
    """Publish a decrypted image only after its SHA-384 matches BuildManifest."""
    if not isinstance(digest, bytes) or len(digest) != 48:
        raise ValueError("missing SHA-384 BuildManifest digest")
    key = archive_key(source)
    directory = os.path.dirname(os.path.abspath(destination))
    with tempfile.TemporaryDirectory(prefix="aea-", dir=directory) as temporary:
        output = os.path.join(temporary, "image")
        subprocess.run([
            "/usr/bin/aea", "decrypt", "-i", os.fspath(source), "-o", output,
            "-key-value", "base64:" + base64.b64encode(key).decode("ascii"),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        verify_sha384(output, digest)
        os.replace(output, destination)
