#! /bin/sh
set -e

cd "$(dirname "$0")"

kubectl apply -k k8s/storage
