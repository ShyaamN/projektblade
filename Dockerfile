# Scorer builds linux/amd64. Digest pins the labeling environment.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

WORKDIR /app

# Permitted static maps from pinned sqlglot (no patch→answer tables).
COPY maps/impact.json /app/maps/impact.json
COPY maps/coverage.json.gz /app/maps/coverage.json.gz
COPY selector.py /app/selector.py

# Runtime user is nobody — maps must be world-readable.
RUN chmod -R a+rX /app

USER nobody
CMD ["python", "/app/selector.py"]
