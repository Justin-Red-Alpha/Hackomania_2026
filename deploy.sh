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
REPO="justin-hackomania2026-factguard"
CLUSTER="default"
SERVICE="factguard"
IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO}:latest"

# Load .env for secrets
set -o allexport
# shellcheck source=.env
source "$(dirname "$0")/.env"
set +o allexport

echo "==> Authenticating Docker with ECR"
aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin \
      "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

echo "==> Building and pushing Docker image"
docker build -t "${REPO}:latest" .
docker tag "${REPO}:latest" "${IMAGE_URI}"
docker push "${IMAGE_URI}"

echo "==> Registering task definition"
TASK_ARN=$(MSYS_NO_PATHCONV=1 aws ecs register-task-definition \
  --family factguard \
  --network-mode awsvpc \
  --requires-compatibilities FARGATE \
  --cpu 512 \
  --memory 1024 \
  --execution-role-arn "arn:aws:iam::${ACCOUNT_ID}:role/service-role/ecsTaskExecutionRole" \
  --container-definitions "[
    {
      \"name\": \"factguard\",
      \"image\": \"${IMAGE_URI}\",
      \"essential\": true,
      \"portMappings\": [{\"containerPort\": 8000, \"protocol\": \"tcp\"}],
      \"environment\": [
        {\"name\": \"ANTHROPIC_API_KEY\",      \"value\": \"${ANTHROPIC_API_KEY}\"},
        {\"name\": \"TAVILY_API_KEY\",          \"value\": \"${TAVILY_API_KEY}\"},
        {\"name\": \"AWS_ACCESS_KEY_ID\",       \"value\": \"${AWS_ACCESS_KEY_ID}\"},
        {\"name\": \"AWS_SECRET_ACCESS_KEY\",   \"value\": \"${AWS_SECRET_ACCESS_KEY}\"},
        {\"name\": \"S3_BUCKET_NAME\",          \"value\": \"${S3_BUCKET_NAME}\"},
        {\"name\": \"S3_REGION\",               \"value\": \"${S3_REGION}\"},
        {\"name\": \"CLICKHOUSE_HOST\",         \"value\": \"${CLICKHOUSE_HOST}\"},
        {\"name\": \"CLICKHOUSE_PORT\",         \"value\": \"${CLICKHOUSE_PORT}\"},
        {\"name\": \"CLICKHOUSE_USER\",         \"value\": \"${CLICKHOUSE_USER}\"},
        {\"name\": \"CLICKHOUSE_PASSWORD\",     \"value\": \"${CLICKHOUSE_PASSWORD}\"},
        {\"name\": \"DATA_GOV_API_KEY\",        \"value\": \"${DATA_GOV_API_KEY}\"}
      ],
      \"logConfiguration\": {
        \"logDriver\": \"awslogs\",
        \"options\": {
          \"awslogs-group\": \"/ecs/factguard\",
          \"awslogs-region\": \"${REGION}\",
          \"awslogs-stream-prefix\": \"ecs\"
        }
      },
      \"healthCheck\": {
        \"command\": [\"CMD-SHELL\", \"curl -f http://localhost:8000/health || exit 1\"],
        \"interval\": 30,
        \"timeout\": 5,
        \"retries\": 3,
        \"startPeriod\": 15
      }
    }
  ]" \
  --region "${REGION}" \
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
echo "    MSYS_NO_PATHCONV=1 aws logs tail /ecs/factguard --follow --region ${REGION}"
