#!/usr/bin/env bash
# Deploy FactGuard to AWS ECS (Fargate)
#
# Prerequisites:
#   - AWS CLI configured (aws configure)
#   - Docker running
#   - ECR repository created: aws ecr create-repository --repository-name factguard
#   - ECS cluster created:    aws ecs create-cluster --cluster-name factguard
#   - IAM roles created: ecsTaskExecutionRole, ecsTaskRole (with S3 + Secrets Manager access)
#   - CloudWatch log group:   aws logs create-log-group --log-group-name /ecs/factguard
#
# Usage:
#   chmod +x deploy.sh
#   AWS_ACCOUNT_ID=123456789012 AWS_REGION=ap-southeast-1 ./deploy.sh

set -euo pipefail

# Preflight checks
missing=()
command -v aws    &>/dev/null || missing+=("aws CLI  -> https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html")
command -v docker &>/dev/null || missing+=("Docker   -> https://docs.docker.com/get-docker/")
if [[ ${#missing[@]} -gt 0 ]]; then
  echo "ERROR: The following required tools are not installed:"
  for m in "${missing[@]}"; do echo "  - ${m}"; done
  exit 1
fi

# Read from env or prompt interactively
if [[ -z "${AWS_ACCOUNT_ID:-}" ]]; then
  read -rp "AWS Account ID: " AWS_ACCOUNT_ID
fi
if [[ -z "${AWS_REGION:-}" ]]; then
  read -rp "AWS Region (e.g. ap-southeast-1): " AWS_REGION
fi

ACCOUNT_ID="${AWS_ACCOUNT_ID}"
REGION="${AWS_REGION}"
REPO="factguard"
CLUSTER="factguard"
SERVICE="factguard"
IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO}:latest"
TASK_DEF_FILE="ecs-task-definition.json"

echo "==> Authenticating Docker with ECR"
aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin \
      "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

echo "==> Building Docker image"
docker build -t "${REPO}:latest" .

echo "==> Tagging and pushing to ECR"
docker tag "${REPO}:latest" "${IMAGE_URI}"
docker push "${IMAGE_URI}"

echo "==> Patching task definition with account/region"
PATCHED=$(sed \
  -e "s/ACCOUNT_ID/${ACCOUNT_ID}/g" \
  -e "s/REGION/${REGION}/g" \
  "${TASK_DEF_FILE}")

echo "==> Registering task definition"
TASK_ARN=$(echo "${PATCHED}" \
  | aws ecs register-task-definition \
      --cli-input-json file:///dev/stdin \
      --query "taskDefinition.taskDefinitionArn" \
      --output text)
echo "    Task definition: ${TASK_ARN}"

echo "==> Updating ECS service"
aws ecs update-service \
  --cluster "${CLUSTER}" \
  --service "${SERVICE}" \
  --task-definition "${TASK_ARN}" \
  --force-new-deployment \
  --region "${REGION}" \
  --query "service.serviceName" \
  --output text

echo "==> Deploy triggered. Monitor with:"
echo "    aws ecs describe-services --cluster ${CLUSTER} --services ${SERVICE} --region ${REGION}"
