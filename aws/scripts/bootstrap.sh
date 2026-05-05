#!/usr/bin/env bash
# =============================================================================
# Health Telemetry Simulator — AWS Bootstrap
#
# Deploys the full AWS infrastructure in dependency order:
#   00-buckets    → S3 deployment + frontend buckets
#   02-network    → VPC, subnets, security groups
#   03-persistent → ECR repository
#   04-platform   → ALB, ECS cluster + task definition (SQLite — no RDS needed)
#   05-service    → ECS Fargate service
#   06-acm        → ACM TLS certificate (always deployed to us-east-1)
#   07-cdn        → CloudFront distribution + Route 53 DNS record
#
# Ingestion pipeline is a separate Docker-on-EC2 PoC:
#   ingestion-pipeline.aleksandr-mordanov.click
#   (deployed independently — not managed by this script)
#
# Prerequisites:
#   aws CLI, docker, python3, npm, jq
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
#   SKIP_NETWORK=1 SKIP_PLATFORM=1 ./aws/scripts/bootstrap.sh
# =============================================================================

set -euo pipefail

# ─── Directories ─────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AWS_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$AWS_DIR")"
CF_DIR="$AWS_DIR/cloudformation"

# ─── Configuration ────────────────────────────────────────────────────────────

PROFILE="${PROFILE:-${AWS_PROFILE:-}}"
[[ -n "$PROFILE" ]] && export AWS_PROFILE="$PROFILE"

REGION="${REGION:-${AWS_DEFAULT_REGION:-us-west-2}}"
PROJECT_NAME="${PROJECT_NAME:-health-simulator}"

# Stack names
STACK_BUCKETS="${STACK_BUCKETS:-${PROJECT_NAME}-buckets}"
STACK_NETWORK="${STACK_NETWORK:-${PROJECT_NAME}-network}"
STACK_PERSISTENT="${STACK_PERSISTENT:-${PROJECT_NAME}-persistent}"
STACK_PLATFORM="${STACK_PLATFORM:-${PROJECT_NAME}-platform}"
STACK_SERVICE="${STACK_SERVICE:-${PROJECT_NAME}-service}"
STACK_ACM="${STACK_ACM:-${PROJECT_NAME}-acm}"
STACK_CDN="${STACK_CDN:-${PROJECT_NAME}-cdn}"

# Ingestion pipeline — Docker on EC2 (deployed separately)
INGESTION_PIPELINE_HOST="${INGESTION_PIPELINE_HOST:-ingestion-pipeline.aleksandr-mordanov.click}"
INGESTION_HTTP_ENDPOINT="${INGESTION_HTTP_ENDPOINT:-ingestion::http://${INGESTION_PIPELINE_HOST}:9000/ingest}"
INGESTION_MQTT_URL="${INGESTION_MQTT_URL:-mqtt://${INGESTION_PIPELINE_HOST}:1883}"
INGESTION_MQTT_TOPIC="${INGESTION_MQTT_TOPIC:-health/telemetry}"
INGESTION_API_KEY="${INGESTION_API_KEY:-dev-key}"

# CDN / DNS config (required for 06-acm + 07-cdn steps)
DOMAIN_NAME="${DOMAIN_NAME:-}"
HOSTED_ZONE_ID="${HOSTED_ZONE_ID:-}"

# Ingestion pipeline CDN — Elastic IP of the EC2 instance (from ingestion-pipeline EC2 stack)
INGESTION_PIPELINE_IP="${INGESTION_PIPELINE_IP:-}"

# Skip flags (set to 1 to skip a step that already completed successfully)
SKIP_BUCKETS="${SKIP_BUCKETS:-0}"
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

for cmd in aws docker npm jq openssl; do
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

# ─── Step 1 — Network (VPC, subnets, security groups) ────────────────────────

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
ALB_SG=$(NET ALBSecurityGroupId)
ECS_SG=$(NET ECSSecurityGroupId)

# ─── Step 2 — Persistent resources (ECR) ────────────────────────────────────

if [[ "$SKIP_PERSISTENT" == "1" ]]; then
  skip "03-persistent (SKIP_PERSISTENT=1)"
else
  step "Persistent resources — ECR (03-persistent)"
  aws cloudformation deploy \
    --template-file "$CF_DIR/03-persistent.yaml" \
    --stack-name "$STACK_PERSISTENT" \
    --region "$REGION" \
    --parameter-overrides \
        ProjectName="$PROJECT_NAME" \
    --no-fail-on-empty-changeset
  success "Persistent stack deployed: $STACK_PERSISTENT"
fi

PERS() { aws cloudformation describe-stacks --stack-name "$STACK_PERSISTENT" --region "$REGION" \
           --query "Stacks[0].Outputs[?OutputKey==\`$1\`].OutputValue" --output text; }
ECR_URI=$(PERS ECRRepositoryUri)
success "ECR repository : $ECR_URI"

# ─── Step 3 — Platform (ALB, ECS cluster, task definition) ───────────────────

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
        VPCId="$VPC_ID" \
        PublicSubnet1Id="$PUBLIC_SUBNET_1" \
        PublicSubnet2Id="$PUBLIC_SUBNET_2" \
        ALBSecurityGroupId="$ALB_SG" \
        ECSSecurityGroupId="$ECS_SG" \
        DefaultHttpEndpoints="$INGESTION_HTTP_ENDPOINT" \
        DefaultMqttBrokerUrl="$INGESTION_MQTT_URL" \
        DefaultMqttTopic="$INGESTION_MQTT_TOPIC" \
        IngestionApiKey="$INGESTION_API_KEY" \
    --no-fail-on-empty-changeset
  success "Platform stack deployed: $STACK_PLATFORM"
fi

PLAT() { aws cloudformation describe-stacks --stack-name "$STACK_PLATFORM" --region "$REGION" \
           --query "Stacks[0].Outputs[?OutputKey==\`$1\`].OutputValue" --output text; }
ECS_CLUSTER=$(PLAT ECSClusterName)
ALB_DNS=$(PLAT ALBDNSName)
TASK_DEF_FAMILY=$(PLAT BackendTaskDefinitionFamily)
TARGET_GROUP_ARN=$(PLAT BackendTargetGroupArn)
CA_CERT_SECRET_ARN=$(PLAT CACertPemSecretArn)
CA_KEY_SECRET_ARN=$(PLAT CAKeyPemSecretArn)

# ── CA certificate initialisation ────────────────────────────────────────────
# Generate a persistent CA cert/key once and store in Secrets Manager.
# Skipped on subsequent runs when the secret already contains real PEM data.

step "CA certificate initialisation"
CA_CURRENT=$(aws secretsmanager get-secret-value \
  --secret-id "$CA_CERT_SECRET_ARN" --region "$REGION" \
  --query 'SecretString' --output text 2>/dev/null || echo "SECRETPLACEHOLDER")

if [[ "$CA_CURRENT" == "SECRETPLACEHOLDER" ]]; then
  info "Generating CA certificate and private key..."
  TMPKEY=$(mktemp) TMPCRT=$(mktemp)
  openssl genrsa -out "$TMPKEY" 4096 2>/dev/null
  openssl req -new -x509 -days 3650 \
    -key "$TMPKEY" -out "$TMPCRT" \
    -subj "/CN=HealthSimulator CA/O=HealthSimulator/C=US" 2>/dev/null

  aws secretsmanager put-secret-value \
    --secret-id "$CA_CERT_SECRET_ARN" --region "$REGION" \
    --secret-string "$(cat "$TMPCRT")"
  aws secretsmanager put-secret-value \
    --secret-id "$CA_KEY_SECRET_ARN" --region "$REGION" \
    --secret-string "$(cat "$TMPKEY")"

  rm -f "$TMPKEY" "$TMPCRT"
  success "CA credentials generated and stored in Secrets Manager"
else
  success "CA credentials already initialised — skipping"
fi

# ─── Step 4 — Docker build + push ────────────────────────────────────────────

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
        DefaultHttpEndpoints="$INGESTION_HTTP_ENDPOINT" \
        DefaultMqttBrokerUrl="$INGESTION_MQTT_URL" \
        DefaultMqttTopic="$INGESTION_MQTT_TOPIC" \
        IngestionApiKey="$INGESTION_API_KEY" \
    --no-fail-on-empty-changeset
  success "Platform stack updated: BackendImage=$FULL_IMAGE"
fi

# ─── Step 5 — ECS service ────────────────────────────────────────────────────

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

# Force a redeployment whenever the platform stack or docker image was updated.
if [[ ("$SKIP_DOCKER" != "1" || "$SKIP_PLATFORM" != "1") && -n "$ECS_CLUSTER" && -n "$ECS_SERVICE" ]]; then
  aws ecs update-service \
    --cluster "$ECS_CLUSTER" \
    --service "$ECS_SERVICE" \
    --region "$REGION" \
    --force-new-deployment > /dev/null
  success "ECS deployment triggered"
fi

# ─── Step 6 — ACM certificate (us-east-1) ────────────────────────────────────

if [[ "$SKIP_ACM" == "1" ]]; then
  skip "06-acm (SKIP_ACM=1)"
elif [[ -z "$DOMAIN_NAME" || -z "$HOSTED_ZONE_ID" ]]; then
  warn "DOMAIN_NAME or HOSTED_ZONE_ID not set — skipping ACM + CDN steps."
  warn "Re-run with: DOMAIN_NAME=your.domain.com HOSTED_ZONE_ID=Z... SKIP_BUCKETS=1 SKIP_NETWORK=1 SKIP_PERSISTENT=1 SKIP_PLATFORM=1 SKIP_DOCKER=1 SKIP_SERVICE=1 ./aws/scripts/bootstrap.sh"
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

ACM() { aws cloudformation describe-stacks --stack-name "$STACK_ACM" --region us-east-1 \
          --query "Stacks[0].Outputs[?OutputKey==\`$1\`].OutputValue" --output text 2>/dev/null || true; }

# ─── Step 7 — CloudFront + Route 53 ──────────────────────────────────────────

if [[ "${SKIP_CDN:-0}" == "1" ]]; then
  skip "07-cdn (SKIP_CDN=1 or DOMAIN_NAME not set)"
else
  step "CloudFront + Route 53 (07-cdn)"

  [[ -z "$INGESTION_PIPELINE_IP" ]] && \
    error "INGESTION_PIPELINE_IP not set — pass the EC2 Elastic IP: INGESTION_PIPELINE_IP=1.2.3.4"

  CERT_ARN=$(ACM CertificateArn)
  INGESTION_CERT_ARN=$(ACM IngestionCertificateArn)
  WILDCARD_CERT_ARN=$(ACM WildcardCertificateArn)

  [[ -z "$WILDCARD_CERT_ARN" || "$WILDCARD_CERT_ARN" == "None" ]] && \
    error "WildcardCertificateArn not found in ACM stack — redeploy 06-acm first (SKIP_ACM=0 SKIP_CDN=1)"

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
        IngestionPipelineIp="$INGESTION_PIPELINE_IP" \
        IngestionCertificateArn="$INGESTION_CERT_ARN" \
        WildcardCertificateArn="$WILDCARD_CERT_ARN" \
    --no-fail-on-empty-changeset
  success "CDN stack deployed: $STACK_CDN"

  CF_DIST_ID=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_CDN" --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontDistributionId`].OutputValue' \
    --output text)

  # ── Build frontend ────────────────────────────────────────────────────────
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

  # ── Upload frontend build ─────────────────────────────────────────────────
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
    warn "frontend/dist/ not found — run 'cd frontend && npm run build' then re-run with SKIP_BUCKETS=1 SKIP_NETWORK=1 SKIP_PERSISTENT=1 SKIP_PLATFORM=1 SKIP_DOCKER=1 SKIP_SERVICE=1 SKIP_ACM=1"
  fi
fi

# ─── Summary ─────────────────────────────────────────────────────────────────

step "Done"

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN} Health Telemetry Simulator infrastructure is ready${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  AWS profile        : ${CYAN}${PROFILE:-default}${NC}"
echo -e "  Region             : ${CYAN}${REGION}${NC}"
echo -e "  ECR repository     : ${CYAN}${ECR_URI:-N/A}${NC}"
echo -e "  ECS cluster        : ${CYAN}${ECS_CLUSTER:-N/A}${NC}"
echo -e "  ALB endpoint       : ${CYAN}http://${ALB_DNS:-N/A}${NC}"
echo -e "  Ingestion pipeline : ${CYAN}http://${INGESTION_PIPELINE_HOST}:9000${NC}"
echo -e "  MQTT broker        : ${CYAN}${INGESTION_MQTT_URL}${NC}"
[[ -n "$DOMAIN_NAME" ]] && echo -e "  Site URL           : ${CYAN}https://${DOMAIN_NAME}${NC}"
[[ -n "$INGESTION_PIPELINE_IP" ]] && echo -e "  Ingestion CDN      : ${CYAN}https://ingestion-pipeline.aleksandr-mordanov.click${NC}"
[[ -n "$INGESTION_PIPELINE_IP" ]] && echo -e "  Grafana            : ${CYAN}https://grafana.aleksandr-mordanov.click${NC}"
[[ -n "$INGESTION_PIPELINE_IP" ]] && echo -e "  Prometheus         : ${CYAN}https://prometheus.aleksandr-mordanov.click${NC}"
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
