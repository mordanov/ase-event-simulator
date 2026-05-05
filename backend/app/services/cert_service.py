"""
X.509 certificate management for simulated device authentication.

CA loading priority:
  1. CA_CERT_PEM / CA_KEY_PEM  — inline PEM in environment variables
  2. CA_CERT_FILE / CA_KEY_FILE — file paths
  3. Auto-generate a self-signed CA (useful for local testing without AWS IoT)

Device certs are RSA-2048, signed by the CA, valid for CERT_VALIDITY_DAYS days.
CN = device_id, so AWS IoT Core policies can reference it directly.
"""

from __future__ import annotations

import datetime
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.services.runtime_mode import is_cloud_mode

CERT_VALIDITY_DAYS = int(os.getenv("CERT_VALIDITY_DAYS", "365"))
CERT_COUNTRY = os.getenv("DEVICE_CERT_COUNTRY", "US")
CERT_ORG = os.getenv("DEVICE_CERT_ORG", "HealthSimulator")
logger = logging.getLogger(__name__)


# ─── CA loading ───────────────────────────────────────────────────────────────


def _pem_from_env_or_file(pem_var: str, file_var: str) -> str | None:
    raw = os.getenv(pem_var, "").strip()
    if raw:
        return raw.replace("\\n", "\n")

    path = os.getenv(file_var, "").strip()
    if path and Path(path).exists():
        return Path(path).read_text()

    return None


def _generate_self_signed_ca() -> tuple[x509.Certificate, RSAPrivateKey]:
    """Generate a throwaway self-signed CA for local testing."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, CERT_COUNTRY),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, CERT_ORG),
            x509.NameAttribute(NameOID.COMMON_NAME, f"{CERT_ORG} Self-Signed CA"),
        ]
    )
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert, key


@lru_cache(maxsize=1)
def load_ca() -> tuple[x509.Certificate, RSAPrivateKey]:
    """Return (ca_cert, ca_key). Cached for the process lifetime."""
    cert_pem = _pem_from_env_or_file("CA_CERT_PEM", "CA_CERT_FILE")
    key_pem = _pem_from_env_or_file("CA_KEY_PEM", "CA_KEY_FILE")

    if cert_pem and key_pem:
        ca_cert = x509.load_pem_x509_certificate(cert_pem.encode())
        ca_key = serialization.load_pem_private_key(key_pem.encode(), password=None)
        return ca_cert, ca_key  # type: ignore[return-value]

    if is_cloud_mode():
        raise RuntimeError(
            "Cloud mode requires CA_CERT_PEM/CA_KEY_PEM (for example from /health-simulator/ca-cert)."
        )

    # Fallback: auto-generate (not suitable for real AWS IoT)
    logger.warning(
        "CA_CERT_PEM/CA_CERT_FILE not set — using auto-generated self-signed CA. "
        "Register this CA with AWS IoT Core for real mTLS."
    )
    return _generate_self_signed_ca()


def ca_cert_pem() -> str:
    cert, _ = load_ca()
    return cert.public_bytes(serialization.Encoding.PEM).decode()


# ─── Device cert generation ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DeviceCert:
    cert_pem: str
    key_pem: str
    fingerprint: str  # hex SHA-256
    serial: str  # decimal serial number


def generate_device_cert(device_id: str) -> DeviceCert:
    """
    Generate RSA-2048 X.509 cert for *device_id*, signed by the CA.

    CN = device_id — required by AWS IoT Core Thing policies.
    The resulting cert + private key should be stored in the devices table.
    """
    ca_cert, ca_key = load_ca()

    # Device key pair
    dev_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, CERT_COUNTRY),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, CERT_ORG),
            x509.NameAttribute(NameOID.COMMON_NAME, device_id),
        ]
    )
    now = datetime.datetime.now(datetime.UTC)

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(dev_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=CERT_VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = dev_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()
    serial = str(cert.serial_number)

    return DeviceCert(cert_pem, key_pem, fingerprint, serial)
