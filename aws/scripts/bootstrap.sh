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
STACK_PLATFORM="${STACK_PLATFORM:-${PROJECT_NAME}-platform}"
STACK_ACM="${STACK_ACM:-${PROJECT_NAME}-acm}"
STACK_CDN="${STACK_CDN:-${PROJECT_NAME}-cdn}"

# JITR config
POLICY_NAME="${POLICY_NAME:-HealthSimulatorDevicePolicy}"
THING_TYPE="${THING_TYPE:-HealthSimulatorDevice}"
TOPIC_PREFIX="${TOPIC_PREFIX:-health/telemetry}"

# CA certificate files
CA_CERT_FILE="${CA_CERT_FILE:-$PROJECT_ROOT/certificates/root-ca.pem}"
CA_KEY_FILE="${CA_KEY_FILE:-$PROJECT_ROOT/certificates/root-ca.key}"

# CDN / DNS config  (required for 03-acm + 04-cdn steps)
DOMAIN_NAME="${DOMAIN_NAME:-}"
HOSTED_ZONE_ID="${HOSTED_ZONE_ID:-}"

# Skip flags (set to 1 to skip a step that already completed successfully)
SKIP_BUCKETS="${SKIP_BUCKETS:-0}"
SKIP_JITR="${SKIP_JITR:-0}"
SKIP_PLATFORM="${SKIP_PLATFORM:-0}"
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

# ─── Step 2 — Platform (VPC, ECR, ECS, RDS, ALB) ─────────────────────────────

if [[ "$SKIP_PLATFORM" == "1" ]]; then
  skip "02-platform (SKIP_PLATFORM=1)"
else
  step "Platform — VPC / ECR / ECS / RDS / ALB (02-platform)"
  info "This step provisions ECS and EFS; it may take 5-10 minutes..."

  # ── ROLLBACK_COMPLETE recovery ──────────────────────────────────────────────
  # A stack in ROLLBACK_COMPLETE cannot be updated — it must be deleted first.
  # Resources with DeletionPolicy:Retain (ECR, EFS) are left behind by CF, which
  # causes "already exists" errors on the next create attempt. Clean them up here.
  STACK_STATUS=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_PLATFORM" --region "$REGION" \
    --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "DOES_NOT_EXIST")

  if [[ "$STACK_STATUS" == "ROLLBACK_COMPLETE" ]]; then
    warn "Stack $STACK_PLATFORM is in ROLLBACK_COMPLETE — cleaning up before redeployment"

    ECR_REPO_NAME="${PROJECT_NAME}-backend"
    if aws ecr describe-repositories \
        --repository-names "$ECR_REPO_NAME" --region "$REGION" &>/dev/null; then
      IMAGE_IDS=$(aws ecr list-images \
        --repository-name "$ECR_REPO_NAME" --region "$REGION" \
        --query 'imageIds[*]' --output json 2>/dev/null || echo "[]")
      IMAGE_COUNT=$(echo "$IMAGE_IDS" | jq 'length')
      if [[ "$IMAGE_COUNT" -gt 0 ]]; then
        info "Deleting $IMAGE_COUNT image(s) from ECR..."
        aws ecr batch-delete-image \
          --repository-name "$ECR_REPO_NAME" --region "$REGION" \
          --image-ids "$IMAGE_IDS" > /dev/null
      fi
      aws ecr delete-repository \
        --repository-name "$ECR_REPO_NAME" --region "$REGION" > /dev/null
      success "ECR repository removed"
    fi

    info "Deleting ROLLBACK_COMPLETE stack: $STACK_PLATFORM"
    aws cloudformation delete-stack --stack-name "$STACK_PLATFORM" --region "$REGION"
    aws cloudformation wait stack-delete-complete \
      --stack-name "$STACK_PLATFORM" --region "$REGION"
    success "Stack deleted — redeploying from scratch"
  fi

  # Preserve the current BackendImage parameter if the stack already exists,
  # so a re-run does not reset a real image URI back to PLACEHOLDER.
  # AWS CLI --output text returns the literal string "None" for null/missing
  # values, which would be passed as BackendImage=None on first run.
  EXISTING_IMAGE=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_PLATFORM" --region "$REGION" \
    --query 'Stacks[0].Parameters[?ParameterKey==`BackendImage`].ParameterValue' \
    --output text 2>/dev/null || true)
  [[ "$EXISTING_IMAGE" == "None" || "$EXISTING_IMAGE" == "PLACEHOLDER" ]] && EXISTING_IMAGE=""
  BACKEND_IMAGE_PARAM="${EXISTING_IMAGE:-PLACEHOLDER}"

  aws cloudformation deploy \
    --template-file "$CF_DIR/02-platform.yaml" \
    --stack-name "$STACK_PLATFORM" \
    --region "$REGION" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides \
        ProjectName="$PROJECT_NAME" \
        BackendImage="$BACKEND_IMAGE_PARAM" \
    --no-fail-on-empty-changeset
  success "Platform stack deployed: $STACK_PLATFORM"
fi

ECR_URI=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_PLATFORM" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`ECRRepositoryUri`].OutputValue' \
  --output text)
ECS_CLUSTER=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_PLATFORM" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`ECSClusterName`].OutputValue' \
  --output text)
ECS_SERVICE=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_PLATFORM" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`ECSServiceName`].OutputValue' \
  --output text)
ALB_DNS=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_PLATFORM" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`ALBDNSName`].OutputValue' \
  --output text)

# ─── Step 3 — Build + push backend Docker image ──────────────────────────────

if [[ "$SKIP_DOCKER" == "1" ]]; then
  skip "Docker build+push (SKIP_DOCKER=1)"
else
  step "Backend Docker image — build and push to ECR"

  [[ -z "$ECR_URI" || "$ECR_URI" == "None" ]] && \
    error "ECR_URI is empty — platform stack may not be deployed yet"

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

  # Only register a new task definition revision and redeploy if the image
  # stored in the current task definition differs from what we just pushed.
  TASK_DEF_FAMILY="${PROJECT_NAME}-backend"
  CURRENT_TASK_DEF=$(aws ecs describe-task-definition \
    --task-definition "$TASK_DEF_FAMILY" --region "$REGION" \
    --query 'taskDefinition' --output json)

  # Target the "backend" container by name — index 0 is the postgres sidecar
  CURRENT_IMAGE=$(echo "$CURRENT_TASK_DEF" \
    | jq -r '.containerDefinitions[] | select(.name == "backend") | .image')

  if [[ "$CURRENT_IMAGE" == "$FULL_IMAGE" ]]; then
    success "ECS task definition already uses $FULL_IMAGE — no update needed"
  else
    NEW_TASK_DEF=$(echo "$CURRENT_TASK_DEF" \
      | jq --arg img "$FULL_IMAGE" \
           '(.containerDefinitions[] | select(.name == "backend") | .image) = $img
            | del(.taskDefinitionArn, .revision, .status, .requiresAttributes,
                  .placementConstraints, .compatibilities, .registeredAt, .registeredBy)')

    NEW_REVISION=$(aws ecs register-task-definition \
      --region "$REGION" \
      --cli-input-json "$NEW_TASK_DEF" \
      --query 'taskDefinition.taskDefinitionArn' \
      --output text)
    success "New task definition: $NEW_REVISION"

    aws ecs update-service \
      --cluster "$ECS_CLUSTER" \
      --service "$ECS_SERVICE" \
      --task-definition "$NEW_REVISION" \
      --region "$REGION" \
      --force-new-deployment > /dev/null
    success "ECS service update triggered"
  fi
fi

# ─── Step 4 — ACM certificate (us-east-1) ────────────────────────────────────

if [[ "$SKIP_ACM" == "1" ]]; then
  skip "03-acm (SKIP_ACM=1)"
elif [[ -z "$DOMAIN_NAME" || -z "$HOSTED_ZONE_ID" ]]; then
  warn "DOMAIN_NAME or HOSTED_ZONE_ID not set — skipping ACM + CDN steps."
  warn "Re-run with: DOMAIN_NAME=your.domain.com HOSTED_ZONE_ID=Z... SKIP_BUCKETS=1 SKIP_JITR=1 SKIP_PLATFORM=1 SKIP_DOCKER=1 ./aws/scripts/bootstrap.sh"
  SKIP_ACM=1
  SKIP_CDN=1
else
  step "ACM Certificate — us-east-1 (03-acm)"
  # CloudFront requires certificates from us-east-1 regardless of app region
  aws cloudformation deploy \
    --template-file "$CF_DIR/03-acm.yaml" \
    --stack-name "$STACK_ACM" \
    --region us-east-1 \
    --parameter-overrides \
        DomainName="$DOMAIN_NAME" \
        HostedZoneId="$HOSTED_ZONE_ID" \
    --no-fail-on-empty-changeset
  success "ACM stack deployed: $STACK_ACM (us-east-1)"
fi

# ─── Step 5 — CloudFront + Route 53 ──────────────────────────────────────────

if [[ "${SKIP_CDN:-0}" == "1" ]]; then
  skip "04-cdn (SKIP_CDN=1 or DOMAIN_NAME not set)"
else
  step "CloudFront + Route 53 (04-cdn)"

  CERT_ARN=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_ACM" --region us-east-1 \
    --query 'Stacks[0].Outputs[?OutputKey==`CertificateArn`].OutputValue' \
    --output text)

  aws cloudformation deploy \
    --template-file "$CF_DIR/04-cdn.yaml" \
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

  # ── Upload frontend build (if dist/ exists) ───────────────────────────────
  FRONTEND_DIST="$PROJECT_ROOT/frontend/dist"
  if [[ -d "$FRONTEND_DIST" ]]; then
    info "Uploading frontend build to s3://${FRONTEND_BUCKET}/ ..."
    SYNC_OUTPUT=$(aws s3 sync "$FRONTEND_DIST/" "s3://${FRONTEND_BUCKET}/" \
      --region "$REGION" \
      --delete \
      --cache-control "public,max-age=31536000,immutable" \
      --exclude "index.html" \
      --exclude "manifest.json")
    # index.html and manifest.json must not be cached long-term
    IDX_OUTPUT=$(aws s3 cp "$FRONTEND_DIST/index.html" "s3://${FRONTEND_BUCKET}/index.html" \
      --region "$REGION" \
      --cache-control "no-cache,no-store,must-revalidate")
    aws s3 cp "$FRONTEND_DIST/manifest.json" "s3://${FRONTEND_BUCKET}/manifest.json" \
      --region "$REGION" \
      --cache-control "no-cache,no-store,must-revalidate" 2>/dev/null || true
    # Only invalidate if any files were actually uploaded or deleted
    if [[ -n "$SYNC_OUTPUT" || "$IDX_OUTPUT" == *"upload"* ]]; then
      aws cloudfront create-invalidation \
        --distribution-id "$CF_DIST_ID" \
        --paths "/*" > /dev/null
      success "Frontend deployed + CloudFront cache invalidated"
    else
      success "Frontend S3 already up to date — no invalidation needed"
    fi
  else
    warn "frontend/dist/ not found — run 'cd frontend && npm run build' then re-run with SKIP_BUCKETS=1 SKIP_JITR=1 SKIP_PLATFORM=1 SKIP_DOCKER=1 SKIP_ACM=1"
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
