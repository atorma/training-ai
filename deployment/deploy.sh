#! /bin/sh
set -e

cd "$(dirname "$0")"

. ./utils.sh
. ./.env

IMAGE="${TRAINING_AI_SERVER_IMAGE:-$GCLOUD_REPOSITORY/training-ai-server:$(get_commit_hash)}"
: "${ATTACHMENT_BUCKET:?Set ATTACHMENT_BUCKET in deployment/.env; see setup-attachments.sh}"

manifest_dir=$(mktemp -d)
trap 'rm -rf "$manifest_dir"' EXIT
trap 'exit 1' HUP INT TERM
cp -R k8s/app/. "$manifest_dir/"
cd "$manifest_dir"
kustomize edit set image training-ai-server-image="$IMAGE"
kustomize edit add configmap training-ai-attachments \
  --from-literal=ATTACHMENT_BUCKET="$ATTACHMENT_BUCKET"
kubectl apply -k .
kubectl rollout status deployment/training-ai --timeout=20m
