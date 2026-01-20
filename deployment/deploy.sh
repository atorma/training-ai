#! /bin/sh
set -e

cd "$(dirname "$0")"

. utils.sh
. .env

cd k8s

IMAGE="${TRAINING_AI_SERVER_IMAGE:-$GCLOUD_REPOSITORY/training-ai-server:$(get_commit_hash)}"

kustomize edit set image training-ai-server-image="$IMAGE"
kubectl apply -k .
git restore kustomization.yaml
