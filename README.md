# ContextPress

A small static-site generator for a personal engineering blog. Markdown posts
with YAML frontmatter go in; a self-contained static site (HTML/CSS + RSS) comes
out. nginx serves it — there is **no server-side runtime** in production.

- **Theme:** `02-technical-terminal` (dark-first terminal aesthetic, light mode
  via `prefers-color-scheme`).
- **Content model:** one Markdown file per post with a `---` YAML frontmatter
  block (`content/posts/*.md`). Global config lives in `content/site.yaml`.
- **Rendering:** Jinja2 templates + `markdown-it-py` + Pygments code
  highlighting, validated through Pydantic models at build time.

## For engineers — start here

If you're reviewing the code (not the prose), these are the files that matter:

| File | What it does |
|------|--------------|
| [`build.py`](build.py) | Static-site generator: orchestrates load → render → write `dist/`, plus RSS. |
| [`app/content.py`](app/content.py) | Loads `site.yaml` + posts, validates, renders Markdown (Pygments highlight, page bundles, drafts). |
| [`app/models.py`](app/models.py) | Pydantic models — the content schema; malformed input fails the build loudly. |
| [`themes/`](themes/) | Presentation only (Jinja2 templates + CSS), one self-contained bundle per theme. |
| [`Dockerfile`](Dockerfile) / [`docker-compose.yaml`](docker-compose.yaml) | Containerized build + nginx serving. |

Everything under [`content/`](content/) is the blog's writing, deliberately kept
separate from the engine. Commit messages are prefixed `content:` for posts and
`feat:` / `fix:` for engine changes, so `git log -- app themes build.py` shows
only the engineering history.

## Layout

```
build.py                 # static site generator (entrypoint)
app/
  models.py              # Pydantic models (SiteConfig, Post, ...)
  content.py             # load site.yaml + posts, validate, render markdown
themes/                  # one bundle per theme (templates + assets together)
  02-technical-terminal/
    templates/           # base.html, index.html, post.html, partials/
    static/css/style.css # this theme's stylesheet
content/
  site.yaml              # title, theme, nav, social, about
  ai.yaml                # optional build-time AI server (env-substituted)
  posts/*.md             # posts (Markdown + YAML frontmatter)
  posts/_drafts/         # unfinished posts — gitignored, local-only
.env.example             # template for .env (secrets; .env is gitignored)
Dockerfile               # builder image (python + deps) that renders the site
docker-compose.yaml      # on-demand build service + always-on nginx
nginx.conf               # nginx server config (bind-mounted into the container)
```

A **theme** is a self-contained bundle under `themes/<name>/` holding its own
`templates/` and `static/`. The `theme:` field in `site.yaml` selects which
bundle the build renders with — swapping themes is a one-line change. The public
asset URL stays `/static/css/style.css` regardless of which theme is active.

## Commands

A `Makefile` wraps the everyday commands — run `make` to list them:

| Command | Does |
|---------|------|
| `make serve` | Build and preview locally at `http://localhost:8000` |
| `make drafts` | Same, including `_drafts/` |
| `make build` | Render `./dist` (local Python) |
| `make gen` | Render `./dist` via the builder container |
| `make up` / `make down` | Start / stop nginx |
| `make deploy` | Pull + rebuild + ensure nginx up (on the server) |

The raw commands are below if you'd rather not use `make`.

## Develop locally

```bash
python -m venv .venv
. .venv/Scripts/activate      # Windows;  ".venv/bin/activate" on macOS/Linux
pip install -r requirements.txt
python build.py               # writes ./dist
python -m http.server -d dist 8000   # preview at http://localhost:8000
```

Add `--drafts` to also build posts under `content/posts/_drafts/` (and any post
marked `draft: true` in frontmatter).

## Add a post

A post is either a **flat file** or a **page bundle**:

```
content/posts/
  my-quick-note.md              # flat: text-only post, slug = "my-quick-note"
  my-illustrated-post/          # bundle: slug = "my-illustrated-post"
    index.md                    #   the post
    diagram.svg                 #   assets live beside index.md
    data.csv
```

Use a bundle when the post has images or downloads. Reference them with a
**relative** path — they're copied next to the post's `index.html`, so
`/posts/<slug>/diagram.svg` resolves without any `base_url`:

```markdown
![A diagram](diagram.svg)
[Download the data](data.csv)
```

Either way the file (or `index.md`) starts with YAML frontmatter:

```markdown
---
title: "My post title"
date: 2026-08-07
tags: [systems]
description: "One-line summary shown on the index and in RSS."
---

Body in **Markdown**. Code blocks are syntax-highlighted.
```

`slug` defaults to the filename (flat) or the directory name (bundle);
`reading_time` is auto-computed if omitted.

### Drafts

Work in progress goes in `content/posts/_drafts/` (a flat `.md` or a bundle dir,
same as published posts). That folder is **gitignored**, so unfinished writing
never reaches the public repo. Preview it locally with `python build.py --drafts`
(or `docker compose run --rm build python build.py --out dist --drafts`). When a
draft is ready, move it up to `content/posts/`.

## Site configuration (`site.yaml`)

Global settings live in `content/site.yaml`:

| Key | Purpose |
|-----|---------|
| `title`, `tagline`, `author`, `base_url` | Identity + absolute-URL base (RSS, canonical, sitemap). |
| `theme` | Which `themes/<name>/` bundle to render with. |
| `home` | Template that renders `/` — `index` (blog home) or `coming-soon` (splash). |
| `favicon` | Optional URL override. Omit to use the **theme's** icon (`themes/<name>/static/favicon.svg`). |
| `robots.allow` | `false` → `robots.txt` blocks all crawlers **and** every page gets `<meta name="robots" content="noindex,nofollow">`. |
| `robots.sitemap` | Emit `/sitemap.xml` (and reference it from `robots.txt`). |
| `nav`, `social`, `about` | Nav links, contact links, and the "whoami" blurb (Markdown). |

### Landing page & the coming-soon phase

`home` decouples "which page is `/`" from "which theme." To launch behind a
splash and flip to the blog with no redeploy:

```yaml
# pre-launch
home: "coming-soon"
robots:
  allow: false        # keep it out of search results until you're ready
```

```yaml
# launch day — rebuild and you're live
home: "index"
robots:
  allow: true
```

Each theme provides its own `templates/coming-soon.html`, so the splash matches
the active theme. (If you host the splash at a different domain than the blog,
that's just a second deployment — see below.)

### Favicon

The icon is **theme-controlled**: drop `favicon.svg` (or `.ico`/`.png`) in the
theme's `static/`, and `base.html` links it at `/static/favicon.svg`. Switching
themes switches the icon automatically. Set `favicon:` in `site.yaml` only to
override.

## AI server (`content/ai.yaml`)

An optional build-time AI server (LM Studio or any OpenAI-compatible API), for
build steps like generating a semantic-search index. Configure it in
[`content/ai.yaml`](content/ai.yaml):

- `provider` — `lmstudio` or `openai` (OpenAI-compatible)
- `scheme` / `host` / `port` — connection; blank `port` defaults to 443 (https)
  or 80 (http)
- `api_key` — **source from `.env`, never hard-code**
- `models` — one per role: `embeddings`, `chat`, `vision`, `tools`, `rerank`,
  `code`, `transcription`, `moderation`

Every value supports **`${VAR-default}` / `${VAR:-default}` substitution**
(Docker/shell style), resolved from the environment at build time. So the
committed `ai.yaml` holds only placeholders; real values — and the API key —
live in a gitignored `.env`:

```yaml
# content/ai.yaml (committed — no secrets)
provider: "${AI_PROVIDER-lmstudio}"
host: "${AI_HOST-localhost}"
port: "${AI_PORT-}"
api_key: "${AI_API_KEY-}"
```

```bash
# .env (gitignored — real values)
AI_PROVIDER=openai
AI_API_KEY=sk-...
```

`build.py` loads `.env` automatically; the Docker `build` service reads it via
`env_file`. Copy [`.env.example`](.env.example) to `.env` to start. Nothing from
`ai.yaml` (including the key) is rendered into the site.

## Secrets & committing

The repo is public (it's your code showcase), and **git history is forever** —
anything committed, even if deleted later, stays in history. So:

- **Real secrets go in `.env`** (gitignored), referenced via `${VAR}` in config.
  Never hard-code keys, tokens, or passwords in tracked files.
- **Before every commit, review what you're staging for secrets:**
  ```bash
  git diff --cached          # read the actual staged changes
  git status                 # confirm .env / key files are NOT staged
  ```
- **Never commit:** `.env`, API keys/tokens, private keys, passwords, the
  runner registration token, or internal hostnames/IPs you don't want public.
- Committed templates end in `.example` and contain **placeholders only**.

If a secret does get committed, treat it as compromised: **rotate/revoke it**,
then scrub history (`git filter-repo`) — deleting the file in a new commit is
not enough.

## Build & run with Docker

Two services (see [`docker-compose.yaml`](docker-compose.yaml)):

- **`build`** — an on-demand Python container that renders the site into `./dist`.
  No Python needed on the host. Not started by `up`.
- **`web`** — always-on `nginx` that **bind-mounts `./dist`**. Because the files
  are mounted (not baked into the image), regenerating `./dist` is served
  immediately — **no container restart**.

First run:

```bash
docker compose build build        # build the builder image (once)
docker compose run --rm build     # render ./dist
docker compose up -d web          # start nginx
```

Publish new content (note: no `up`/restart):

```bash
# edit content/posts/... then:
docker compose run --rm build     # regenerate ./dist — nginx serves it live
```

On the server, [`deploy.sh`](deploy.sh) wraps the whole update — `git pull`,
rebuild `./dist`, ensure nginx is up — into one command:

```bash
./deploy.sh
```

See **[DEPLOY.md](DEPLOY.md)** for first-time server setup (NPM + Cloudflare) and
push-to-deploy automation via a Gitea Action that SSHes in and runs `deploy.sh`.

Both images use **pinned base tags** (`python:3.12-slim`, `nginx:1.27-alpine`)
with `pull_policy` set so they're pulled once and reused from the local cache
rather than re-fetched from Docker Hub. `web` attaches to the external
`nginx-proxy-manager_default` network and only `expose`s port 80 to it — TLS and
routing are handled by NPM / Cloudflare in front.

> **Trade-off:** the nginx image is *not* self-contained — the site lives in the
> bind-mounted `./dist`, so a deploy means having the repo on the host and
> running the `build` service there. That's the cost of live, restart-free
> updates. (On Linux the build container writes `./dist` as root; `chown` it to
> your user if that matters.)
