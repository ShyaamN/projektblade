# Scorer builds linux/amd64. Digest pins the labeling environment.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

WORKDIR /app

# Build-time inputs only. The runtime opens a read-only pinned checkout.
COPY requirements.lock /opt/requirements.lock
RUN python -m pip install --disable-pip-version-check --no-cache-dir -r /opt/requirements.lock
RUN python -c 'import os, tarfile, urllib.request; sha = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"; url = "https://codeload.github.com/tobymao/sqlglot/tar.gz/" + sha; stream = urllib.request.urlopen(url, timeout=60); archive = tarfile.open(fileobj=stream, mode="r|gz"); archive.extractall("/opt", filter="data"); os.rename("/opt/sqlglot-" + sha, "/opt/sqlglot")'
RUN python -m compileall -q /opt/sqlglot/sqlglot /opt/sqlglot/tests

# Permitted static maps from pinned sqlglot (no patch→answer tables).
COPY maps/impact.json /app/maps/impact.json
COPY maps/coverage.json.gz /app/maps/coverage.json.gz
COPY maps/pinpoint.marshal.gz /app/maps/pinpoint.marshal.gz
COPY selector.py pinpoint.py verify.py /app/

# Runtime user is nobody — maps must be world-readable.
RUN chmod -R a+rX /app /opt/sqlglot

ENV PYTHONDONTWRITEBYTECODE=1 PYTHON_CPU_COUNT=2 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
USER nobody
CMD ["python", "/app/selector.py"]
