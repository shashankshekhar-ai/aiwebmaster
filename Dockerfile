FROM python:3.13-slim

# docker CLI + compose plugin (talks to the host daemon via the mounted socket)
# + git/openssh for the git action. Debian trixie's docker.io apt package only
# ships dockerd (no client, no docker-compose-plugin), so both the docker CLI
# and the compose v2 plugin binaries are fetched directly from upstream releases.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git openssh-client ca-certificates curl postgresql-client \
    && curl -fsSL -o /tmp/docker.tgz https://download.docker.com/linux/static/stable/x86_64/docker-27.3.1.tgz \
    && tar -xzf /tmp/docker.tgz -C /usr/local/bin --strip-components=1 docker/docker \
    && rm /tmp/docker.tgz \
    && mkdir -p /usr/libexec/docker/cli-plugins \
    && curl -fsSL -o /usr/libexec/docker/cli-plugins/docker-compose \
       https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-x86_64 \
    && chmod +x /usr/libexec/docker/cli-plugins/docker-compose \
    && rm -rf /var/lib/apt/lists/* \
    # /repo is a bind mount owned by the host user; this container runs as
    # root, so git (>=2.35) refuses to operate on it as "dubious ownership"
    # unless explicitly trusted.
    && git config --system --add safe.directory /repo \
    # git commit refuses to run at all with no identity configured — the
    # `git` action (core/executors.py::run_git) failed outright with
    # "Author identity unknown" until this was added, confirmed by testing.
    # Every commit through this app already goes through the propose/
    # approve/RBAC/audit pipeline — this identity is just the git-level
    # committer field, not a substitute for that audit trail.
    && git config --system user.name "AIwebmaster" \
    && git config --system user.email "aiwebmaster@thebradburygroup.com"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8010

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"]
