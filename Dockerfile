# syntax=docker/dockerfile:1

# Builder image: Python + build dependencies only. Source is NOT baked in — it
# is bind-mounted at run time (see the `build` service in docker-compose.yaml),
# so editing content/templates never requires rebuilding this image. Rebuild it
# only when requirements.txt changes.
#
# Pinned tag (not :latest) so rebuilds reuse the locally-cached base image and
# only hit Docker Hub the first time this exact tag is needed.
FROM python:3.12-slim
WORKDIR /src

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.txt

# Default: render the site into /src/dist. The `build` service mounts the repo
# at /src and ./dist at /src/dist, so this writes straight to the host's ./dist.
CMD ["python", "build.py", "--out", "dist"]
