"""Chequeo del desempate de reportes: gana el del commit mas reciente aunque
el mtime diga lo contrario (lo que pasa tras un git clone).

    python3 test_scanner.py
"""
import os
import subprocess
import tempfile
from pathlib import Path

import scanner

NEWMAN = '<h6>Total Assertions</h6><h1>1</h1><h6>Total Failed Tests</h6><h1>{}</h1>'


def git(*args, cwd, fecha):
    env = {**os.environ, "GIT_AUTHOR_DATE": fecha, "GIT_COMMITTER_DATE": fecha,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)


with tempfile.TemporaryDirectory() as tmp:
    raiz = Path(tmp)
    res = raiz / "Test_Modulo1" / "RF-01" / "TC-M01-001" / "resultados"
    res.mkdir(parents=True)
    git("init", "-q", cwd=raiz, fecha="2026-01-01T00:00:00")
    (res / "viejo.html").write_text(NEWMAN.format(1))      # corrida vieja: fallo
    git("add", ".", cwd=raiz, fecha="2026-01-01T00:00:00")
    git("commit", "-qm", "1", cwd=raiz, fecha="2026-01-01T00:00:00")
    (res / "nuevo.html").write_text(NEWMAN.format(0))      # corrida nueva: paso
    git("add", ".", cwd=raiz, fecha="2026-02-01T00:00:00")
    git("commit", "-qm", "2", cwd=raiz, fecha="2026-02-01T00:00:00")
    os.utime(res / "viejo.html", (2e9, 2e9))                # mtime engañoso

    scanner.ROOT = raiz
    caso, = scanner._escanear_raiz(raiz, "backend", {}, {})
    assert caso.estado == "Aprobado", caso
    print("ok")
