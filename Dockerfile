FROM python:3.13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server.py scanner.py dashboard.html asignaciones.csv declarados_sin_evidencia.csv incidencias.csv ./

# /data debe ser un volumen persistente en Dokploy: ahi quedan los repos
# clonados (rama test) y el ultimo escaneo, asi un redeploy no re-clona todo.
ENV HOST=0.0.0.0 \
    REPOS_DIR=/data/repos \
    CACHE_FILE=/data/scan.json \
    REPO_BRANCH=test \
    ACTUALIZAR_CADA_MIN=15 \
    TZ=America/Bogota \
    PYTHONUNBUFFERED=1

EXPOSE 8899
CMD ["python", "server.py", "8899"]
