#!/usr/bin/env bash
# =============================================================================
# Health Telemetry Simulator — AWS Bootstrap
#
# Deploys the full AWS infrastructure in dependency order:
#   00-buckets  → S3 deployment + frontend buckets
#   01-jitr     → IoT CA registration + JITR Lambda
#   02-platform → VPC, ECR, ECS Fargate, RDS PostgreSQL, ALB
#   03-acm      → ACM TLS certificate (always deployed to us-east-1)
#   04-cdn      → CloudFront distribution + Route 53 DNS record
#
# Prerequisites:
#   aws CLI, docker, python3, pip3, openssl, zip, jq
#
# Usage:
#   cd simulator/
#   ./aws/scripts/bootstrap.sh
#
#   # Override any default via env var:
#   REGION=eu-west-1 DOMAIN_NAME=sim.example.com HOSTED_ZONE_ID=Z123 \
#     PROFILE=my-profile ./aws/scripts/bootstrap.sh
#
#   # Skip steps you've already completed:
#   SKIP_JITR=1 SKIP_PLATFORM=1 ./aws/scripts/bootstrap.sh
# =============================================================================

set -euo pipefail

# ─── Directories ─────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AWS_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$AWS_DIR")"
CF_DIR="$AWS_DIR/cloudformation"
LAMBDA_DIR="$SCRIPT_DIR/lambda"

# ─── Configuration ────────────────────────────────────────────────────────────

PROFILE="${PROFILE:-${AWS_PROFILE:-}}"
[[ -n "$PROFILE" ]] && export AWS_PROFILE="$PROFILE"

REGION="${REGION:-${AWS_DEFAULT_REGION:-us-west-2}}"
PROJECT_NAME="${PROJECT_NAME:-health-simulator}"

# Stack names
STACK_BUCKETS="${STACK_BUCKETS:-${PROJECT_NAME}-buckets}"
STACK_JITR="${STACK_JITR:-${PROJECT_NAME}-jitr}"
STACK_NETWORK="${STACK_NETWORK:-${PROJECT_NAME}-network}"
STACK_PERSISTENT="${STACK_PERSISTENT:-${PROJECT_NAME}-persistent}"
STACK_PLATFORM="${STACK_PLATFORM:-${PROJECT_NAME}-platform}"
STACK_SERVICE="${STACK_SERVICE:-${PROJECT_NAME}-service}"
STACK_ACM="${STACK_ACM:-${PROJECT_NAME}-acm}"
STACK_CDN="${STACK_CDN:-${PROJECT_NAME}-cdn}"

# JITR config
POLICY_NAME="${POLICY_NAME:-HealthSimulatorDevicePolicy}"
THING_TYPE="${THING_TYPE:-HealthSimulatorDevice}"
TOPIC_PREFIX="${TOPIC_PREFIX:-health/telemetry}"

# CA certificate files
CA_CERT_FILE="${CA_CERT_FILE:-$PROJECT_ROOT/certificates/root-ca.pem}"
CA_KEY_FILE="${CA_KEY_FILE:-$PROJECT_ROOT/certificates/root-ca.key}"
APP_CONFIG_SECRET_ARN="${APP_CONFIG_SECRET_ARN:-}"

# CDN / DNS config (required for 06-acm + 07-cdn steps)
DOMAIN_NAME="${DOMAIN_NAME:-}"
HOSTED_ZONE_ID="${HOSTED_ZONE_ID:-}"

# Skip flags (set to 1 to skip a step that already completed successfully)
SKIP_BUCKETS="${SKIP_BUCKETS:-0}"
SKIP_JITR="${SKIP_JITR:-0}"
SKIP_NETWORK="${SKIP_NETWORK:-0}"
SKIP_PERSISTENT="${SKIP_PERSISTENT:-0}"
SKIP_PLATFORM="${SKIP_PLATFORM:-0}"
SKIP_SERVICE="${SKIP_SERVICE:-0}"
SKIP_ACM="${SKIP_ACM:-0}"
SKIP_CDN="${SKIP_CDN:-0}"
SKIP_DOCKER="${SKIP_DOCKER:-0}"

# ─── Colours ─────────────────────────────────────────────────────────────────

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }
step()    { echo -e "\n${CYAN}══ $* ══${NC}"; }
skip()    { echo -e "${YELLOW}[SKIP]${NC}  $*"; }

# ─── Preflight ────────────────────────────────────────────────────────────────

step "Preflight checks"

for cmd in aws docker python3 pip3 openssl zip jq; do
  command -v "$cmd" &>/dev/null || error "Required tool not found: $cmd"
done
success "All required tools present"

[[ -n "$PROFILE" ]] && info "AWS profile : $PROFILE"

aws sts get-caller-identity --region "$REGION" --query 'Account' --output text &>/dev/null \
  || error "AWS credentials invalid (profile: ${PROFILE:-default}, region: $REGION)"

ACCOUNT_ID=$(aws sts get-caller-identity --region "$REGION" --query 'Account' --output text)
success "AWS account: $ACCOUNT_ID  region: $REGION  profile: ${PROFILE:-default}"

# ─── Step 0 — S3 buckets ─────────────────────────────────────────────────────

if [[ "$SKIP_BUCKETS" == "1" ]]; then
  skip "00-buckets (SKIP_BUCKETS=1)"
  DEPLOY_BUCKET=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_BUCKETS" --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`DeployBucketName`].OutputValue' \
    --output text)
else
  step "S3 Buckets (00-buckets)"
  aws cloudformation deploy \
    --template-file "$CF_DIR/00-buckets.yaml" \
    --stack-name "$STACK_BUCKETS" \
    --region "$REGION" \
    --parameter-overrides ProjectName="$PROJECT_NAME" \
    --no-fail-on-empty-changeset
  success "Buckets stack deployed: $STACK_BUCKETS"

  DEPLOY_BUCKET=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_BUCKETS" --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`DeployBucketName`].OutputValue' \
    --output text)
fi

FRONTEND_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_BUCKETS" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`FrontendBucketName`].OutputValue' \
  --output text)
FRONTEND_BUCKET_ARN=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_BUCKETS" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`FrontendBucketArn`].OutputValue' \
  --output text)
success "Deploy bucket  : $DEPLOY_BUCKET"
success "Frontend bucket: $FRONTEND_BUCKET"

# ─── Step 1 — JITR (IoT CA + Lambda) ─────────────────────────────────────────

if [[ "$SKIP_JITR" == "1" ]]; then
  skip "01-jitr (SKIP_JITR=1)"
else
  step "IoT CA Registration + JITR Lambda (01-jitr)"

  [[ -f "$CA_CERT_FILE" ]] || error "CA certificate not found: $CA_CERT_FILE"
  [[ -f "$CA_KEY_FILE"  ]] || error "CA private key not found: $CA_KEY_FILE"

  openssl pkey -in "$CA_KEY_FILE" -noout 2>/dev/null \
    || error "CA_KEY_FILE is not a private key: $CA_KEY_FILE"

  # ── Check/register CA certificate ────────────────────────────────────────

  CA_CERT_PEM=$(cat "$CA_CERT_FILE")
  ALREADY_REGISTERED=false
  ALL_CA_IDS=$(aws iot list-ca-certificates \
    --region "$REGION" \
    --query 'certificates[].certificateId' \
    --output text 2>/dev/null || true)

  for ca_id in $ALL_CA_IDS; do
    REMOTE_PEM=$(aws iot describe-ca-certificate \
      --certificate-id "$ca_id" --region "$REGION" \
      --query 'certificateDescription.certificatePem' \
      --output text 2>/dev/null || true)
    if [[ "$REMOTE_PEM" == "$CA_CERT_PEM" ]]; then
      ALREADY_REGISTERED=true
      REGISTERED_CA_ID="$ca_id"
      break
    fi
  done

  if $ALREADY_REGISTERED; then
    success "CA already registered: $REGISTERED_CA_ID"
  else
    info "Generating proof-of-possession verification certificate..."
    REG_CODE=$(aws iot get-registration-code --region "$REGION" \
      --query 'registrationCode' --output text)

    TMPDIR_CERTS=$(mktemp -d)
    trap 'rm -rf "$TMPDIR_CERTS"' EXIT

    openssl genrsa -out "$TMPDIR_CERTS/verification.key" 2048 2>/dev/null
    openssl req -new \
      -key "$TMPDIR_CERTS/verification.key" \
      -subj "/CN=${REG_CODE}" \
      -out "$TMPDIR_CERTS/verification.csr" 2>/dev/null
    openssl x509 -req \
      -in  "$TMPDIR_CERTS/verification.csr" \
      -CA  "$CA_CERT_FILE" \
      -CAkey "$CA_KEY_FILE" \
      -CAcreateserial \
      -out "$TMPDIR_CERTS/verification.pem" \
      -days 1 -sha256 2>/dev/null

    REGISTERED_CA_ID=$(aws iot register-ca-certificate \
      --ca-certificate "file://${CA_CERT_FILE}" \
      --verification-cert "file://${TMPDIR_CERTS}/verification.pem" \
      --set-as-active \
      --allow-auto-registration \
      --region "$REGION" \
      --query 'certificateId' \
      --output text)
    success "CA registered: $REGISTERED_CA_ID"
    echo "$REGISTERED_CA_ID" > "$PROJECT_ROOT/aws-ca-id.txt"
  fi

  # ── Package + upload Lambda ───────────────────────────────────────────────

  BUILD_DIR="$SCRIPT_DIR/.build"
  ZIP_PATH="$SCRIPT_DIR/jitr_handler.zip"
  rm -rf "$BUILD_DIR" && mkdir -p "$BUILD_DIR"

  pip3 install cryptography --target "$BUILD_DIR" --quiet --no-compile --upgrade
  cp "$LAMBDA_DIR/jitr_handler.py" "$BUILD_DIR/"
  (cd "$BUILD_DIR" && zip -r "$ZIP_PATH" . \
    -x "*.pyc" -x "*/__pycache__/*" -x "*.dist-info/*") > /dev/null

  S3_KEY="lambda/jitr_handler.zip"
  aws s3 cp "$ZIP_PATH" "s3://${DEPLOY_BUCKET}/${S3_KEY}" --region "$REGION" > /dev/null
  success "Lambda package uploaded to s3://${DEPLOY_BUCKET}/${S3_KEY}"

  # ── Deploy JITR stack ─────────────────────────────────────────────────────

  aws cloudformation deploy \
    --template-file "$CF_DIR/01-jitr.yaml" \
    --stack-name "$STACK_JITR" \
    --region "$REGION" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides \
        LambdaS3Bucket="$DEPLOY_BUCKET" \
        LambdaS3Key="$S3_KEY" \
        PolicyName="$POLICY_NAME" \
        ThingTypeName="$THING_TYPE" \
        TelemetryTopicPrefix="$TOPIC_PREFIX" \
    --no-fail-on-empty-changeset
  success "JITR stack deployed: $STACK_JITR"
fi

# ─── CA cert Secrets Manager secret (created once; updated on re-runs) ───────
#
# The backend uses CA_CERT_PEM / CA_KEY_PEM to sign per-device X.509 certs for
# JITR.  ECS reads these from Secrets Manager at task launch (not from disk).

step "CA Cert — Secrets Manager"
CA_SECRET_NAME="/${PROJECT_NAME}/ca-cert"

CA_CERT_PEM_CONTENT=""
CA_KEY_PEM_CONTENT=""
[[ -f "$CA_CERT_FILE" ]] && CA_CERT_PEM_CONTENT=$(cat "$CA_CERT_FILE")
[[ -f "$CA_KEY_FILE"  ]] && CA_KEY_PEM_CONTENT=$(cat "$CA_KEY_FILE")

CA_SECRET_PAYLOAD=$(jq -n \
  --arg cert "$CA_CERT_PEM_CONTENT" \
  --arg key  "$CA_KEY_PEM_CONTENT" \
  '{"cert_pem":$cert,"key_pem":$key}')

if aws secretsmanager describe-secret \
    --secret-id "$CA_SECRET_NAME" --region "$REGION" &>/dev/null; then
  aws secretsmanager update-secret \
    --secret-id "$CA_SECRET_NAME" \
    --region "$REGION" \
    --secret-string "$CA_SECRET_PAYLOAD" > /dev/null
  success "CA cert secret updated: $CA_SECRET_NAME"
else
  aws secretsmanager create-secret \
    --name "$CA_SECRET_NAME" \
    --region "$REGION" \
    --description "CA certificate and private key for JITR device registration" \
    --secret-string "$CA_SECRET_PAYLOAD" > /dev/null
  success "CA cert secret created: $CA_SECRET_NAME"
fi

CA_CERT_SECRET_ARN=$(aws secretsmanager describe-secret \
  --secret-id "$CA_SECRET_NAME" --region "$REGION" \
  --query 'ARN' --output text)
success "CA cert secret ARN: $CA_CERT_SECRET_ARN"

# ─── IoT endpoint + default send-target values ───────────────────────────────

IOT_ENDPOINT=$(aws iot describe-endpoint \
  --endpoint-type iot:Data-ATS --region "$REGION" \
  --query 'endpointAddress' --output text 2>/dev/null || echo "")
[[ "$IOT_ENDPOINT" == "None" ]] && IOT_ENDPOINT=""

DEFAULT_HTTP_ENDPOINTS_VAL=""
DEFAULT_MQTT_URL_VAL=""
if [[ -n "$IOT_ENDPOINT" ]]; then
  DEFAULT_HTTP_ENDPOINTS_VAL="aws-iot::https://${IOT_ENDPOINT}:8443/topics/${TOPIC_PREFIX}?qos=1"
  DEFAULT_MQTT_URL_VAL="mqtt://${IOT_ENDPOINT}"
fi

# ─── Step 2 — Network (VPC, subnets, security groups) ────────────────────────

if [[ "$SKIP_NETWORK" == "1" ]]; then
  skip "02-network (SKIP_NETWORK=1)"
else
  step "Network — VPC / subnets / security groups (02-network)"
  aws cloudformation deploy \
    --template-file "$CF_DIR/02-network.yaml" \
    --stack-name "$STACK_NETWORK" \
    --region "$REGION" \
    --parameter-overrides ProjectName="$PROJECT_NAME" \
    --no-fail-on-empty-changeset
  success "Network stack deployed: $STACK_NETWORK"
fi

NET() { aws cloudformation describe-stacks --stack-name "$STACK_NETWORK" --region "$REGION" \
          --query "Stacks[0].Outputs[?OutputKey==\`$1\`].OutputValue" --output text; }
VPC_ID=$(NET VPCId)
PUBLIC_SUBNET_1=$(NET PublicSubnet1Id)
PUBLIC_SUBNET_2=$(NET PublicSubnet2Id)
PRIVATE_SUBNET_1=$(NET PrivateSubnet1Id)
PRIVATE_SUBNET_2=$(NET PrivateSubnet2Id)
ALB_SG=$(NET ALBSecurityGroupId)
ECS_SG=$(NET ECSSecurityGroupId)
EFS_SG=$(NET EFSSecurityGroupId)

# ─── Step 3 — Persistent resources (ECR, EFS) ────────────────────────────────

if [[ "$SKIP_PERSISTENT" == "1" ]]; then
  skip "03-persistent (SKIP_PERSISTENT=1)"
else
  step "Persistent resources — ECR + EFS (03-persistent)"
  aws cloudformation deploy \
    --template-file "$CF_DIR/03-persistent.yaml" \
    --stack-name "$STACK_PERSISTENT" \
    --region "$REGION" \
    --parameter-overrides \
        ProjectName="$PROJECT_NAME" \
        PrivateSubnet1Id="$PRIVATE_SUBNET_1" \
        PrivateSubnet2Id="$PRIVATE_SUBNET_2" \
        EFSSecurityGroupId="$EFS_SG" \
    --no-fail-on-empty-changeset
  success "Persistent stack deployed: $STACK_PERSISTENT"
fi

PERS() { aws cloudformation describe-stacks --stack-name "$STACK_PERSISTENT" --region "$REGION" \
           --query "Stacks[0].Outputs[?OutputKey==\`$1\`].OutputValue" --output text; }
ECR_URI=$(PERS ECRRepositoryUri)
EFS_FS_ID=$(PERS EFSFileSystemId)
EFS_AP_ID=$(PERS EFSAccessPointId)
success "ECR repository : $ECR_URI"

# ─── Step 4 — Platform (ALB, ECS cluster, task definition) ───────────────────

if [[ "$SKIP_PLATFORM" == "1" ]]; then
  skip "04-platform (SKIP_PLATFORM=1)"
else
  step "Platform — ALB / ECS cluster / task definition (04-platform)"
  info "This step may take a few minutes..."

  # Handle ROLLBACK_COMPLETE — stack must be deleted before redeployment
  STACK_STATUS=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_PLATFORM" --region "$REGION" \
    --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "DOES_NOT_EXIST")
  if [[ "$STACK_STATUS" == "ROLLBACK_COMPLETE" ]]; then
    warn "Stack $STACK_PLATFORM is in ROLLBACK_COMPLETE — deleting before redeployment"
    aws cloudformation delete-stack --stack-name "$STACK_PLATFORM" --region "$REGION"
    aws cloudformation wait stack-delete-complete \
      --stack-name "$STACK_PLATFORM" --region "$REGION"
    success "Stack deleted — redeploying from scratch"
  fi

  # Preserve BackendImage on re-runs so a real image is not reset to PLACEHOLDER.
  # AWS CLI --output text returns "None" for null/missing values.
  EXISTING_IMAGE=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_PLATFORM" --region "$REGION" \
    --query 'Stacks[0].Parameters[?ParameterKey==`BackendImage`].ParameterValue' \
    --output text 2>/dev/null || true)
  [[ "$EXISTING_IMAGE" == "None" || "$EXISTING_IMAGE" == "PLACEHOLDER" ]] && EXISTING_IMAGE=""
  BACKEND_IMAGE_PARAM="${EXISTING_IMAGE:-PLACEHOLDER}"

  aws cloudformation deploy \
    --template-file "$CF_DIR/04-platform.yaml" \
    --stack-name "$STACK_PLATFORM" \
    --region "$REGION" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides \
        ProjectName="$PROJECT_NAME" \
        BackendImage="$BACKEND_IMAGE_PARAM" \
        ECRRepositoryUri="$ECR_URI" \
        EFSFileSystemId="$EFS_FS_ID" \
        EFSAccessPointId="$EFS_AP_ID" \
        VPCId="$VPC_ID" \
        PublicSubnet1Id="$PUBLIC_SUBNET_1" \
        PublicSubnet2Id="$PUBLIC_SUBNET_2" \
        ALBSecurityGroupId="$ALB_SG" \
        ECSSecurityGroupId="$ECS_SG" \
        CaCertSecretArn="$CA_CERT_SECRET_ARN" \
        AppConfigSecretArn="$APP_CONFIG_SECRET_ARN" \
        DefaultHttpEndpoints="$DEFAULT_HTTP_ENDPOINTS_VAL" \
        DefaultMqttBrokerUrl="$DEFAULT_MQTT_URL_VAL" \
        DefaultMqttTopic="$TOPIC_PREFIX" \
    --no-fail-on-empty-changeset
  success "Platform stack deployed: $STACK_PLATFORM"
fi

PLAT() { aws cloudformation describe-stacks --stack-name "$STACK_PLATFORM" --region "$REGION" \
           --query "Stacks[0].Outputs[?OutputKey==\`$1\`].OutputValue" --output text; }
ECS_CLUSTER=$(PLAT ECSClusterName)
ALB_DNS=$(PLAT ALBDNSName)
TASK_DEF_FAMILY=$(PLAT BackendTaskDefinitionFamily)
TARGET_GROUP_ARN=$(PLAT BackendTargetGroupArn)

# ─── Step 5 — Docker build + push ────────────────────────────────────────────

if [[ "$SKIP_DOCKER" == "1" ]]; then
  skip "Docker build+push (SKIP_DOCKER=1)"
else
  step "Backend Docker image — build and push to ECR"

  [[ -z "$ECR_URI" || "$ECR_URI" == "None" ]] && \
    error "ECR_URI is empty — persistent stack may not be deployed yet"

  aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin \
        "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

  IMAGE_TAG="${IMAGE_TAG:-latest}"
  FULL_IMAGE="${ECR_URI}:${IMAGE_TAG}"

  info "Building $FULL_IMAGE ..."
  docker build \
    --platform linux/amd64 \
    -t "$FULL_IMAGE" \
    "$PROJECT_ROOT/backend"

  info "Pushing $FULL_IMAGE ..."
  docker push "$FULL_IMAGE"
  success "Backend image pushed: $FULL_IMAGE"

  # Update the platform stack with the real image URI so CloudFormation owns
  # the task definition revision.
  info "Updating platform stack with new image..."
  aws cloudformation deploy \
    --template-file "$CF_DIR/04-platform.yaml" \
    --stack-name "$STACK_PLATFORM" \
    --region "$REGION" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides \
        BackendImage="$FULL_IMAGE" \
        CaCertSecretArn="$CA_CERT_SECRET_ARN" \
        DefaultHttpEndpoints="$DEFAULT_HTTP_ENDPOINTS_VAL" \
        DefaultMqttBrokerUrl="$DEFAULT_MQTT_URL_VAL" \
        DefaultMqttTopic="$TOPIC_PREFIX" \
    --no-fail-on-empty-changeset
  success "Platform stack updated: BackendImage=$FULL_IMAGE"
fi

# ─── Step 6 — ECS service ────────────────────────────────────────────────────

if [[ "$SKIP_SERVICE" == "1" ]]; then
  skip "05-service (SKIP_SERVICE=1)"
else
  step "ECS service (05-service)"
  aws cloudformation deploy \
    --template-file "$CF_DIR/05-service.yaml" \
    --stack-name "$STACK_SERVICE" \
    --region "$REGION" \
    --parameter-overrides \
        ProjectName="$PROJECT_NAME" \
        ECSClusterName="$ECS_CLUSTER" \
        BackendTaskDefinitionFamily="$TASK_DEF_FAMILY" \
        BackendTargetGroupArn="$TARGET_GROUP_ARN" \
        PublicSubnet1Id="$PUBLIC_SUBNET_1" \
        PublicSubnet2Id="$PUBLIC_SUBNET_2" \
        ECSSecurityGroupId="$ECS_SG" \
    --no-fail-on-empty-changeset
  success "Service stack deployed: $STACK_SERVICE"
fi

ECS_SERVICE=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_SERVICE" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`ECSServiceName`].OutputValue' \
  --output text 2>/dev/null || true)

# Force a redeployment whenever the platform stack or docker image was updated
# so the running task picks up any new env vars or a new image.
if [[ ("$SKIP_DOCKER" != "1" || "$SKIP_PLATFORM" != "1") && -n "$ECS_CLUSTER" && -n "$ECS_SERVICE" ]]; then
  aws ecs update-service \
    --cluster "$ECS_CLUSTER" \
    --service "$ECS_SERVICE" \
    --region "$REGION" \
    --force-new-deployment > /dev/null
  success "ECS deployment triggered"
fi

# ─── Step 7 — ACM certificate (us-east-1) ────────────────────────────────────

if [[ "$SKIP_ACM" == "1" ]]; then
  skip "06-acm (SKIP_ACM=1)"
elif [[ -z "$DOMAIN_NAME" || -z "$HOSTED_ZONE_ID" ]]; then
  warn "DOMAIN_NAME or HOSTED_ZONE_ID not set — skipping ACM + CDN steps."
  warn "Re-run with: DOMAIN_NAME=your.domain.com HOSTED_ZONE_ID=Z... SKIP_BUCKETS=1 SKIP_JITR=1 SKIP_NETWORK=1 SKIP_PERSISTENT=1 SKIP_PLATFORM=1 SKIP_DOCKER=1 SKIP_SERVICE=1 ./aws/scripts/bootstrap.sh"
  SKIP_ACM=1
  SKIP_CDN=1
else
  step "ACM Certificate — us-east-1 (06-acm)"
  aws cloudformation deploy \
    --template-file "$CF_DIR/06-acm.yaml" \
    --stack-name "$STACK_ACM" \
    --region us-east-1 \
    --parameter-overrides \
        DomainName="$DOMAIN_NAME" \
        HostedZoneId="$HOSTED_ZONE_ID" \
    --no-fail-on-empty-changeset
  success "ACM stack deployed: $STACK_ACM (us-east-1)"
fi

# ─── Step 8 — CloudFront + Route 53 ──────────────────────────────────────────

if [[ "${SKIP_CDN:-0}" == "1" ]]; then
  skip "07-cdn (SKIP_CDN=1 or DOMAIN_NAME not set)"
else
  step "CloudFront + Route 53 (07-cdn)"

  CERT_ARN=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_ACM" --region us-east-1 \
    --query 'Stacks[0].Outputs[?OutputKey==`CertificateArn`].OutputValue' \
    --output text)

  aws cloudformation deploy \
    --template-file "$CF_DIR/07-cdn.yaml" \
    --stack-name "$STACK_CDN" \
    --region "$REGION" \
    --parameter-overrides \
        DomainName="$DOMAIN_NAME" \
        HostedZoneId="$HOSTED_ZONE_ID" \
        CertificateArn="$CERT_ARN" \
        BackendAlbDns="$ALB_DNS" \
        FrontendBucketName="$FRONTEND_BUCKET" \
        FrontendBucketArn="$FRONTEND_BUCKET_ARN" \
    --no-fail-on-empty-changeset
  success "CDN stack deployed: $STACK_CDN"

  CF_DIST_ID=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_CDN" --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontDistributionId`].OutputValue' \
    --output text)

  # ── Build frontend ────────────────────────────────────────────────────────
  # Runs npm run build so the production bundle uses relative API paths
  # (VITE_API_BASE_URL is empty in .env.production → CloudFront routes /api/*).
  # Skip with SKIP_FRONTEND_BUILD=1 if you already have a fresh dist/.
  SKIP_FRONTEND_BUILD="${SKIP_FRONTEND_BUILD:-0}"
  FRONTEND_DIST="$PROJECT_ROOT/frontend/dist"
  if [[ "$SKIP_FRONTEND_BUILD" == "1" ]]; then
    skip "Frontend build (SKIP_FRONTEND_BUILD=1)"
    [[ -d "$FRONTEND_DIST" ]] || warn "frontend/dist/ not found — upload may be empty"
  else
    info "Building frontend..."
    (cd "$PROJECT_ROOT/frontend" && npm install --silent && npm run build)
    success "Frontend built → $FRONTEND_DIST"
  fi

  # ── Upload frontend build (if dist/ exists) ───────────────────────────────
  if [[ -d "$FRONTEND_DIST" ]]; then
    info "Uploading frontend build to s3://${FRONTEND_BUCKET}/ ..."
    SYNC_OUTPUT=$(aws s3 sync "$FRONTEND_DIST/" "s3://${FRONTEND_BUCKET}/" \
      --region "$REGION" \
      --delete \
      --cache-control "public,max-age=31536000,immutable" \
      --exclude "index.html" \
      --exclude "manifest.json")
    IDX_OUTPUT=$(aws s3 cp "$FRONTEND_DIST/index.html" "s3://${FRONTEND_BUCKET}/index.html" \
      --region "$REGION" \
      --cache-control "no-cache,no-store,must-revalidate")
    aws s3 cp "$FRONTEND_DIST/manifest.json" "s3://${FRONTEND_BUCKET}/manifest.json" \
      --region "$REGION" \
      --cache-control "no-cache,no-store,must-revalidate" 2>/dev/null || true
    if [[ -n "$SYNC_OUTPUT" || "$IDX_OUTPUT" == *"upload"* ]]; then
      aws cloudfront create-invalidation \
        --distribution-id "$CF_DIST_ID" \
        --paths "/*" > /dev/null
      success "Frontend deployed + CloudFront cache invalidated"
    else
      success "Frontend S3 already up to date — no invalidation needed"
    fi
  else
    warn "frontend/dist/ not found — run 'cd frontend && npm run build' then re-run with SKIP_BUCKETS=1 SKIP_JITR=1 SKIP_NETWORK=1 SKIP_PERSISTENT=1 SKIP_PLATFORM=1 SKIP_DOCKER=1 SKIP_SERVICE=1 SKIP_ACM=1"
  fi
fi

# ─── Summary ─────────────────────────────────────────────────────────────────

step "Done"

IOT_ENDPOINT=$(aws iot describe-endpoint \
  --endpoint-type iot:Data-ATS --region "$REGION" \
  --query 'endpointAddress' --output text 2>/dev/null || echo "N/A")

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN} Health Telemetry Simulator infrastructure is ready${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  AWS profile      : ${CYAN}${PROFILE:-default}${NC}"
echo -e "  Region           : ${CYAN}${REGION}${NC}"
echo -e "  ECR repository   : ${CYAN}${ECR_URI:-N/A}${NC}"
echo -e "  ECS cluster      : ${CYAN}${ECS_CLUSTER:-N/A}${NC}"
echo -e "  ALB endpoint     : ${CYAN}http://${ALB_DNS:-N/A}${NC}"
echo -e "  IoT MQTT endpoint: ${CYAN}mqtt://${IOT_ENDPOINT}${NC}"
[[ -n "$DOMAIN_NAME" ]] && echo -e "  Site URL         : ${CYAN}https://${DOMAIN_NAME}${NC}"
echo ""
echo -e "  GitHub Actions secrets to configure:"
echo -e "  ${YELLOW}AWS_REGION=${REGION}${NC}"
echo -e "  ${YELLOW}ECR_REPOSITORY=${ECR_URI:-N/A}${NC}"
echo -e "  ${YELLOW}ECS_CLUSTER=${ECS_CLUSTER:-N/A}${NC}"
echo -e "  ${YELLOW}ECS_SERVICE=${ECS_SERVICE:-N/A}${NC}"
echo -e "  ${YELLOW}FRONTEND_BUCKET=${FRONTEND_BUCKET:-N/A}${NC}"
[[ "${SKIP_CDN:-0}" != "1" && -n "${CF_DIST_ID:-}" ]] && \
  echo -e "  ${YELLOW}CLOUDFRONT_DISTRIBUTION_ID=${CF_DIST_ID}${NC}"
echo ""
