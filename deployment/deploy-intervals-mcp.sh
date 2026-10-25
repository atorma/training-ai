#! /bin/sh
set -e

cd "$(dirname "$0")"

# The image is pinned by digest in the manifest; no build or push step.
kubectl apply -k k8s/intervals-mcp
kubectl rollout status deployment/intervals-mcp-deployment --timeout=5m
