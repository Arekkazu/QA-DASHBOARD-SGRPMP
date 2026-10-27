"""Chequeo del desempate de reportes: gana el del commit mas reciente aunque
el mtime diga lo contrario (lo que pasa tras un git clone).

    python3 test_scanner.py
"""
import json
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


# resultado.json de las corridas RUN-<grupo>-V<n>-<fecha>/ (M03 RF-58/59)
def _run(casos):
    return scanner._parse_resultado_run(json.dumps({"grupo": "G", "casos": casos}))


assert _run({"TC-1": {"estado": "APROBADO"}, "TC-2": {"estado": "RECHAZADO", "motivo": "x"}})["failed"] == 1
assert _run({"TC-1": {"estado": "APROBADO"}, "TC-2": {"estado": "APROBADO"}}) == {"total": 2, "passed": 2, "failed": 0, "errores": []}
# NO_CONCLUYENTE o sin veredicto (ERROR_QA) no aprueba: total 0 => Pendiente
assert _run({"TC-1": {"estado": "NO_CONCLUYENTE"}})["total"] == 0
assert _run({"TC-1": {"estado": "APROBADO"}, "TC-2": {"estado": None, "estado_tecnico": "ERROR_QA"}})["total"] == 0
# un RECHAZADO manda aunque haya casos pendientes
assert _run({"TC-1": {"estado": "NO_CONCLUYENTE"}, "TC-2": {"estado": "RECHAZADO"}})["failed"] == 1
# otros resultado.json (sin "grupo", o con casos como texto) no deben romper el escaneo
assert scanner._parse_resultado_run('{"casos": {"TC-1": {"estado": "APROBADO"}}}') is None
assert scanner._parse_resultado_run('{"grupo": "G", "casos": ["texto", "otro"]}') is None
print("ok resultado-run")
