#! /bin/sh
set -e

cd "$(dirname "$0")"

. utils.sh
. .env

IMAGE="${TRAINING_AI_SERVER_IMAGE:-$GCLOUD_REPOSITORY/training-ai-server:$(get_commit_hash)}"

docker push "$IMAGE"
