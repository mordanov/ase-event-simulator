#!/usr/bin/env bash
# Rebuild the backend Docker image, push to ECR, and force a new ECS deployment.
# Usage: ./aws/scripts/quickfix.sh [image-tag]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

PROFILE="${PROFILE:-${AWS_PROFILE:-}}"
[[ -n "$PROFILE" ]] && export AWS_PROFILE="$PROFILE"

REGION="${REGION:-${AWS_DEFAULT_REGION:-us-west-2}}"
PROJECT_NAME="${PROJECT_NAME:-health-simulator}"
IMAGE_TAG="${1:-latest}"

STACK_PERSISTENT="${PROJECT_NAME}-persistent"
STACK_PLATFORM="${PROJECT_NAME}-platform"
STACK_SERVICE="${PROJECT_NAME}-service"

ACCOUNT_ID=$(aws sts get-caller-identity --region "$REGION" --query 'Account' --output text)

ECR_URI=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_PERSISTENT" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`ECRRepositoryUri`].OutputValue' \
  --output text)

ECS_CLUSTER=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_PLATFORM" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`ECSClusterName`].OutputValue' \
  --output text)

ECS_SERVICE=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_SERVICE" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`ECSServiceName`].OutputValue' \
  --output text)

FULL_IMAGE="${ECR_URI}:${IMAGE_TAG}"

echo "Building $FULL_IMAGE ..."
docker build --platform linux/amd64 -t "$FULL_IMAGE" "$PROJECT_ROOT/backend"

echo "Pushing $FULL_IMAGE ..."
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin \
      "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
docker push "$FULL_IMAGE"
echo "Image pushed."

echo "Forcing new ECS deployment ..."
aws ecs update-service \
  --cluster "$ECS_CLUSTER" \
  --service  "$ECS_SERVICE" \
  --region   "$REGION" \
  --force-new-deployment > /dev/null
echo "Done — new task starting on cluster '$ECS_CLUSTER', service '$ECS_SERVICE'."
