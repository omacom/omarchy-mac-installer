# SPDX-License-Identifier: MIT
"""Small synthetic fixtures only; no Apple archive or key is checked in."""

import base64
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import omarchy_aea as aea

# Generated independently with CryptoKit HPKE.Sender, scalar 1, empty info/aad,
# plaintext bytes(range(32)), P256_SHA256_AES_GCM_256. This is a synthetic key.
PEM = b"""-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAGhRANCAARrF9Hy4SxCR/i85uVjpEDydwN9gS3r
M6D0oTlF2JjClk/jQuL+Gn+bjufrSnwPnhYrzjNXazFezsu2QGg3v1H1
-----END PRIVATE KEY-----"""
ENC = bytes.fromhex(
    "043c1c4c9c8f6fa8be4023e9509c17705f7fdda70d73b1ad3557db48ab984190a"
    "482f259738925b089d6a82bf9ff4f3ac4261f005f99aac80d3801d3b3aa2e7490"
)
SEALED = bytes.fromhex(
    "092b73c49107b61346dde1ee30efa11c9322e59fd2b5548386abd26dabdb80b900"
    "f8a660bd99136cd0ff66e5313f9b55"
)
URL = "https://wkms-public.apple.com/fcs-keys/synthetic"


def header(fields=None):
    fields = fields if fields is not None else {
        b"com.apple.wkms.fcs-key-url": URL.encode(),
        b"com.apple.wkms.fcs-response": json.dumps({
            "enc-request": base64.b64encode(ENC).decode(),
            "wrapped-key": base64.b64encode(SEALED).decode(),
        }).encode(),
    }
    data = b""
    for name, value in fields.items():
        field = name + b"\0" + value
        data += struct.pack("<I", len(field) + 4) + field
    return b"AEA1\1\0\0\0" + struct.pack("<I", len(data)) + data


class AeaTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "synthetic.aea"
        self.source.write_bytes(header())
        self.output = self.root / "image"

    def test_cryptokit_fixture_unwraps_through_aea_metadata(self):
        with patch.object(aea, "_fetch_key", return_value=PEM) as fetch:
            self.assertEqual(aea.archive_key(self.source), bytes(range(32)))
        fetch.assert_called_once_with(URL)

    def test_cfrg_hpke_vector(self):
        # RFC 9180's CFRG test-vectors.json: mode 0, kem 16, kdf 1, aead 2,
        # first encryption (sequence 0). Includes nonempty info and aad.
        # https://github.com/cfrg/draft-irtf-cfrg-hpke/blob/master/test-vectors.json
        scalar = int("317f915db7bc629c48fe765587897e01e282d3e8445f79f27f65d031a88082b2", 16)
        encapsulated = bytes.fromhex(
            "04c06b4f6bebc7bb495cb797ab753f911aff80aefb86fd8b6fcc35525f3ab5f03e0b"
            "21bd31a86c6048af3cb2d98e0d3bf01da5cc4c39ff5370d331a4f1f7d5a4e0"
        )
        sealed = bytes.fromhex(
            "58c61a45059d0c5704560e9d88b564a8b63f1364b8d1fcb3c4c6ddc1d2917424"
            "65e902cd216f8908da49f8f96f"
        )
        self.assertEqual(aea._hpke_open(scalar, encapsulated, sealed,
                                      b"Ode on a Grecian Urn", b"Count-0"),
                         b"Beauty is truth, truth beauty")

    def test_aes256_gcm_known_answer(self):
        # NIST AES-256-GCM: zero key, 96-bit zero IV, one zero block, no aad.
        sealed = bytes.fromhex("cea7403d4d606b6e074ec5d3baf39d18d0d1c8a799996bf0265b98b5d48ab919")
        self.assertEqual(aea._gcm_open(bytes(32), bytes(12), sealed), bytes(16))

    def test_wrong_private_key_and_corrupt_tag_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "authentication"):
            aea._hpke_open(2, ENC, SEALED)
        with self.assertRaisesRegex(ValueError, "authentication"):
            aea._hpke_open(1, ENC, SEALED[:-1] + bytes([SEALED[-1] ^ 1]))

    def test_bad_points_and_scalars_are_rejected(self):
        for scalar, point in ((0, ENC), (aea._N, ENC), (1, bytes(65)),
                              (1, b"\4" + aea._P.to_bytes(32, "big") + ENC[33:])):
            with self.subTest(scalar=scalar, point=point), self.assertRaises(ValueError):
                aea._hpke_open(scalar, point, SEALED)

    def test_malformed_headers_are_rejected(self):
        valid = header()
        for data in (b"", b"AEA1", valid[:11], valid[:-1],
                     valid[:4] + b"\2" + valid[5:],
                     valid[:8] + struct.pack("<I", 1024 * 1024 + 1),
                     header({b"x": b"y"}) + b"not auth"):
            self.source.write_bytes(data)
            with self.subTest(data=data[:12]), self.assertRaises(ValueError):
                aea.archive_key(self.source)

    def test_trailing_or_duplicate_auth_fields_are_rejected(self):
        for blob in (b"x", struct.pack("<I", 3), struct.pack("<I", 8) + b"abcd",
                     2 * (struct.pack("<I", 7) + b"x\0y")):
            self.source.write_bytes(b"AEA1\1\0\0\0" + struct.pack("<I", len(blob)) + blob)
            with self.assertRaises(ValueError):
                aea._auth_data(self.source)

    def test_bad_pem_is_rejected(self):
        for pem in (b"bad key", PEM.replace(b"MIGH", b"!!!!"), PEM.replace(b"AAGh", b"AACh")):
            with self.subTest(pem=pem), self.assertRaises(ValueError):
                aea._private_scalar(pem)

    def test_fetch_failure_never_invokes_decrypt(self):
        with patch.object(aea, "_fetch_key", side_effect=urllib.error.URLError("offline")), \
             patch.object(aea.subprocess, "run") as run:
            with self.assertRaises(urllib.error.URLError):
                aea.decrypt_verified(self.source, self.output, bytes(48))
        run.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_bad_fetched_key_never_invokes_decrypt(self):
        with patch.object(aea, "_fetch_key", return_value=b"bad key"), \
             patch.object(aea.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                aea.decrypt_verified(self.source, self.output, bytes(48))
        run.assert_not_called()

    def _decrypt(self, digest, failure=False):
        def run(command, **kwargs):
            self.assertEqual(command[:2], ["/usr/bin/aea", "decrypt"])
            self.assertEqual(command[-1], "base64:" + base64.b64encode(bytes(range(32))).decode())
            Path(command[command.index("-o") + 1]).write_bytes(b"plain synthetic image")
            if failure:
                raise subprocess.CalledProcessError(1, command)
        with patch.object(aea, "_fetch_key", return_value=PEM), patch.object(aea.subprocess, "run", run):
            aea.decrypt_verified(self.source, self.output, digest)

    def test_verified_output_is_published(self):
        self._decrypt(hashlib.sha384(b"plain synthetic image").digest())
        self.assertEqual(self.output.read_bytes(), b"plain synthetic image")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["image", "synthetic.aea"])

    def test_wrong_digest_preserves_destination_and_removes_partial_output(self):
        self.output.write_bytes(b"existing")
        with self.assertRaises(aea.DigestMismatchError):
            self._decrypt(bytes(48))
        self.assertEqual(self.output.read_bytes(), b"existing")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["image", "synthetic.aea"])

    def test_aea_failure_removes_partial_output(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self._decrypt(bytes(48), failure=True)
        self.assertEqual(list(self.root.iterdir()), [self.source])

    def test_missing_manifest_digest_is_rejected_before_fetch(self):
        with patch.object(aea, "_fetch_key") as fetch:
            for digest in (None, b"", bytes(32)):
                with self.assertRaises(ValueError):
                    aea.decrypt_verified(self.source, self.output, digest)
        fetch.assert_not_called()

    def test_only_public_apple_key_urls_and_redirects_are_allowed(self):
        for url in ("http://wkms-public.apple.com/fcs-keys/x", "https://example.com/fcs-keys/x",
                    URL + "?x=y", URL.replace("/fcs-keys/", "/other/"),
                    URL.replace("apple.com", "apple.com.evil.invalid"),
                    URL.replace("https://", "https://user@")):
            with self.subTest(url=url), self.assertRaises(ValueError):
                aea._key_url(url)
        handler = aea._AppleRedirect()
        request = aea.urllib.request.Request(URL)
        with self.assertRaises(ValueError):
            handler.redirect_request(request, None, 301, "Moved", {}, "https://example.com/key")
        redirected = handler.redirect_request(request, None, 301, "Moved", {},
                                             "https://fcs-keys-pub-prod.cdn-apple.com/fcs-keys/synthetic")
        self.assertIn("cdn-apple.com", redirected.full_url)

    def test_fetch_checks_final_url_and_limits_response(self):
        for url, body in ((URL, PEM), ("https://example.com/key", PEM), (URL, bytes(16385))):
            response = io.BytesIO(body)
            response.geturl = lambda: url
            with patch.object(aea.urllib.request, "build_opener") as opener:
                opener.return_value.open.return_value = response
                if url == URL and body == PEM:
                    self.assertEqual(aea._fetch_key(URL), PEM)
                    self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 30)
                else:
                    with self.assertRaises(ValueError):
                        aea._fetch_key(URL)


if __name__ == "__main__":
    unittest.main()
