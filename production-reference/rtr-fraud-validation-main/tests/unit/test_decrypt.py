"""Unit tests for app/crypto/decrypt_batchRTR.py."""
from __future__ import annotations

import io
import os

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.crypto.decrypt_batchRTR import decrypt_hybrid


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rsa_keypair():
    """Generate a real RSA-2048 key pair once per module."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    public_key = private_key.public_key()
    return private_key, public_key


@pytest.fixture(scope="module")
def private_key_pem(rsa_keypair) -> str:
    """Return the private key as a PEM string (newlines as literal \\n for the API)."""
    private_key, _ = rsa_keypair
    pem_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    # The decrypt_hybrid function calls .replace("\\n", "\n"), so we pass the
    # literal PEM string as-is (it already contains real newlines — the
    # replace is a no-op, which is fine).
    return pem_bytes.decode("utf-8")


def _make_encrypted_stream(plaintext: bytes, public_key) -> io.BytesIO:
    """Encrypt *plaintext* using the hybrid RSA+AES-GCM scheme expected by decrypt_hybrid."""
    # Generate AES-256 key and 12-byte IV
    aes_key = os.urandom(32)
    iv = os.urandom(12)

    # Encrypt plaintext with AES-GCM
    encryptor = Cipher(algorithms.AES(aes_key), modes.GCM(iv)).encryptor()
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()
    tag = encryptor.tag  # 16 bytes

    # Encrypt AES key with RSA-OAEP SHA-256
    encrypted_aes_key = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    # Build binary payload: [4-byte key length][encrypted AES key][IV][ciphertext][tag]
    key_length = len(encrypted_aes_key)
    payload = (
        key_length.to_bytes(4, byteorder="big")
        + encrypted_aes_key
        + iv
        + ciphertext
        + tag
    )
    return io.BytesIO(payload)


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestDecryptHybridHappyPath:
    def test_small_plaintext_decrypts_correctly(self, rsa_keypair, private_key_pem: str) -> None:
        _, public_key = rsa_keypair
        plaintext = b"Hello, World!"
        stream = _make_encrypted_stream(plaintext, public_key)
        result = decrypt_hybrid(stream, private_key_pem)
        assert result == plaintext

    def test_empty_plaintext(self, rsa_keypair, private_key_pem: str) -> None:
        _, public_key = rsa_keypair
        stream = _make_encrypted_stream(b"", public_key)
        result = decrypt_hybrid(stream, private_key_pem)
        assert result == b""

    def test_binary_plaintext(self, rsa_keypair, private_key_pem: str) -> None:
        _, public_key = rsa_keypair
        plaintext = bytes(range(256)) * 10
        stream = _make_encrypted_stream(plaintext, public_key)
        result = decrypt_hybrid(stream, private_key_pem)
        assert result == plaintext

    def test_single_chunk_less_than_64kb(self, rsa_keypair, private_key_pem: str) -> None:
        """Plaintext smaller than chunk_size (64 KB) — single iteration of the while loop."""
        _, public_key = rsa_keypair
        plaintext = b"A" * 1000
        stream = _make_encrypted_stream(plaintext, public_key)
        result = decrypt_hybrid(stream, private_key_pem)
        assert result == plaintext

    def test_multi_chunk_greater_than_64kb(self, rsa_keypair, private_key_pem: str) -> None:
        """Plaintext larger than chunk_size (64 KB) — exercises the multi-chunk loop."""
        _, public_key = rsa_keypair
        plaintext = b"B" * (65536 + 1024)  # slightly more than one 64 KB chunk
        stream = _make_encrypted_stream(plaintext, public_key)
        result = decrypt_hybrid(stream, private_key_pem)
        assert result == plaintext

    def test_exactly_two_chunks(self, rsa_keypair, private_key_pem: str) -> None:
        """Plaintext exactly 2 × 64 KB — exercises the loop boundary."""
        _, public_key = rsa_keypair
        plaintext = b"C" * (2 * 64 * 1024)
        stream = _make_encrypted_stream(plaintext, public_key)
        result = decrypt_hybrid(stream, private_key_pem)
        assert result == plaintext

    def test_pem_with_escaped_newlines(self, rsa_keypair) -> None:
        """Private key with literal '\\n' escape sequences — tests the replace() call."""
        private_key, public_key = rsa_keypair
        pem_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        # Replace real newlines with literal \n escape (as sent via env var / config)
        escaped_pem = pem_bytes.decode("utf-8").replace("\n", "\\n")
        plaintext = b"escape test"
        stream = _make_encrypted_stream(plaintext, public_key)
        result = decrypt_hybrid(stream, escaped_pem)
        assert result == plaintext


# ---------------------------------------------------------------------------
# Error-path tests
# ---------------------------------------------------------------------------


class TestDecryptHybridErrors:
    def test_corrupted_tag_raises_invalid_tag(self, rsa_keypair, private_key_pem: str) -> None:
        """Flipping a byte in the GCM tag causes authentication to fail."""
        _, public_key = rsa_keypair
        plaintext = b"sensitive data"
        stream = _make_encrypted_stream(plaintext, public_key)

        # Corrupt the last 16 bytes (the GCM tag)
        raw = bytearray(stream.getvalue())
        raw[-1] ^= 0xFF  # flip all bits in the last byte
        corrupted_stream = io.BytesIO(bytes(raw))

        with pytest.raises(InvalidTag):
            decrypt_hybrid(corrupted_stream, private_key_pem)

    def test_wrong_private_key_raises(self, rsa_keypair) -> None:
        """Encrypting with one key and decrypting with another raises an error."""
        _, public_key = rsa_keypair

        # Generate a different private key for decryption
        wrong_private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        wrong_pem = wrong_private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")

        plaintext = b"mismatch test"
        stream = _make_encrypted_stream(plaintext, public_key)

        with pytest.raises(Exception):  # ValueError or similar from RSA decryption
            decrypt_hybrid(stream, wrong_pem)

    def test_invalid_pem_raises(self, rsa_keypair) -> None:
        """Passing a non-PEM string raises an exception during key loading."""
        _, public_key = rsa_keypair
        plaintext = b"invalid key test"
        stream = _make_encrypted_stream(plaintext, public_key)

        with pytest.raises(Exception):
            decrypt_hybrid(stream, "NOT A VALID PEM STRING")
