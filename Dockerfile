# Two stages, because the app needs Node to build and not to run.
#
# The front end lives in `web/` and is compiled to `kpi_maker/ui_dist/`, which
# is a build artifact and therefore not committed. Before this existed, the
# Render blueprint only ran `pip install`, so a deploy shipped a server with no
# front end behind it — `GET /` answered 500 with "No front end at …". A native
# Python runtime cannot fix that without assuming Node is present in an image
# nobody controls; a build stage states the requirement instead of hoping.
#
# The same file is what makes the deploy reproducible anywhere else: the
# equivalent by hand is the two commands in the README, in this order.

FROM node:22-slim AS web
WORKDIR /build
# Dependencies first, so a source-only change does not re-resolve the tree.
COPY web/package.json web/package-lock.json ./web/
RUN npm --prefix web ci
COPY web/ ./web/
RUN npm --prefix web run build


FROM python:3.11-slim
WORKDIR /app

# Layer the dependency install ahead of the source copy for the same reason.
# `--no-cache-dir` keeps the image from carrying pip's download cache, which is
# larger than the wheels it installed.
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY kpi_maker/ ./kpi_maker/
COPY samples/ ./samples/
COPY tools/ ./tools/

# The compiled front end, from the stage above. `vite build` writes it here, so
# the path matches what `kpi_maker/api/server.py` serves without special-casing
# the container.
COPY --from=web /build/kpi_maker/ui_dist/ ./kpi_maker/ui_dist/

# Runs are ephemeral on a free host and the disk is wiped on every deploy, but
# they still have to land somewhere writable that is not the read-only source
# tree. Same hook the browser smoke test uses.
ENV MASTERBI_RUNS_DIR=/var/lib/masterbi/runs \
    PYTHONUNBUFFERED=1
RUN mkdir -p /var/lib/masterbi/runs

# $PORT is injected by the host. 0.0.0.0 is required — 127.0.0.1 would only be
# reachable from inside the container.
ENV PORT=8000
CMD ["sh", "-c", "uvicorn kpi_maker.api.server:app --host 0.0.0.0 --port ${PORT}"]
