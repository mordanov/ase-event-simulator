"""
JITR (Just-In-Time Registration) Lambda handler.

Triggered by AWS IoT Core when a device with a cert signed by the registered CA
connects for the first time (certificateStatus = PENDING_ACTIVATION).

Flow:
  1. Extract device_id from the certificate CN
  2. Activate the certificate
  3. Create IoT Thing  (name = device_id)
  4. Ensure device policy exists
  5. Attach policy → certificate
  6. Attach certificate → Thing

The device can then reconnect successfully and start publishing telemetry.
"""
import boto3
import json
import logging
import os

from cryptography import x509

logger = logging.getLogger()
logger.setLevel(logging.INFO)

iot = boto3.client("iot")

POLICY_NAME  = os.environ["POLICY_NAME"]
THING_TYPE   = os.environ["THING_TYPE"]
TOPIC_PREFIX = os.environ.get("TOPIC_PREFIX", "health/telemetry")


# ─── Entry point ──────────────────────────────────────────────────────────────

def handler(event, context):
    logger.info("JITR event received: %s", json.dumps(event))

    cert_id = event.get("certificateId")
    if not cert_id:
        logger.error("No certificateId in event")
        return {"status": "error", "reason": "missing certificateId"}

    # Fetch certificate details from IoT Core
    desc     = iot.describe_certificate(certificateId=cert_id)["certificateDescription"]
    cert_arn = desc["certificateArn"]
    cert_pem = desc["certificatePem"]

    device_id = _extract_cn(cert_pem)
    if not device_id:
        logger.error("Could not extract CN from certificate %s — aborting", cert_id)
        return {"status": "error", "reason": "CN not found in certificate"}

    logger.info("Registering device_id=%s  cert_id=%s", device_id, cert_id)

    # 1. Activate certificate
    iot.update_certificate(certificateId=cert_id, newStatus="ACTIVE")
    logger.info("Certificate activated: %s", cert_id)

    # 2. Create Thing (idempotent)
    try:
        iot.create_thing(thingName=device_id, thingTypeName=THING_TYPE)
        logger.info("Thing created: %s", device_id)
    except iot.exceptions.ResourceAlreadyExistsException:
        logger.info("Thing already exists: %s", device_id)

    # 3. Ensure device policy exists
    _ensure_policy()

    # 4. Attach policy to certificate
    iot.attach_policy(policyName=POLICY_NAME, target=cert_arn)
    logger.info("Policy %s attached to cert %s", POLICY_NAME, cert_id)

    # 5. Attach certificate to Thing
    iot.attach_thing_principal(thingName=device_id, principal=cert_arn)
    logger.info("Cert %s attached to Thing %s", cert_id, device_id)

    logger.info("JITR complete: device_id=%s", device_id)
    return {"status": "registered", "device_id": device_id, "cert_id": cert_id}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _extract_cn(cert_pem: str) -> str:
    """Return the Common Name from a PEM-encoded X.509 certificate."""
    cert  = x509.load_pem_x509_certificate(cert_pem.encode())
    attrs = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    return attrs[0].value if attrs else ""


def _ensure_policy() -> None:
    """Create the IoT device policy if it doesn't already exist."""
    session = boto3.session.Session()
    region  = session.region_name
    account = boto3.client("sts").get_caller_identity()["Account"]
    base    = f"arn:aws:iot:{region}:{account}"

    # Each device may only connect/publish/subscribe under its own Thing name.
    policy_doc = json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "iot:Connect",
                "Resource": f"{base}:client/${{iot:Connection.Thing.ThingName}}",
            },
            {
                "Effect": "Allow",
                "Action": ["iot:Publish", "iot:Receive"],
                "Resource": f"{base}:topic/{TOPIC_PREFIX}/${{iot:Connection.Thing.ThingName}}",
            },
            {
                "Effect": "Allow",
                "Action": "iot:Subscribe",
                "Resource": f"{base}:topicfilter/{TOPIC_PREFIX}/${{iot:Connection.Thing.ThingName}}",
            },
        ],
    })

    try:
        iot.create_policy(policyName=POLICY_NAME, policyDocument=policy_doc)
        logger.info("IoT policy created: %s", POLICY_NAME)
    except iot.exceptions.ResourceAlreadyExistsException:
        logger.debug("IoT policy already exists: %s", POLICY_NAME)
