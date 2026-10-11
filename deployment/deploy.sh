#! /bin/sh
set -e

cd "$(dirname "$0")"

. ./utils.sh
. ./.env

IMAGE="${TRAINING_AI_SERVER_IMAGE:-$GCLOUD_REPOSITORY/training-ai-server:$(get_commit_hash)}"

manifest_dir=$(mktemp -d)
trap 'rm -rf "$manifest_dir"' EXIT
trap 'exit 1' HUP INT TERM
cp -R k8s/app/. "$manifest_dir/"
cd "$manifest_dir"
kustomize edit set image training-ai-server-image="$IMAGE"
kubectl apply -k .
kubectl rollout status deployment/training-ai --timeout=20m
