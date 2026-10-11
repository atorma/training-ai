#! /bin/sh
set -e

cd "$(dirname "$0")"
# Application manifests exclude storage; the PVC and StorageClass remain untouched.
kubectl delete -k k8s/app --ignore-not-found=true
