# Kubernetes deployment

The manifests deploy one Python server with SQLite on GKE, with separate Kustomize roots:

- `deployment/k8s/app/`: Deployment, Service, Ingress, Certificate, and summary CronJob.
- `deployment/k8s/storage/`: PersistentVolumeClaim and StorageClass.

Render them locally with `make render-k8s` and `make render-k8s-storage`; neither contacts
a cluster. There is no combined root Kustomization that can delete both sets together.
The deployment scripts apply to the current kubectl context and namespace.

Build the image with `make build-docker` after `make bootstrap` and configuring
`deployment/.env`. The script runs `make build-web` locally before Docker packages the
generated assets; no Node build stage runs in Docker. Direct Docker builds require
`make build-web` first. Missing assets fail the image build; rebuilding avoids stale UI assets.
Image push and application deployment remain separate script operations.

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
If there are chats on an existing pod's container filesystem, export them consistently before
the first rollout with this PVC. Mounting a new volume does not copy the old container data.

## Removing the application

`deployment/delete.sh` removes the application workload, service, ingress, certificate, and
summary CronJob by deleting only `deployment/k8s/app/`. The PVC and StorageClass remain.
Reapplying the application manifests reuses the claim. Storage removal is a separate manual
maintenance operation; there is no storage deletion script. Deleting the storage manifests
would remove the PVC and leave the retained disk requiring manual recovery.
