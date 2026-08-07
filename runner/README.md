# act_runner (push-to-deploy)

A Gitea Actions runner for ContextPress, defined declaratively so it never has
to be reverse-engineered from a `docker run`. It runs on the **deploy server**
(the DigitalOcean droplet), alongside the site.

On push to `main`, the [`deploy` workflow](../.gitea/workflows/deploy.yml) starts
a job in a container on this runner; that job SSHes to **this host**
(`172.17.0.1`, the Docker bridge → the host's real `sshd`) and runs
[`../deploy.sh`](../deploy.sh). Running on the host avoids the
docker-compose-from-container path problem, and hitting `172.17.0.1` (the
`docker0` interface) bypasses the Tailscale-SSH ACL. Full context: [../DEPLOY.md](../DEPLOY.md).

## First-time setup

1. In Gitea: **Repo → Settings → Actions → Runners → Create new runner**, copy
   the token.
2. On the deploy server:
   ```bash
   cd /docker/ContextPress/runner
   cp .env.example .env
   # paste the token into .env as GITEA_RUNNER_REGISTRATION_TOKEN=...
   docker compose up -d
   ```
3. Confirm the runner shows up as **Idle** under Settings → Actions → Runners
   (it registers as `cloud-digitalocean-01-runner`).

The token is one-time: it's exchanged for permanent credentials written to
`./data/.runner` on first run, then ignored. You can blank it or delete `.env`
afterward.

## Durability

The runner's identity lives in `./data` (a **bind mount**, gitignored), not in
the container:

- Container crash / host reboot → restarted by `restart: unless-stopped`
  (ensure `systemctl enable docker`).
- `docker container prune` → running containers aren't pruned.
- `docker volume prune` / `system prune --volumes` → can't touch a bind mount.
- Recreate the container (`docker compose up -d` after `down`/upgrade) → reads
  `./data/.runner` back, **no re-registration**.

The only way to lose the identity is deleting `./data`. It's a few hundred
bytes — back up `./data/.runner` once. If it is lost, delete the stale runner in
Gitea (Settings → Actions → Runners) and repeat first-time setup with a new
token.

## Notes

- **Label mapping:** `ubuntu-latest` → `catthehacker/ubuntu:act-22.04`, which
  ships an `ssh` client (the deploy job needs it). Changing the label requires
  clearing `./data` and re-registering.
- **Privilege:** the runner mounts the Docker socket to launch job containers —
  that's root-equivalent on this host. Standard for CI; keep the box locked down.
- **Reaching Gitea:** the container must resolve and reach
  `https://git.ramonecung.com`. If that's a MagicDNS/tailnet-only name that
  doesn't resolve inside the container, point `GITEA_INSTANCE_URL` at the
  tailnet IP or give the container the host's resolver.
- **Upgrades:** bump the pinned `gitea/act_runner` tag in `compose.yaml`, then
  `docker compose up -d`. Identity in `./data` carries over.
