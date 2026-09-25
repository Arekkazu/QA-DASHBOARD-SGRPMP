"""
Servidor minimo (solo libreria estandar) para el panel de QA.

Sirve dashboard.html y expone:
  GET  /api/data        -> ultimo escaneo guardado en CACHE_FILE (no reescanea)
  POST /api/actualizar  -> revisa los repos, escanea de nuevo y guarda el cache

El escaneo tambien se repite solo al arrancar y cada ACTUALIZAR_CADA_MIN minutos.

Dos modos, segun exista la variable REPOS_DIR:
  - Local (sin REPOS_DIR): lee los repos que estan al lado de qa-dashboard/ y
    solo AVISA si no existen o no estan en la rama test -- nunca los toca,
    porque son las copias de trabajo de alguien.
  - Docker/Dokploy (REPOS_DIR=/data/repos): clona los repos ahi (rama test) y
    en cada actualizacion los lleva a lo ultimo de origin/test.

Uso local:
    python3 server.py [puerto]     # por defecto 8899
    Abrir http://localhost:8899 en el navegador.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import scanner

AQUI = Path(__file__).resolve().parent
DASHBOARD_HTML = AQUI / "dashboard.html"
CACHE = Path(os.environ.get("CACHE_FILE") or AQUI / "scan_cache.json")
SINCRONIZAR = bool(os.environ.get("REPOS_DIR"))
RAMA = os.environ.get("REPO_BRANCH", "test")
CADA_MIN = float(os.environ.get("ACTUALIZAR_CADA_MIN", "15"))
# Carpeta destino -> URL. El nombre de carpeta es el que espera scanner.py.
REPOS = {
    "sgpmp-backend": os.environ.get("BACKEND_REPO_URL", "https://github.com/Arekkazu/sgpmp-backend.git"),
    "SGPMP-FRONT-END-PWA": os.environ.get("FRONTEND_REPO_URL", "https://github.com/Arekkazu/SGPMP-FRONT-END-PWA.git"),
}

_lock = threading.Lock()
_error_inicial = None  # si el primer escaneo falla, /api/data lo muestra en vez de "en curso"


def _git(*args, cwd=None) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise RuntimeError(f"git {args[0]}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout.strip()


def _sincronizar(destino: Path, url: str):
    if not (destino / ".git").exists():
        shutil.rmtree(destino, ignore_errors=True)  # restos de un clone cortado
        destino.parent.mkdir(parents=True, exist_ok=True)
        # Historial completo (scanner.py desempata reportes por fecha de commit)
        # pero sin blobs viejos: solo se bajan los archivos de la punta de test.
        _git("clone", "--filter=blob:none", "--branch", RAMA, "--single-branch", url, str(destino))
        return
    _git("fetch", "origin", RAMA, cwd=destino)
    _git("checkout", "-f", "-B", RAMA, "FETCH_HEAD", cwd=destino)


def _estado_repo(nombre: str, url: str) -> dict:
    destino = scanner.ROOT / nombre
    estado = {"repo": nombre, "rama_esperada": RAMA}
    errores = []
    if SINCRONIZAR:
        try:
            _sincronizar(destino, url)
        except Exception as exc:  # p. ej. GitHub caido: se sigue con la copia que haya
            errores.append(f"no se pudo actualizar ({exc})")
    try:
        if not (destino / ".git").exists():
            raise RuntimeError(f"no se encontro {destino}")
        rama = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=destino)
        commit, fecha, titulo = _git("log", "-1", "--format=%h%n%ci%n%s", cwd=destino).split("\n", 2)
        estado.update(rama=rama, commit=commit, fecha_commit=fecha, titulo_commit=titulo)
        if rama != RAMA:
            errores.append(f"esta en la rama {rama}, no en {RAMA}")
    except Exception as exc:
        errores.append(str(exc))
    estado["ok"] = not errores
    if errores:
        estado["error"] = "; ".join(errores)
    return estado


def actualizar():
    """Sincroniza/revisa repos, escanea y guarda el cache. Si ya hay una
    actualizacion en curso, espera a que termine en vez de repetirla."""
    if not _lock.acquire(blocking=False):
        with _lock:
            return
    try:
        repos = [_estado_repo(n, u) for n, u in REPOS.items()]
        data = scanner.scan()
        data["repos"] = repos
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CACHE)
    finally:
        _lock.release()


def _actualizar_periodicamente():
    global _error_inicial
    while True:
        try:
            actualizar()
        except Exception as exc:
            _error_inicial = str(exc)
            print(f"Actualizacion fallida: {exc}", flush=True)
        if CADA_MIN <= 0:
            return
        time.sleep(CADA_MIN * 60)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        ruta = self.path.split("?")[0]
        if ruta in ("/", "/index.html"):
            self._responder(200, DASHBOARD_HTML.read_bytes(), "text/html; charset=utf-8")
        elif ruta == "/api/data":
            if CACHE.exists():
                self._responder(200, CACHE.read_bytes())
            else:
                self._json(500 if _error_inicial else 503,
                           {"error": _error_inicial or "Primer escaneo en curso (clonando repos). Espera un momento…"})
        else:
            self._responder(404, b"", "text/plain")

    def do_POST(self):
        if self.path.split("?")[0] != "/api/actualizar":
            return self._responder(404, b"", "text/plain")
        try:
            actualizar()
            self._responder(200, CACHE.read_bytes())
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def _json(self, status: int, obj: dict):
        self._responder(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _responder(self, status: int, body: bytes, content_type="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    puerto = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    host = os.environ.get("HOST", "127.0.0.1")
    threading.Thread(target=_actualizar_periodicamente, daemon=True).start()
    servidor = ThreadingHTTPServer((host, puerto), Handler)
    print(f"Panel QA disponible en http://localhost:{puerto}  (Ctrl+C para detener)", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nDetenido.")


if __name__ == "__main__":
    main()
