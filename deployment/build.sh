#! /bin/sh
set -e
cd "$(dirname "$0")"

. ./utils.sh
. ./.env

IMAGE="$GCLOUD_REPOSITORY/training-ai-server:$(get_commit_hash)"
# GKE nodes are amd64; build for them regardless of the local Docker host architecture.
IMAGE_PLATFORM="${IMAGE_PLATFORM:-linux/amd64}"

make -C .. build-web
DOCKER_BUILDKIT=1 docker build --platform "$IMAGE_PLATFORM" -f ../Dockerfile -t training-ai-server -t "$IMAGE" ../

if [ -f .env ]; then
  tmp_file=$(mktemp)
  awk -v image="$IMAGE" '
    BEGIN { updated = 0 }
    /^TRAINING_AI_SERVER_IMAGE=/ {
      print "TRAINING_AI_SERVER_IMAGE=" image
      updated = 1
      next
    }
    { print }
    END {
      if (!updated) {
        print "TRAINING_AI_SERVER_IMAGE=" image
      }
    }
  ' .env > "$tmp_file"
  mv "$tmp_file" .env
fi
