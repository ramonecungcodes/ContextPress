# Deploying ContextPress

The site is a static bundle served by nginx behind Nginx Proxy Manager (NPM),
fronted by a Cloudflare Tunnel. Because `dist/` is **bind-mounted** into the
nginx container, publishing new content just means regenerating `dist/` — no
container restart.

- **First-time server setup** — clone, build, run, wire up NPM + Cloudflare.
- **Manual deploy** — one command on the box.
- **Automated deploy** — push to `main`, a Gitea Action SSHes in and deploys.

---

## First-time server setup

On the VPS (paths below assume `/docker/ContextPress` — adjust to taste):

```bash
cd /docker
git clone https://git.ramonecung.com/ramonecung/ContextPress.git
cd ContextPress

mkdir -p dist
docker compose build build        # build the builder image (once)
docker compose run --rm build     # render ./dist
docker compose up -d web          # start nginx on the npm network
```

For unattended `git pull`, give the box **read-only** access to the repo — a
Gitea **deploy token** in the clone URL, or a deploy key.

### NPM proxy host

In the NPM UI, add a Proxy Host:

- **Domain:** `blog.ramonecung.com`
- **Scheme** `http` → **Forward Hostname** `contextpress` → **Port** `80`
  (NPM resolves the container by name on the shared `nginx-proxy-manager_default`
  network.)
- **SSL:** Cloudflare terminates TLS at the edge, so NPM can stay on HTTP here,
  or issue a Let's Encrypt cert via the Cloudflare DNS-01 method.

### Cloudflare

- If your tunnel already routes `*.ramonecung.com` to NPM, just add a **DNS
  record** for `blog` (proxied).
- Otherwise add a tunnel public-hostname route: `blog.ramonecung.com` →
  `http://<npm-container>:80`.

Then load `https://blog.ramonecung.com` to verify.

---

## Manual deploy

On the server:

```bash
cd /docker/ContextPress
./deploy.sh        # git pull -> rebuild ./dist -> ensure nginx is up
```

`deploy.sh` uses `git pull --ff-only`, so it never does a surprise merge.

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
