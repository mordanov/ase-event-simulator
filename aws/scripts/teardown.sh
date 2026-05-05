#!/usr/bin/env bash
# =============================================================================
# Health Telemetry Simulator — AWS Teardown
#
# Deletes ALL resources created by bootstrap.sh in reverse dependency order:
#   07-cdn        → CloudFront distribution + Route 53 record
#   06-acm        → ACM certificate (us-east-1)
#   05-service    → ECS service
#   04-platform   → ALB, ECS cluster, task definition
#   03-persistent → ECR (images deleted first)
#   02-network    → VPC, subnets, security groups
#   00-buckets    → S3 buckets (emptied first, then deleted)
#
# Usage — same env vars as bootstrap.sh:
#   cd simulator/
#   ./aws/scripts/teardown.sh
#
#   PROFILE=my-profile REGION=eu-west-1 ./aws/scripts/teardown.sh
#
#   # Skip individual steps:
#   SKIP_CDN=1 SKIP_ACM=1 ./aws/scripts/teardown.sh
#
#   # Dry run — print what would be deleted without doing anything:
#   DRY_RUN=1 ./aws/scripts/teardown.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AWS_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$AWS_DIR")"

# ─── Configuration (mirrors bootstrap.sh defaults) ───────────────────────────

PROFILE="${PROFILE:-${AWS_PROFILE:-}}"
[[ -n "$PROFILE" ]] && export AWS_PROFILE="$PROFILE"

REGION="${REGION:-${AWS_DEFAULT_REGION:-us-west-2}}"
PROJECT_NAME="${PROJECT_NAME:-health-simulator}"

STACK_BUCKETS="${STACK_BUCKETS:-${PROJECT_NAME}-buckets}"
STACK_NETWORK="${STACK_NETWORK:-${PROJECT_NAME}-network}"
STACK_PERSISTENT="${STACK_PERSISTENT:-${PROJECT_NAME}-persistent}"
STACK_PLATFORM="${STACK_PLATFORM:-${PROJECT_NAME}-platform}"
STACK_SERVICE="${STACK_SERVICE:-${PROJECT_NAME}-service}"
STACK_ACM="${STACK_ACM:-${PROJECT_NAME}-acm}"
STACK_CDN="${STACK_CDN:-${PROJECT_NAME}-cdn}"

SKIP_CDN="${SKIP_CDN:-0}"
SKIP_ACM="${SKIP_ACM:-0}"
SKIP_SERVICE="${SKIP_SERVICE:-0}"
SKIP_PLATFORM="${SKIP_PLATFORM:-0}"
SKIP_PERSISTENT="${SKIP_PERSISTENT:-0}"
SKIP_NETWORK="${SKIP_NETWORK:-0}"
SKIP_BUCKETS="${SKIP_BUCKETS:-0}"

DRY_RUN="${DRY_RUN:-0}"

# ─── Colours ─────────────────────────────────────────────────────────────────

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }
step()    { echo -e "\n${CYAN}══ $* ══${NC}"; }
skip()    { echo -e "${YELLOW}[SKIP]${NC}  $*"; }
dry()     { echo -e "${YELLOW}[DRY]${NC}   would run: $*"; }

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    dry "$*"
  else
    "$@"
  fi
}

# ─── Helpers ─────────────────────────────────────────────────────────────────

stack_exists() {
  aws cloudformation describe-stacks \
    --stack-name "$1" \
    --region "${2:-$REGION}" \
    --query 'Stacks[0].StackStatus' \
    --output text 2>/dev/null | grep -qv "NONE\|does not exist" && return 0 || return 1
}

stack_output() {
  aws cloudformation describe-stacks \
    --stack-name "$1" --region "${3:-$REGION}" \
    --query "Stacks[0].Outputs[?OutputKey==\`$2\`].OutputValue" \
    --output text 2>/dev/null || true
}

delete_stack() {
  local name="$1" region="${2:-$REGION}"
  if ! stack_exists "$name" "$region"; then
    warn "Stack $name not found — already deleted or never created"
    return 0
  fi
  info "Deleting stack: $name (region: $region)"
  run aws cloudformation delete-stack --stack-name "$name" --region "$region"
  if [[ "$DRY_RUN" != "1" ]]; then
    info "Waiting for $name to be deleted..."
    aws cloudformation wait stack-delete-complete \
      --stack-name "$name" --region "$region" \
      || error "Stack deletion failed: $name — check CloudFormation console for details"
    success "Stack deleted: $name"
  fi
}

empty_bucket() {
  local bucket="$1"
  if ! aws s3api head-bucket --bucket "$bucket" --region "$REGION" 2>/dev/null; then
    warn "Bucket $bucket not found — already deleted"
    return 0
  fi
  info "Emptying bucket: $bucket"
  run aws s3 rm "s3://${bucket}/" --recursive --region "$REGION" 2>/dev/null || true
  if [[ "$DRY_RUN" != "1" ]]; then
    while true; do
      BATCH=$(aws s3api list-object-versions \
        --bucket "$bucket" --region "$REGION" \
        --max-items 1000 \
        --query '{Objects: (Versions[].{Key:Key,VersionId:VersionId}) + (DeleteMarkers[].{Key:Key,VersionId:VersionId})}' \
        --output json 2>/dev/null || echo '{"Objects":[]}')
      COUNT=$(echo "$BATCH" | jq '.Objects | length')
      [[ "$COUNT" == "0" || "$COUNT" == "null" ]] && break
      info "  Deleting $COUNT object versions..."
      echo "$BATCH" | aws s3api delete-objects \
        --bucket "$bucket" \
        --delete "$(echo "$BATCH" | jq '{Objects:.Objects,Quiet:true}')" \
        --region "$REGION" > /dev/null
    done
  fi
  success "Bucket emptied: $bucket"
}

delete_bucket() {
  local bucket="$1"
  if ! aws s3api head-bucket --bucket "$bucket" --region "$REGION" 2>/dev/null; then
    warn "Bucket $bucket not found"
    return 0
  fi
  run aws s3api delete-bucket --bucket "$bucket" --region "$REGION"
  [[ "$DRY_RUN" != "1" ]] && success "Bucket deleted: $bucket"
}

# ─── Preflight ────────────────────────────────────────────────────────────────

step "Preflight"

for cmd in aws jq; do
  command -v "$cmd" &>/dev/null || error "Required tool not found: $cmd"
done

aws sts get-caller-identity --region "$REGION" --query 'Account' --output text &>/dev/null \
  || error "AWS credentials invalid (profile: ${PROFILE:-default}, region: $REGION)"

ACCOUNT_ID=$(aws sts get-caller-identity --region "$REGION" --query 'Account' --output text)
info "AWS account: $ACCOUNT_ID  region: $REGION  profile: ${PROFILE:-default}"

[[ "$DRY_RUN" == "1" ]] && warn "DRY RUN — no resources will be modified"

# ─── Confirmation ─────────────────────────────────────────────────────────────

echo ""
echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${RED} WARNING: This will permanently delete all simulator resources${NC}"
echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  Stacks to delete : ${YELLOW}${STACK_CDN}, ${STACK_ACM} (us-east-1), ${STACK_SERVICE},${NC}"
echo -e "                     ${YELLOW}${STACK_PLATFORM}, ${STACK_PERSISTENT}, ${STACK_NETWORK}, ${STACK_BUCKETS}${NC}"
echo -e "  ECR repository   : ${YELLOW}${PROJECT_NAME}-backend${NC} (images deleted, then repo)"
echo -e "  S3 buckets       : ${YELLOW}${PROJECT_NAME}-deploy-${ACCOUNT_ID}-${REGION}${NC}"
echo -e "                     ${YELLOW}${PROJECT_NAME}-frontend-${ACCOUNT_ID}-${REGION}${NC}"
echo ""

if [[ "$DRY_RUN" != "1" ]]; then
  read -r -p "Type the project name to confirm deletion [${PROJECT_NAME}]: " CONFIRM
  if [[ "$CONFIRM" != "$PROJECT_NAME" ]]; then
    echo "Aborted — input did not match '${PROJECT_NAME}'"
    exit 1
  fi
fi

# ─── Step 1 — CloudFront + Route 53 (07-cdn) ─────────────────────────────────

if [[ "$SKIP_CDN" == "1" ]]; then
  skip "07-cdn"
else
  step "CloudFront + Route 53 (07-cdn)"
  delete_stack "$STACK_CDN" "$REGION"
fi

# ─── Step 2 — ACM certificate (06-acm, us-east-1) ────────────────────────────

if [[ "$SKIP_ACM" == "1" ]]; then
  skip "06-acm"
else
  step "ACM Certificate (06-acm, us-east-1)"
  delete_stack "$STACK_ACM" "us-east-1"
fi

# ─── Step 3 — ECS service (05-service) ───────────────────────────────────────

if [[ "$SKIP_SERVICE" == "1" ]]; then
  skip "05-service"
else
  step "ECS Service (05-service)"

  ECS_CLUSTER=$(stack_output "$STACK_PLATFORM" "ECSClusterName")
  ECS_SERVICE=$(stack_output "$STACK_SERVICE"  "ECSServiceName")

  if [[ -n "$ECS_CLUSTER" && -n "$ECS_SERVICE" ]]; then
    info "Scaling ECS service to 0 tasks..."
    run aws ecs update-service \
      --cluster "$ECS_CLUSTER" \
      --service  "$ECS_SERVICE" \
      --desired-count 0 \
      --region "$REGION" > /dev/null
    if [[ "$DRY_RUN" != "1" ]]; then
      info "Waiting for ECS tasks to stop..."
      aws ecs wait services-stable \
        --cluster  "$ECS_CLUSTER" \
        --services "$ECS_SERVICE" \
        --region   "$REGION"
      success "ECS service drained"
    fi
  else
    warn "ECS cluster/service not found in stack outputs — skipping scale-down"
  fi

  delete_stack "$STACK_SERVICE" "$REGION"
fi

# ─── Step 4 — Platform (04-platform) ─────────────────────────────────────────

if [[ "$SKIP_PLATFORM" == "1" ]]; then
  skip "04-platform"
else
  step "Platform — ALB / ECS cluster / task definition (04-platform)"

  # Delete the app-config secret created by CloudFormation (no recovery window).
  APP_CONFIG_SECRET="/${PROJECT_NAME}/app-config-params"
  if aws secretsmanager describe-secret \
      --secret-id "$APP_CONFIG_SECRET" \
      --region "$REGION" &>/dev/null; then
    info "Deleting Secrets Manager secret: $APP_CONFIG_SECRET"
    run aws secretsmanager delete-secret \
      --secret-id "$APP_CONFIG_SECRET" \
      --force-delete-without-recovery \
      --region "$REGION" > /dev/null
    [[ "$DRY_RUN" != "1" ]] && success "Secret deleted: $APP_CONFIG_SECRET"
  else
    warn "Secret $APP_CONFIG_SECRET not found"
  fi

  delete_stack "$STACK_PLATFORM" "$REGION"
fi

# ─── Step 5 — Persistent resources (03-persistent) ───────────────────────────

if [[ "$SKIP_PERSISTENT" == "1" ]]; then
  skip "03-persistent"
else
  step "Persistent resources — ECR (03-persistent)"

  ECR_REPO_NAME="${PROJECT_NAME}-backend"

  if aws ecr describe-repositories \
      --repository-names "$ECR_REPO_NAME" --region "$REGION" &>/dev/null; then
    if [[ "$DRY_RUN" != "1" ]]; then
      IMAGE_IDS=$(aws ecr list-images \
        --repository-name "$ECR_REPO_NAME" --region "$REGION" \
        --query 'imageIds[*]' --output json 2>/dev/null || echo "[]")
      IMAGE_COUNT=$(echo "$IMAGE_IDS" | jq 'length')
      if [[ "$IMAGE_COUNT" -gt 0 ]]; then
        info "Deleting $IMAGE_COUNT image(s) from ECR: $ECR_REPO_NAME"
        aws ecr batch-delete-image \
          --repository-name "$ECR_REPO_NAME" --region "$REGION" \
          --image-ids "$IMAGE_IDS" > /dev/null
      fi
    else
      dry "aws ecr batch-delete-image --repository-name $ECR_REPO_NAME (all images)"
    fi
    info "Deleting ECR repository: $ECR_REPO_NAME"
    run aws ecr delete-repository \
      --repository-name "$ECR_REPO_NAME" --region "$REGION" > /dev/null
    [[ "$DRY_RUN" != "1" ]] && success "ECR repository deleted"
  else
    warn "ECR repository $ECR_REPO_NAME not found"
  fi

  delete_stack "$STACK_PERSISTENT" "$REGION"
fi

# ─── Step 6 — Network (02-network) ───────────────────────────────────────────

if [[ "$SKIP_NETWORK" == "1" ]]; then
  skip "02-network"
else
  step "Network — VPC / subnets / security groups (02-network)"
  delete_stack "$STACK_NETWORK" "$REGION"
fi

# ─── Step 7 — S3 buckets (00-buckets) ────────────────────────────────────────

if [[ "$SKIP_BUCKETS" == "1" ]]; then
  skip "00-buckets"
else
  step "S3 Buckets (00-buckets)"

  DEPLOY_BUCKET=$(stack_output "$STACK_BUCKETS" "DeployBucketName")
  FRONTEND_BUCKET=$(stack_output "$STACK_BUCKETS" "FrontendBucketName")

  DEPLOY_BUCKET="${DEPLOY_BUCKET:-${PROJECT_NAME}-deploy-${ACCOUNT_ID}-${REGION}}"
  FRONTEND_BUCKET="${FRONTEND_BUCKET:-${PROJECT_NAME}-frontend-${ACCOUNT_ID}-${REGION}}"

  empty_bucket "$DEPLOY_BUCKET"
  empty_bucket "$FRONTEND_BUCKET"

  delete_stack "$STACK_BUCKETS" "$REGION"

  delete_bucket "$DEPLOY_BUCKET"
  delete_bucket "$FRONTEND_BUCKET"
fi

# ─── Done ─────────────────────────────────────────────────────────────────────

step "Done"

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN} Teardown complete${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  Remaining manual cleanup (if any):"
echo -e "  ${YELLOW}• CloudWatch log groups${NC} — /ecs/${PROJECT_NAME}-backend"
echo -e "  ${YELLOW}• Ingestion pipeline${NC} — deployed separately on EC2, not affected"
echo ""
