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


# M06: el reporte del grupo completo y los reruns de un subcaso se combinan por prueba;
# de cada una vale el reporte generado mas recientemente (TC-M06-G07/G08/G20 salian
# Rechazado aunque el rerun del subcaso fallido ya habia pasado).
import html as _html


def _pytest_html(generado, pruebas):
    """pruebas: {nombre: "Passed" | "Failed" | "Skipped"}"""
    blob = {"tests": {f"TC.py::{n}": [{"result": r, "log": "E   AssertionError: " + n if r == "Failed" else ""}]
                      for n, r in pruebas.items()}}
    return (f"<html><body><p>Report generated on {generado} by pytest-html</p>"
            f'<div id="data-container" data-jsonblob="{_html.escape(json.dumps(blob))}"></div></body></html>')


def _caso_m06(archivos, mtimes=None):
    with tempfile.TemporaryDirectory() as tmp:
        raiz = Path(tmp)
        tc = raiz / "Test_Modulo6" / "RF-82" / "TC-M06-G07"
        tc.mkdir(parents=True)
        for nombre, texto in archivos.items():
            (tc / nombre).write_text(texto)
            if mtimes:
                os.utime(tc / nombre, (mtimes, mtimes))
        scanner.ROOT = raiz
        caso, = scanner._escanear_raiz(raiz, "backend", {}, {})
        return caso


GRUPO = _pytest_html("13-Nov-2026 at 00:33:42", {"a": "Passed", "b": "Failed", "c": "Skipped"})
RERUN_B_OK = _pytest_html("13-Nov-2026 at 00:37:55", {"b": "Passed"})
RERUN_B_FALLA = _pytest_html("13-Nov-2026 at 00:37:55", {"b": "Failed"})
VIEJO = _pytest_html("07-Nov-2026 at 23:38:58", {"a": "Failed", "b": "Failed", "c": "Failed"})

# rerun aprobado: el subcaso fallido queda aprobado y el resto del grupo se conserva
caso = _caso_m06({"reporte-TC-M06-G07-V2.0.html": GRUPO, "reporte-TC-M06-G07-031c-V2.0.html": RERUN_B_OK}, mtimes=1e9)
assert (caso.estado, caso.passed, caso.failed, caso.total) == ("Aprobado", 2, 0, 3), caso
# la version anterior (sin V2.0) no aporta sus fallos
caso = _caso_m06({"reporte-TC-M06-G07.html": VIEJO, "reporte-TC-M06-G07-V2.0.html": GRUPO,
                  "reporte-TC-M06-G07-031c-V2.0.html": RERUN_B_OK}, mtimes=1e9)
assert caso.estado == "Aprobado", caso
# el orden lo da la fecha del reporte, no el nombre: un rerun que sigue fallando mantiene el rechazo
caso = _caso_m06({"reporte-TC-M06-G07-V2.0.html": GRUPO, "reporte-TC-M06-G07-031c-V2.0.html": RERUN_B_FALLA}, mtimes=1e9)
assert (caso.estado, caso.failed) == ("Rechazado", 1), caso
# un reporte generado ANTES no pisa al mas nuevo aunque su nombre ordene despues
GRUPO_TARDE = _pytest_html("13-Nov-2026 at 01:00:00", {"a": "Passed", "b": "Passed", "c": "Skipped"})
caso = _caso_m06({"reporte-TC-M06-G07-V2.0.html": GRUPO_TARDE, "reporte-TC-M06-G07-031c-V2.0.html": RERUN_B_FALLA}, mtimes=1e9)
assert caso.estado == "Aprobado", caso
print("ok fusion-m06")


# estado_oficial_<TC>.json (M06): el dictamen vigente de QA gana a reportes anteriores
def _caso_oficial(oficial, otros=None):
    with tempfile.TemporaryDirectory() as tmp:
        raiz = Path(tmp)
        res = raiz / "Test_Modulo6" / "RF-90" / "TC-M06-G112" / "resultados"
        res.mkdir(parents=True)
        for nombre, texto in (otros or {}).items():
            (res / nombre).write_text(texto)
        if oficial is not None:
            (res / "estado_oficial_G112.json").write_text(json.dumps({"caso": "TC-M06-G112", **oficial}))
        scanner.ROOT = raiz
        caso, = scanner._escanear_raiz(raiz, "backend", {}, {})
        return caso


VIEJO_RECHAZADO = json.dumps({"caso": "TC-M06-G112", "subcasos": [{"subcaso": "x", "estado": "RECHAZADO", "motivo": "m"}]})
# APROBADO gana a un resumen viejo RECHAZADO; un escenario fuera de alcance (pendiente) no lo deja Pendiente
caso = _caso_oficial({"dictamen_general": "APROBADO", "escenarios": [
    {"id": "A", "estado": "APROBADO"}, {"id": "B", "estado": "APROBADO"},
    {"id": "RF-93", "estado": "PENDIENTE_CASO_INDEPENDIENTE"}]}, {"resumen_ejecucion_TC-M06-G112.json": VIEJO_RECHAZADO})
assert (caso.estado, caso.passed, caso.failed, caso.tipo_reporte) == ("Aprobado", 2, 0, "estado-oficial"), caso
# alcance delimitado aprueba aunque el grupo completo no este aprobado (decision de QA, TC-M06-G104)
caso = _caso_oficial({"dictamen_general": "APROBADO_CON_ALCANCE_DELIMITADO", "grupo_completo_aprobado": False})
assert caso.estado == "Aprobado", caso
# RECHAZADO rechaza aunque haya un reporte viejo aprobado
caso = _caso_oficial({"dictamen_general": "RECHAZADO", "motivo": "falla"},
                     {"resumen_ejecucion_TC-M06-G112.json": json.dumps({"caso": "TC-M06-G112", "aprobados": 3})})
assert caso.estado == "Rechazado", caso
# BLOQUEADO u otro texto = Pendiente; sin dictamen se ignora el archivo y manda el resto
caso = _caso_oficial({"dictamen_general": "BLOQUEADO"}, {"resumen_ejecucion_TC-M06-G112.json": json.dumps({"caso": "TC-M06-G112", "aprobados": 3})})
assert caso.estado == "Pendiente", caso
caso = _caso_oficial({"nota": "sin dictamen"}, {"resumen_ejecucion_TC-M06-G112.json": json.dumps({"caso": "TC-M06-G112", "aprobados": 3})})
assert caso.estado == "Aprobado" and caso.tipo_reporte == "resumen-m06", caso
print("ok estado-oficial")
