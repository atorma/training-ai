# Kubernetes deployment

The manifests deploy one Python server with SQLite on GKE, with separate Kustomize roots:

- `deployment/k8s/app/`: Deployment, ServiceAccount, Service, Ingress, Certificate, and summary
  CronJob.
- `deployment/k8s/storage/`: PersistentVolumeClaim and StorageClass.
- `deployment/k8s/intervals-mcp/`: the Intervals.icu MCP server the application calls.

Render them locally with `make render-k8s`, `make render-k8s-storage` and
`make render-k8s-mcp`; none contacts a cluster. There is no combined root Kustomization that
can delete all sets together. The deployment scripts apply to the current kubectl context and
namespace.

Build the image with `make build-docker` after `make bootstrap` and configuring
`GCLOUD_REPOSITORY` in `deployment/.env`. BuildKit and the Docker buildx CLI plugin are
required; check `docker buildx version` when using Colima. The script runs `make build-web`
locally before Docker packages the generated assets; no Node build stage runs in Docker.
The image targets `linux/amd64` to match the GKE nodes, whatever the local Docker host
architecture is. On an aarch64 Colima VM this needs amd64 emulation (`vmType: vz` with
`rosetta: true`, or binfmt/QEMU). Override with `IMAGE_PLATFORM` if the node pool changes.
Direct Docker builds require
`make build-web` first. Missing assets fail the image build; rebuilding avoids stale UI assets.
Image push and application deployment remain separate script operations.
Python dependencies use `uv sync --locked --no-dev` with uv 0.13.0 in the build stages.
Dependencies install before application source is copied, and BuildKit caches uv downloads.
The project installs without editable source links; only the Linux Python environment is
copied into the runtime image. uv and its cache remain in the build stages.

Before the first application deployment, run `sh deployment/setup-storage.sh` to apply the
storage manifests. Storage changes, such as expanding the claim, use this separate script.
Routine `deployment/deploy.sh` and `deployment/delete.sh` operate only on application manifests.
The same resource names are preserved, so existing storage can be reused after this file split.

## Persistent storage

- `training-ai-data` requests a 10 GiB `ReadWriteOnce` volume.
- `training-ai-balanced` uses the GKE Persistent Disk CSI driver, `pd-balanced`, and ext4.
- Provisioning waits for the consuming pod and is restricted to `europe-west1-b`.
- The volume mounts at `/app/data`; `DATABASE_PATH` is `/app/data/conversations.sqlite3`.
  The database, WAL, lock file, and upgrade backups all live on this volume.
- Pod UID/GID and `fsGroup` are 1000, allowing the non-root server and backup container
  to write the mounted filesystem.
- Volume expansion is enabled. Increase the PVC request when needed; disks cannot shrink.
- The StorageClass uses `Retain`. Deleting a PVC retains the underlying disk, but reclaiming
  and rebinding it requires administrator action. Avoid deleting the PVC during upgrades.

The cluster must have the Persistent Disk CSI driver enabled, compatible nodes in
`europe-west1-b`, and permissions to create the cluster-scoped StorageClass. These requirements
have not been checked against the live cluster. See [GKE CSI documentation](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/persistent-volumes/gce-pd-csi-driver).

## Attachment storage

The application ingress allows request bodies of 21 MB, just above the 20 MB attachment limit, so
the application enforces that limit itself rather than ingress-nginx's 1 MB default.

Chat attachment bytes live in a private Google Cloud Storage bucket, not on the volume. The
server reaches it as the `training-ai` Kubernetes service account through Workload Identity,
with Application Default Credentials and no key files.

Run `sh deployment/setup-attachments.sh` once before deploying this feature, with explicit
approval, because it creates cloud resources. It reads `GCLOUD_PROJECT_ID`, `GKE_CLUSTER`,
`ATTACHMENT_BUCKET` and optionally `GKE_LOCATION` (default `europe-west1-b`) from
`deployment/.env`, and uses the current kubectl namespace. The script:

1. Checks that Workload Identity is enabled on the cluster and that every node pool uses the
   GKE metadata server. If not, it prints the `gcloud` commands that enable them and stops.
   It never changes cluster settings itself; updating a node pool recreates its nodes.
2. Creates the bucket in the cluster's region with uniform bucket-level access and public
   access prevention, or enforces both on an existing bucket.
3. Grants the service account `roles/storage.objectCreator` and `roles/storage.objectViewer`
   on the bucket only: it can create and read objects but not delete or overwrite them.

`deployment/deploy.sh` requires `ATTACHMENT_BUCKET` and passes it to the server through a
generated `training-ai-attachments` ConfigMap. If the bucket is unreachable at startup, the
server logs a warning and still starts: text chat works, and uploads, downloads and runs that
need attachment content fail with a storage error until access is fixed. Check access first
when attachments fail after a deploy.

Data that stays outside the database lifecycle:

- Pre-upgrade backups copy SQLite only, not attachment bytes. Migrations never touch stored
  bytes and the app never deletes them, so a restored database finds every file it refers to.
- Deleting a thread removes its attachments from the app, but the bytes stay in the bucket.
  They may be health-related; they stay private until a later cleanup change removes
  unreferenced data. Abandoned unsent uploads also stay until then.
- Upload staging uses the container's temporary directory; nothing is kept on the volume.

## Rollouts and migrations

The Deployment uses one replica and the `Recreate` strategy: the previous server stops
before the new pod starts. This avoids overlapping database owners during normal rollouts.
Expect downtime while the volume attaches, the backup runs, and the application initializes.
Do not add replicas, additional workers, or an autoscaler to this SQLite deployment.
See [Kubernetes deployment strategies](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/#recreate-deployment).

The startup sequence is:

1. The `database-backup` init container uses the same application image and ownership lock.
   It skips backups for a missing database or an already-current schema. For pending upgrades,
   it uses SQLite's backup API and checks the result for integrity and foreign-key violations.
   A backup failure blocks the application container from starting. Unsupported versions fail.
2. The server applies pending migrations using the shared migration runner, then recovers
   interrupted runs. All schema and persistent data changes use that runner.
3. `/api/health` becomes available after successful application startup. The startup probe
   allows about ten minutes before restarting the application container; readiness and
   liveness probes wait for startup success. Init containers have their own lifecycle and
   are not bounded by this probe. See [Kubernetes probe documentation](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/).

`deployment/deploy.sh` renders the application and selected image in a temporary directory and
applies it without modifying repository files. It waits up to 20 minutes for rollout readiness,
returning a failure if the rollout times out or exceeds its progress deadline. Applying YAML
alone does not mean the new application is running. There is no automatic rollback.

Backups are stored under `/app/data/backups` with source/target schema versions and unique
timestamps. They are not overwritten or automatically deleted. Monitor disk usage and copy
important backups off the disk. These local copies protect against a bad migration; they do
not protect against losing the persistent disk. Scheduled off-disk backups remain pending.

## Failed upgrades and recovery

Inspect the pod events, `database-backup` init-container logs, application logs, and rollout
status. A failed migration rolls back its own changes; earlier successful migrations remain
committed. The new server stays unavailable. Fix the migration and deploy a corrected image
to resume from the last committed version. See [database migrations](database-migrations.md).

Before reverting an image, check its supported schema version. Reverting the Deployment does
not undo migrations; an older image rejects a newer database. For a restore:

1. Stop the application and wait for the pod to terminate. Suspend the summary CronJob during
   maintenance so scheduled requests do not repeatedly fail.
2. Mount the existing PVC in a maintenance pod as UID/GID 1000. Preserve the current database
   and its WAL/SHM files separately before replacing anything.
3. Verify the chosen backup and restore it as `conversations.sqlite3`. Do not leave WAL/SHM
   files from the replaced database beside the restored file. Set ownership to 1000:1000.
4. Start an image compatible with the restored schema, wait for readiness, and resume the
   summary CronJob. Restoring loses writes made after the backup.

Restore operations are deliberate maintenance; no automatic restore script is provided.
Rolling back the attachment feature is also manual: either restore the pre-upgrade backup and
lose later writes, or roll forward with a fix. The bucket and its objects are left in place.
If there are chats on an existing pod's container filesystem, export them consistently before
the first rollout with this PVC. Mounting a new volume does not copy the old container data.

## Intervals.icu MCP server

The application reads training data through the
[futureweb/intervals-mcp-server](https://github.com/futureweb/intervals-mcp-server) image,
deployed from its own Kustomize root with `make deploy-k8s-mcp`. Keeping it separate means
application deploys and deletes leave it alone. The image is published and pinned by digest,
so there is nothing to build. The deployment expects the
`intervals-api-secret` and `intervals-mcp-basic-auth` secrets to exist.

The ingress basic auth guards the public endpoint, so the server's own OAuth stays off, which
also keeps the server stateless. Leave `MCP_PUBLIC_URL` unset: it enables Host header checks that
would reject the in-cluster service name the application uses.

## Removing the application

`deployment/delete.sh` removes the application workload, service, ingress, certificate, and
summary CronJob by deleting only `deployment/k8s/app/`. The PVC and StorageClass remain.
Reapplying the application manifests reuses the claim. Storage removal is a separate manual
maintenance operation; there is no storage deletion script. Deleting the storage manifests
would remove the PVC and leave the retained disk requiring manual recovery.
