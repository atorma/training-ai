#! /bin/sh
# Create the private attachment bucket and give the app's Kubernetes service account
# object create and read access (no delete) through Workload Identity.
# Never changes cluster settings: if Workload Identity is off, it prints the commands and stops.
set -eu

cd "$(dirname "$0")"

. ./.env

: "${GCLOUD_PROJECT_ID:?Set GCLOUD_PROJECT_ID in deployment/.env}"
: "${GKE_CLUSTER:?Set GKE_CLUSTER in deployment/.env}"
: "${ATTACHMENT_BUCKET:?Set ATTACHMENT_BUCKET in deployment/.env}"
GKE_LOCATION="${GKE_LOCATION:-europe-west1-b}"
BUCKET_LOCATION="${BUCKET_LOCATION:-${GKE_LOCATION%-*}}"
SERVICE_ACCOUNT=training-ai
NAMESPACE="$(kubectl config view --minify -o jsonpath='{..namespace}')"
NAMESPACE="${NAMESPACE:-default}"
WORKLOAD_POOL="$GCLOUD_PROJECT_ID.svc.id.goog"

cluster() {
  gcloud container "$@" --project "$GCLOUD_PROJECT_ID" --location "$GKE_LOCATION"
}

echo "Checking Workload Identity on cluster $GKE_CLUSTER..."
missing=0
pool="$(cluster clusters describe "$GKE_CLUSTER" --format 'value(workloadIdentityConfig.workloadPool)')"
if [ "$pool" != "$WORKLOAD_POOL" ]; then
  missing=1
  echo "Workload Identity is not enabled on the cluster. Enable it with:"
  echo "  gcloud container clusters update $GKE_CLUSTER --project $GCLOUD_PROJECT_ID \\"
  echo "    --location $GKE_LOCATION --workload-pool=$WORKLOAD_POOL"
fi
for node_pool in $(cluster node-pools list --cluster "$GKE_CLUSTER" --format 'value(name)'); do
  mode="$(cluster node-pools describe "$node_pool" --cluster "$GKE_CLUSTER" \
    --format 'value(config.workloadMetadataConfig.mode)')"
  if [ "$mode" != "GKE_METADATA" ]; then
    missing=1
    echo "Node pool $node_pool does not use the GKE metadata server. Enable it with:"
    echo "  gcloud container node-pools update $node_pool --cluster $GKE_CLUSTER \\"
    echo "    --project $GCLOUD_PROJECT_ID --location $GKE_LOCATION --workload-metadata=GKE_METADATA"
  fi
done
if [ "$missing" -ne 0 ]; then
  echo "Updating a node pool recreates its nodes. Rerun this script afterwards."
  exit 1
fi

bucket="gs://$ATTACHMENT_BUCKET"
if gcloud storage buckets describe "$bucket" --project "$GCLOUD_PROJECT_ID" >/dev/null 2>&1; then
  echo "Bucket $bucket exists; enforcing private access."
  gcloud storage buckets update "$bucket" --project "$GCLOUD_PROJECT_ID" \
    --uniform-bucket-level-access --public-access-prevention
else
  echo "Creating private bucket $bucket in $BUCKET_LOCATION."
  gcloud storage buckets create "$bucket" --project "$GCLOUD_PROJECT_ID" \
    --location "$BUCKET_LOCATION" --uniform-bucket-level-access --public-access-prevention
fi

project_number="$(gcloud projects describe "$GCLOUD_PROJECT_ID" --format 'value(projectNumber)')"
member="principal://iam.googleapis.com/projects/$project_number/locations/global"
member="$member/workloadIdentityPools/$WORKLOAD_POOL/subject/ns/$NAMESPACE/sa/$SERVICE_ACCOUNT"
for role in roles/storage.objectCreator roles/storage.objectViewer; do
  echo "Granting $role on $bucket to $NAMESPACE/$SERVICE_ACCOUNT."
  gcloud storage buckets add-iam-policy-binding "$bucket" --project "$GCLOUD_PROJECT_ID" \
    --member "$member" --role "$role" >/dev/null
done

echo "Done. Deploy the application with deployment/deploy.sh to use the bucket."
