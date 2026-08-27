# Deploying ContextPress

The site is a static bundle served by nginx behind Nginx Proxy Manager (NPM),
fronted by Cloudflare, at the apex domain **`ramonecung.com`**. nginx is the
only thing serving requests in production — there is no application runtime.

Each deploy renders into `./dist`, then **promotes** it into the live webroot
`./dist-webroot` in place (nginx bind-mounts `./dist-webroot`), so the swap is
picked up live with **no container restart**. The version being replaced is
archived to a dated `./dist-webroot-YYYY-MM-DD/` for rollback, and old backups
are pruned:

```
./dist/                    fresh build output (overwritten every deploy)
./dist-webroot/            the live site nginx serves
./dist-webroot-YYYY-MM-DD/ dated backups of prior live versions (rollback)
```

A failed build never reaches the webroot: the last good `./dist-webroot` keeps
serving.

- **First-time server setup** — clone, build, run, wire up NPM + Cloudflare.
- **Manual deploy** — one command on the box.
- **Automated deploy** — push to `main`, a Gitea Action SSHes in and deploys.
- **Rollback** — restore a dated backup, no rebuild.

---

## First-time server setup

On the VPS (paths below assume `/docker/ContextPress` — adjust to taste):

```bash
cd /docker
git clone https://git.ramonecung.com/ramonecung/ContextPress.git
cd ContextPress

docker compose build build        # build the builder image (once)
./deploy.sh                       # render ./dist, promote to ./dist-webroot, start nginx
```

`deploy.sh` creates `./dist-webroot` on the first run, so nginx has something to
serve. For unattended `git pull`, give the box **read-only** access to the repo
— a Gitea **deploy token** in the clone URL, or a deploy key.

### NPM proxy host

In the NPM UI, point the `ramonecung.com` Proxy Host at this container:

- **Domain:** `ramonecung.com`
- **Scheme** `http` → **Forward Hostname** `contextpress` → **Port** `80`
  (NPM resolves the container by name on the shared `nginx-proxy-manager_default`
  network.)
- **SSL:** Cloudflare terminates TLS at the edge, so NPM can stay on HTTP here,
  or issue a Let's Encrypt cert via the Cloudflare DNS-01 method.

Cutting the apex over is just repointing that proxy host's upstream to
`contextpress` (from whatever served `ramonecung.com` before). Rolling back is
repointing it back.

### Cloudflare

The apex `ramonecung.com` already routes to NPM, so nothing changes at the
Cloudflare layer — only the NPM upstream above. Then load
`https://ramonecung.com` to verify.

---

## Manual deploy

On the server:

```bash
cd /docker/ContextPress
./deploy.sh        # git pull -> render ./dist -> promote to ./dist-webroot -> nginx up
```

`deploy.sh` uses `git pull --ff-only` (never a surprise merge), takes a `flock`
so overlapping runs can't collide, and keeps the newest `KEEP` dated backups
(default 7; override with `KEEP=N ./deploy.sh`).

## Rollback

Restore a dated backup into the live webroot — no rebuild, picked up live:

```bash
cd /docker/ContextPress
ls -1dt dist-webroot-*/            # list backups, newest first
rsync -a --delete dist-webroot-2026-08-27/ dist-webroot/
```

For a full revert (back to whatever served the apex before), repoint the
`ramonecung.com` upstream in NPM instead.

---

## Automated deploy — Gitea Action over SSH

Push to `main` → the [`deploy`](.gitea/workflows/deploy.yml) workflow SSHes into
the server and runs `deploy.sh` **on the host**. Running on the host (not inside
the job container) avoids the docker-compose-from-container path problem, and the
CI side only needs SSH-out.

### 1. Enable Actions + register a runner (one-time)

- Admin: `[actions] ENABLED = true` in `app.ini` (default in Gitea ≥ 1.21), and
  enable Actions in this repo's settings.
- Run the runner **on the deploy server** using the declarative setup in
  [`runner/`](runner/) (`cp .env.example .env`, paste a token, `docker compose
  up -d`). Running it on the deploy box is what lets the job SSH to `172.17.0.1`
  and dodge the Tailscale-SSH ACL — see [runner/README.md](runner/README.md).

### 2. Create a deploy SSH key, locked to one command

```bash
ssh-keygen -t ed25519 -f deploy_key -C gitea-deploy -N ''
```

On the server, add the **public** key to the deploy user's
`~/.ssh/authorized_keys` with a **forced command**, so this key can only ever run
the deploy — even if it leaks:

```
command="cd /docker/ContextPress && ./deploy.sh",no-port-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAA...deploy_key gitea-deploy
```

### 3. Add repo Actions secrets

**Repo → Settings → Actions → Secrets:**

| Secret | Value |
|--------|-------|
| `DEPLOY_SSH_KEY` | the **private** key (`deploy_key` contents) |
| `DEPLOY_HOST` | the server address the runner can reach (see reachability below) |
| `DEPLOY_USER` | the deploy user on the server |

The workflow is already committed — nothing else to add in the repo.

### 4. Reachability (important)

Tailscale SSH on this node is denied by tailnet policy, and it intercepts port 22
on the tailscale interface. The runner needs a real path to the server's `sshd`:

- **Simplest:** run the runner **on the VPS** and set `DEPLOY_HOST` to the Docker
  bridge gateway (`172.17.0.1`) or the host's LAN IP. A job container connecting
  that way reaches the host's real `sshd`, bypassing Tailscale SSH's ACL.
- **Or** add a Tailscale ACL/`ssh` grant for the runner node → VPS and use the
  tailnet name.

Confirm `ssh` works from the runner to the server before relying on it.

### 5. Test

Push a trivial change to `main` (or use **Run workflow** if enabled) and watch
the run under the repo's **Actions** tab. On success the site updates with no
container restart.

---

## Choosing an approach

| Approach | Standing infra | Instant? | Notes |
|----------|----------------|----------|-------|
| Gitea Action + SSH | `act_runner` (Docker) | yes | CI logs/retries; key locked to one command |
| `webhook` host service | one small binary | yes | Lightest listener; HMAC-verified |
| cron poll | none | ~2-min lag | `git fetch` + `deploy.sh` when `main` moves |

All three ultimately run the same `deploy.sh`.
