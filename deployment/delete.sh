#! /bin/sh
set -e

cd "$(dirname "$0")"
cd k8s

kubectl delete -k .