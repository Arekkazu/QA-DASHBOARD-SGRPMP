"""
Escanea los resultados de pruebas de SGPMP (backend + frontend) y produce
un resumen agregado: % aprobado/rechazado/pendiente, agrupacion por RF,
errores mas frecuentes, y el contenido de la tabla de incidencias.

Solo usa la libreria estandar de Python (sin pip install) para poder
correr en cualquier maquina sin preparacion previa.

Formatos de reporte reconocidos dentro de cada carpeta Resultados/RESULTADOS:
- pytest-html autocontenido (bloque data-jsonblob en el HTML)
- Newman con reporter htmlextra (HTML con tarjetas "Total Assertions" / "Total Failed Tests")
- JSON propio de Cypress (TC-XXX_resultado.json, con lista "checkpoints")
- mochawesome, en JSON o en HTML (JSON embebido en el atributo data-raw)
- resumen k6 de las pruebas de rendimiento del Modulo 9 (resultado_tc_m09_g*.json:
  campo "resultados.veredicto", o "metrics.checks" + umbrales de --summary-export)
- log de consola de `cypress run` guardado como .txt (caja "(Results)")
- JUnit XML de `pytest --junitxml` (<testsuite tests=".." failures=".." skipped="..">)

Cuando hay varios reportes en la misma carpeta gana el de la corrida MAS
RECIENTE, sin importar el formato: primero por marca de version en el nombre
(reintento2, v2.0...), luego por la fecha del ultimo commit que toco el archivo
(mtime solo para archivos aun sin commit), y por ultimo por nombre.

Un TC sin carpeta de resultados, o con una carpeta vacia, cuenta como
"Pendiente". Un formato de reporte no reconocido no rompe el escaneo:
se marca "no_reconocido" y sigue con el resto. Para esos casos (y para los
que no dejan ningun archivo) declarados_sin_evidencia.csv permite fijar el
veredicto a mano leyendolo del .md de la corrida.
"""
from __future__ import annotations

import csv
import html
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# Local: los repos viven al lado de qa-dashboard/. En Docker/Dokploy REPOS_DIR
# apunta al volumen persistente donde server.py los clona (rama test).
ROOT = Path(os.environ.get("REPOS_DIR") or Path(__file__).resolve().parent.parent)
BACKEND_TESTS = ROOT / "sgpmp-backend" / "tests" / "Test_Testing"
FRONTEND_TESTS = ROOT / "SGPMP-FRONT-END-PWA" / "testing" / "test_testing"
INCIDENCIAS_CSV = Path(__file__).resolve().parent / "incidencias.csv"
ASIGNACIONES_CSV = Path(__file__).resolve().parent / "asignaciones.csv"
DECLARADOS_CSV = Path(__file__).resolve().parent / "declarados_sin_evidencia.csv"
SIN_ASIGNAR = "Sin asignar"

TC_DIR_RE = re.compile(r"^TC-M0(\d+)-(G?)(\d+)$", re.IGNORECASE)
RF_DIR_RE = re.compile(r"^RF-(\d+)$", re.IGNORECASE)


@dataclass
class ResultadoTC:
    tc_id: str
    rf: str
    modulo: str
    origen: str  # "backend" | "frontend"
    tipo_reporte: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    errores: list = field(default_factory=list)
    carpeta: str = ""
    responsable: str = SIN_ASIGNAR
    estado_declarado: Optional[str] = None
    nota_declarada: str = ""

    @property
    def estado(self) -> str:
        if self.estado_declarado:
            return self.estado_declarado
        if self.tipo_reporte in ("sin_datos", "no_reconocido"):
            return "Pendiente"
        if self.failed > 0:
            return "Rechazado"
        if self.total > 0:
            return "Aprobado"
        return "Pendiente"

    @property
    def ejecutado(self) -> bool:
        # Consistente por construccion con `estado`: un caso nunca puede
        # contar como ejecutado y a la vez caer en "Pendiente" (bug
        # detectado en TC-M01-074 -- reporte cypress-json reconocido pero
        # con checkpoints=[] porque la preparacion/login fallo antes de
        # llegar a ellos; el veredicto propio del reporte ya dice "NO
        # EJECUTADO").
        return self.estado != "Pendiente"


def _find_resultados_dir(tc_dir: Path) -> Optional[Path]:
    for child in tc_dir.iterdir():
        if child.is_dir() and child.name.lower() in ("resultados", "resultado"):
            return child
    return None


def _parse_pytest_html(texto: str) -> Optional[dict]:
    m = re.search(r'data-jsonblob="([^"]*)"', texto)
    if not m:
        return None
    crudo = html.unescape(m.group(1))
    try:
        data = json.loads(crudo)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    tests = data.get("tests")
    if not isinstance(tests, dict):
        return None
    total = passed = failed = 0
    errores = []
    for nombre_test, resultados in tests.items():
        for r in resultados:
            resultado = (r.get("result") or "").lower()
            total += 1
            if resultado == "passed":
                passed += 1
            elif resultado in ("failed", "error"):
                failed += 1
                log = (r.get("log") or "").strip()
                linea = nombre_test.split("::")[-1]
                if log:
                    lineas_e = [l for l in log.splitlines() if l.strip().startswith("E ")]
                    if lineas_e:
                        linea = lineas_e[0].strip()[2:].strip()
                errores.append(linea[:220])
    return {"total": total, "passed": passed, "failed": failed, "errores": errores}


def _parse_newman_htmlextra(texto: str) -> Optional[dict]:
    if "Total Assertions" not in texto:
        return None
    m_assert = re.search(r'Total Assertions</h6>\s*<h1[^>]*>(\d+)</h1>', texto)
    m_failed = re.search(r'Total Failed Tests</h6>\s*<h1[^>]*>(\d+)</h1>', texto)
    if not m_assert:
        return None
    total = int(m_assert.group(1))
    failed = int(m_failed.group(1)) if m_failed else 0
    passed = max(total - failed, 0)
    crudos = re.findall(
        r'Assertion Error Message</h5>\s*<div>\s*<pre><code\s*>(.*?)</code></pre>',
        texto, re.DOTALL,
    )
    errores = [re.sub(r"\s+", " ", html.unescape(e)).strip()[:220] for e in crudos]
    return {"total": total, "passed": passed, "failed": failed, "errores": errores}


def _parse_cypress_json(texto: str) -> Optional[dict]:
    try:
        data = json.loads(texto)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    checkpoints = data.get("checkpoints")
    if checkpoints is None:
        return None
    total = len(checkpoints)
    failed = sum(1 for c in checkpoints if c.get("estado") == "FALLA")
    passed = sum(1 for c in checkpoints if c.get("estado") == "OK")
    errores = [
        f"{c.get('paso', '')}: {c.get('obtenido', '')}"[:220]
        for c in checkpoints if c.get("estado") == "FALLA"
    ]
    return {"total": total, "passed": passed, "failed": failed, "errores": errores}


def _mochawesome_desde_dict(data) -> Optional[dict]:
    if not isinstance(data, dict):
        return None
    stats = data.get("stats")
    # En mochawesome stats.tests es un entero. El reporter propio del plan de
    # Modulo 9 (newman-TC-M09-*.json de TC-M09-G23/G24) tambien trae un bloque
    # "stats" pero con la forma de Newman: stats.tests = {"total": N, ...}. Sin
    # este chequeo, ese dict se colaba como `total` y ResultadoTC.estado
    # reventaba con "'>' not supported between 'dict' and 'int'", tumbando
    # /api/data entero (el panel solo mostraba "No se pudo conectar").
    if not isinstance(stats, dict) or not isinstance(stats.get("tests"), int):
        return None
    total = stats.get("tests", 0)
    passed = stats.get("passes", 0)
    failed = stats.get("failures", 0)
    errores = []
    for suite in data.get("results", []):
        for s in _iter_mocha_suites(suite):
            for t in s.get("tests", []):
                if t.get("fail"):
                    msg = ((t.get("err") or {}).get("message") or t.get("title") or "").strip()
                    errores.append(msg[:220])
    return {"total": total, "passed": passed, "failed": failed, "errores": errores}


def _parse_mochawesome(texto: str) -> Optional[dict]:
    try:
        data = json.loads(texto)
    except json.JSONDecodeError:
        return None
    return _mochawesome_desde_dict(data)


def _parse_mochawesome_html(texto: str) -> Optional[dict]:
    # El reporte HTML de mochawesome incrusta el JSON completo en el atributo
    # data-raw del <div id="report"> (pytest-html usa data-jsonblob; son
    # formatos distintos). Cypress + mochawesome guardan solo este HTML en
    # Resultados/, sin el .json, asi que sin esto el TC quedaba "no_reconocido".
    m = re.search(r'\sdata-raw="([^"]*)"', texto)
    if not m:
        return None
    try:
        data = json.loads(html.unescape(m.group(1)))
    except json.JSONDecodeError:
        return None
    return _mochawesome_desde_dict(data)


def _parse_k6_resumen(texto: str) -> Optional[dict]:
    """Reportes propios de las pruebas de rendimiento k6 del Modulo 9
    (resultado_tc_m09_g*.json). Dos formas:
      A) {"resultados": {"veredicto": "OK"|..., ...}}  -- una sola medicion.
      B) salida de `k6 --summary-export`: {"metrics": {"checks": {"passes": N,
         "fails": M}, "<trend>": {"thresholds": {"p(95)<2000": false}}}}. Un
         umbral incumplido aparece como `true` dentro de metrics.<x>.thresholds.
    """
    try:
        data = json.loads(texto)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    res = data.get("resultados")
    if isinstance(res, dict) and "veredicto" in res:
        ok = str(res.get("veredicto", "")).strip().upper().startswith("OK")
        errores = [] if ok else [f"veredicto k6: {res.get('veredicto')}"[:220]]
        return {"total": 1, "passed": 1 if ok else 0, "failed": 0 if ok else 1, "errores": errores}

    metrics = data.get("metrics")
    if isinstance(metrics, dict) and isinstance(metrics.get("checks"), dict):
        passes = int(metrics["checks"].get("passes", 0) or 0)
        fails = int(metrics["checks"].get("fails", 0) or 0)
        errores = []
        umbrales_incumplidos = 0
        for nombre, val in metrics.items():
            thr = val.get("thresholds") if isinstance(val, dict) else None
            if not isinstance(thr, dict):
                continue
            for expr, incumplido in thr.items():
                if incumplido:
                    umbrales_incumplidos += 1
                    errores.append(f"{nombre}: umbral k6 incumplido ({expr})"[:220])
        total = passes + fails + umbrales_incumplidos
        if total == 0:
            return None
        return {
            "total": total,
            "passed": passes,
            "failed": fails + umbrales_incumplidos,
            "errores": errores,
        }

    return None


def _parse_cypress_consola(texto: str) -> Optional[dict]:
    """Log de consola de `cypress run` guardado como .txt (lo que hace el
    equipo de frontend/Laura en vez de copiar el mochawesome.html). Se busca
    la caja de resumen "(Results)": Tests / Passing / Failing / Pending /
    Skipped. Un .txt cualquiera de evidencia no trae ese bloque y se ignora."""
    if "Passing:" not in texto or "Failing:" not in texto:
        return None
    if "Run Finished" not in texto and "Run Starting" not in texto:
        return None
    m = re.search(
        r"Tests:\s*(\d+).*?Passing:\s*(\d+).*?Failing:\s*(\d+).*?Pending:\s*(\d+)"
        r"(?:.*?Skipped:\s*(\d+))?",
        texto, re.DOTALL,
    )
    if not m:
        return None
    total, passed, failed, pending = (int(m.group(i)) for i in range(1, 5))
    skipped = int(m.group(5)) if m.group(5) else 0
    total = max(total - pending - skipped, 0)
    errores = [
        _reparar_mojibake(re.sub(r"\s+", " ", e).strip())[:220]
        for e in re.findall(r"^\s*(?:\d+\)\s*)?(Error:.*?)$", texto, re.MULTILINE)
    ][:5]
    return {"total": total, "passed": passed, "failed": failed, "errores": errores}


def _parse_junit_xml(texto: str) -> Optional[dict]:
    """Reporte JUnit XML de pytest (--junitxml), formato <testsuites><testsuite
    tests=".." failures=".." errors=".." skipped="..">. Un test 'skipped' (ej.
    bloqueado por falta de credencial) no cuenta ni como aprobado ni como
    fallido -- se descuenta del total, igual que pending/skipped en el log de
    consola de Cypress (_parse_cypress_consola)."""
    try:
        root = ET.fromstring(texto)
    except ET.ParseError:
        return None
    suites = root.findall(".//testsuite") if root.tag != "testsuite" else [root]
    if not suites:
        return None
    tests = failures = errors = skipped = 0
    errores = []
    for ts in suites:
        try:
            tests += int(ts.get("tests", 0))
            failures += int(ts.get("failures", 0))
            errors += int(ts.get("errors", 0))
            skipped += int(ts.get("skipped", 0))
        except (TypeError, ValueError):
            return None
        for tc in ts.findall("testcase"):
            nodo = tc.find("failure")
            if nodo is None:
                nodo = tc.find("error")
            if nodo is not None:
                msg = (nodo.get("message") or (nodo.text or "")).strip()
                nombre = tc.get("name", "")
                errores.append(f"{nombre}: {msg}"[:220] if msg else nombre[:220])
    if tests == 0:
        return None
    total = max(tests - skipped, 0)
    failed = failures + errors
    passed = max(total - failed, 0)
    return {"total": total, "passed": passed, "failed": failed, "errores": errores[:5]}


def _reparar_mojibake(s: str) -> str:
    """Los logs de Cypress en Windows llegan doble-codificados (UTF-8 leido
    como CP850 y luego guardado en UTF-16): 'opcion' aparece como 'opci##n'.
    Si se ven caracteres de dibujo de caja en medio del texto, se revierte."""
    if not any(c in s for c in "├│┤┐└┴┬┼╬╣║"):
        return s
    try:
        return s.encode("cp850", "ignore").decode("utf-8", "ignore") or s
    except (UnicodeError, LookupError):
        return s


def _iter_mocha_suites(suite: dict):
    yield suite
    for hijo in suite.get("suites", []) or []:
        yield from _iter_mocha_suites(hijo)


def _leer_texto(p: Path) -> Optional[str]:
    """Lee un reporte como texto. Los logs de consola de Cypress en Windows
    se guardan en UTF-16; el resto suele ser UTF-8."""
    try:
        crudo = p.read_bytes()
    except OSError:
        return None
    if crudo[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return crudo.decode("utf-16")
        except UnicodeDecodeError:
            return None
    return crudo.decode("utf-8", errors="ignore")


# Archivos dentro de la carpeta del TC que son INSUMO de la prueba (colecciones
# Postman, config de Cypress, specs), no un reporte de ejecucion -- se excluyen
# al buscar candidatos directamente en la raiz de la carpeta del TC.
_ARCHIVOS_NO_REPORTE = {
    "package.json", "package-lock.json", "tsconfig.json", "commands.ts",
    "cypress.config.js", "cypress.config.ts",
}


def _es_candidato_valido(p: Path, es_raiz_tc: bool) -> bool:
    if p.suffix.lower() not in (".json", ".html", ".txt", ".xml"):
        return False
    if not es_raiz_tc:
        return "node_modules" not in p.parts
    if p.name in _ARCHIVOS_NO_REPORTE:
        return False
    if p.name.endswith((".postman_collection.json", ".cy.ts", ".cy.js")):
        return False
    return "node_modules" not in p.parts


_PATRONES_RANGO_REINTENTO = [
    re.compile(r"definitivo", re.IGNORECASE),
    re.compile(r"reintento\s*(\d*)", re.IGNORECASE),
    re.compile(r"retest\s*(\d*)", re.IGNORECASE),
    re.compile(r"retry\s*(\d*)", re.IGNORECASE),
    re.compile(r"\bv(\d+)\.(\d+)\b", re.IGNORECASE),
    re.compile(r"fase\s*(\d+)", re.IGNORECASE),
    re.compile(r"parte\s*(\d+)", re.IGNORECASE),
]


def _rango_version(p: Path) -> int:
    """Estima que tan 'final' es un nombre de archivo entre varios reportes
    del mismo TC (reintento2 > reintento, v2.0 > v1, DEFINITIVO > parte2...).

    Existe porque, tras un `git clone`/checkout en bloque, los archivos de
    reintentos sucesivos quedan con mtimes separados por microsegundos que
    reflejan el orden de escritura en disco durante el checkout, NO el orden
    real en que se ejecutaron las pruebas -- así que mtime solo no basta para
    elegir el intento correcto (ver TC-M01-02: mtime elegia el reintento 1,
    que tambien fallo, en vez de reintento2, el que de verdad paso)."""
    nombre = p.name
    rango = 0
    for patron in _PATRONES_RANGO_REINTENTO:
        m = patron.search(nombre)
        if not m:
            continue
        if patron is _PATRONES_RANGO_REINTENTO[0]:
            rango = max(rango, 1000)
        elif patron is _PATRONES_RANGO_REINTENTO[4]:
            major, minor = m.groups()
            rango = max(rango, int(major) * 100 + int(minor))
        else:
            n = m.group(1)
            rango = max(rango, int(n) if n else 1)
    return rango


def _fechas_git(raiz: Path) -> dict:
    """Fecha (epoch) del ultimo commit que toco cada archivo bajo `raiz`.

    Tras un `git clone` todos los archivos quedan con el mismo mtime, asi que
    desempatar por mtime elegia al azar entre dos reportes del mismo TC: en dos
    clones del mismo commit TC-M02-G032, TC-M02-G004 y TC-M09-G004 salian
    Aprobado en uno y Rechazado en el otro. La fecha de commit es la misma en
    cualquier clon. --no-renames evita que git pida blobs en un clone parcial.
    Si `raiz` no es un repo git devuelve {} y todo cae a mtime."""
    try:
        r = subprocess.run(
            ["git", "-c", "core.quotepath=off", "log", "--relative", "--no-renames",
             "--name-only", "--format=%x00%ct", "--", "."],
            cwd=raiz, capture_output=True, text=True, timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    fechas: dict = {}
    t = 0
    for linea in r.stdout.splitlines() if r.returncode == 0 else []:
        if linea.startswith("\0"):
            t = int(linea[1:])
        elif linea:
            ruta = raiz / linea
            fechas[ruta] = max(fechas.get(ruta, 0), t)
    return fechas


def _recolectar_candidatos(tc_dir: Path, resultados_dir: Optional[Path], fechas_git: dict) -> list:
    candidatos = []
    if resultados_dir is not None:
        candidatos += [p for p in resultados_dir.rglob("*") if p.is_file() and _es_candidato_valido(p, False)]
    # Algunos TC guardan el reporte directo en la raiz de su carpeta, sin
    # subcarpeta Resultados/RESULTADOS (ej. TC-M01-31).
    candidatos += [p for p in tc_dir.glob("*") if p.is_file() and _es_candidato_valido(p, True)]
    # Gana primero el nombre de archivo que se declara mas "final"
    # (reintento2, v2.0, DEFINITIVO...); entre archivos con el mismo rango
    # desempata la fecha de commit (mtime si aun no tiene commit) y al final el
    # nombre, para que el resultado sea el mismo en cualquier clon.
    candidatos = sorted(
        (p for p in set(candidatos) if not _es_coleccion_postman(p)),
        key=lambda p: (_rango_version(p), fechas_git.get(p) or p.stat().st_mtime, p.name),
        reverse=True,
    )
    return candidatos


def _es_coleccion_postman(p: Path) -> bool:
    """Detecta colecciones Postman con nombre libre (no siempre terminan en
    .postman_collection.json, ej. tc-m01-112.json) para no contarlas como
    reporte sin reconocer -- son el insumo de la prueba, no su resultado."""
    if p.suffix.lower() != ".json":
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and "item" in data and isinstance(data.get("info"), dict) and "schema" in data.get("info", {})


_PARSERS_POR_EXT = {
    ".json": [
        ("cypress-json", _parse_cypress_json),
        ("mochawesome", _parse_mochawesome),
        ("k6-resumen", _parse_k6_resumen),
    ],
    ".html": [
        ("pytest-html", _parse_pytest_html),
        ("newman-htmlextra", _parse_newman_htmlextra),
        ("mochawesome-html", _parse_mochawesome_html),
    ],
    ".txt": [
        ("cypress-consola", _parse_cypress_consola),
    ],
    ".xml": [
        ("junit-xml", _parse_junit_xml),
    ],
}


def _parsear_candidatos(candidatos: list) -> dict:
    # `candidatos` viene ordenado del mas reciente al mas viejo: se recorre asi, sin importar
    # la extension, para que gane el reporte de la corrida MAS RECIENTE aunque
    # una corrida vieja haya dejado un formato "mas confiable" (ej. TC-M09-G90:
    # mochawesome.html que paso, y un .txt posterior de Cypress que fallo).
    mejor = None
    for p in candidatos:
        parsers = _PARSERS_POR_EXT.get(p.suffix.lower())
        if not parsers:
            continue
        texto = _leer_texto(p)
        if texto is None:
            continue
        for tipo, fn in parsers:
            parsed = fn(texto)
            if not parsed:
                continue
            if mejor is None:
                mejor = {**parsed, "tipo_reporte": tipo}
                if tipo != "junit-xml":
                    return mejor
                break
            # El XML de JUnit describe UN sub-caso de prueba, no todo el grupo
            # (ej. TC-M02-G97: el pytest de TC-M02-314 pasa, pero el reporte
            # Newman del mismo grupo trae un TC-313 rechazado). Si el reporte
            # que ya se acepto es un XML y aparece OTRO formato en la misma
            # carpeta que indique un resultado peor, ese otro gana -- igual
            # que la regla de "el peor veredicto gana" al fusionar backend y
            # frontend (_deduplicar_por_tc).
            if mejor["tipo_reporte"] == "junit-xml" and parsed["failed"] > 0 and mejor["failed"] == 0:
                mejor = {**parsed, "tipo_reporte": tipo}
            return mejor

    if mejor:
        return mejor
    if candidatos:
        return {"total": 0, "passed": 0, "failed": 0, "errores": [], "tipo_reporte": "no_reconocido"}
    return {"total": 0, "passed": 0, "failed": 0, "errores": [], "tipo_reporte": "sin_datos"}


def _normalizar_tc_id(nombre: str) -> str:
    m = TC_DIR_RE.match(nombre)
    if not m:
        return nombre.upper()
    # El grupo "G" (ej. TC-M09-G02) identifica un caso agrupado del plan de
    # Modulo 9 consolidado -- se conserva el prefijo, solo se normaliza el
    # padding para que ordene igual sin importar cuantos digitos traiga la
    # carpeta original (G2, G02, G002 -> G002).
    modulo, prefijo_g, numero = m.group(1), m.group(2).upper(), m.group(3)
    return f"TC-M0{modulo}-{prefijo_g}{int(numero):03d}"


def _escanear_raiz(raiz: Path, origen: str, asignaciones: dict, declarados: dict) -> list:
    resultados = []
    if not raiz.exists():
        return resultados
    fechas_git = _fechas_git(raiz)
    for modulo_dir in sorted(p for p in raiz.iterdir() if p.is_dir()):
        for rf_dir in sorted(p for p in modulo_dir.iterdir() if p.is_dir()):
            if not RF_DIR_RE.match(rf_dir.name):
                continue
            for tc_dir in sorted(p for p in rf_dir.iterdir() if p.is_dir()):
                if not TC_DIR_RE.match(tc_dir.name):
                    continue
                resultados_dir = _find_resultados_dir(tc_dir)
                candidatos = _recolectar_candidatos(tc_dir, resultados_dir, fechas_git)
                parsed = _parsear_candidatos(candidatos)
                tc_id = _normalizar_tc_id(tc_dir.name)

                estado_declarado = None
                nota_declarada = ""
                tipo_reporte = parsed["tipo_reporte"]
                # Respaldo manual: un TC declarado a mano SIEMPRE gana sobre lo
                # que detecte el escaneo, incondicionalmente -- no solo cuando
                # el escaneo no encuentra nada ("sin_datos"/"no_reconocido").
                # Un TC en declarados_sin_evidencia.csv suele ser un caso
                # compuesto (varias corridas/archivos con veredictos parciales,
                # ej. TC-M09-G28: 3 XML de intentos distintos con el mismo
                # mtime) donde QA ya revisó todo a mano y fijó el veredicto
                # real en un .md -- un parser nuevo que de pronto reconoce UNO
                # de esos archivos sueltos no debe pisar esa decisión.
                if tc_id in declarados:
                    tipo_reporte = "declarado"
                    estado_declarado = declarados[tc_id]["estado"]
                    nota_declarada = declarados[tc_id]["nota"]

                resultados.append(ResultadoTC(
                    tc_id=tc_id,
                    rf=rf_dir.name.upper(),
                    modulo=modulo_dir.name,
                    origen=origen,
                    tipo_reporte=tipo_reporte,
                    total=parsed["total"],
                    passed=parsed["passed"],
                    failed=parsed["failed"],
                    # Si el TC esta declarado, la lista de errores tambien debe
                    # venir del veredicto manual (nota_declarada), no del
                    # escaneo: si no, un TC declarado podia mostrar en la UI
                    # el mensaje de error crudo de la corrida que la propia
                    # declaracion esta pisando (ej. TC-M09-G85/G88:
                    # Estado=Pendiente por decision de QA, pero Errores
                    # mostraba el fallo real de Cypress -- contradictorio).
                    errores=([nota_declarada] if nota_declarada else []) if estado_declarado
                        else parsed["errores"],
                    carpeta=str(tc_dir.relative_to(ROOT)),
                    responsable=asignaciones.get(tc_id, SIN_ASIGNAR),
                    estado_declarado=estado_declarado,
                    nota_declarada=nota_declarada,
                ))
    return resultados


_STOP_TOOLS_VERSION = {
    "newman", "cypress", "postman", "pytest", "python", "node",
    "npm", "chrome", "junit", "k6", "react", "openapi", "swagger",
}
_RONDA_VERSION_PUNTO = re.compile(r"(\S+)\s+v\s*(\d+)[.,](\d+)\b", re.IGNORECASE)
_RONDA_VERSION_SUELTA = re.compile(
    r"reeval\w*[-_]?v(\d+)\b"
    r"|\bv(\d+)\s+ejecutad"
    r"|reevaluaci[oó]n\s*v?(\d+)\b"
    r"|revisi[oó]n\s*(\d+)\b"
    r"|-\s*v(\d+)\b",
    re.IGNORECASE,
)


def _ronda_incidencia(id_incidencia: str, notas: str) -> int:
    """Estima en que ronda de evaluacion se detecto/confirmo una incidencia
    (1a, 2a, 3a...), a partir de las mismas marcas de version que
    _rango_version usa para elegir el reporte mas reciente de un TC
    (v2.0, V3, reintento2, revision 3, REEVAL-V3...), buscadas en el ID de
    la incidencia y en el texto libre de 'Evidencia / Notas'.

    Existe porque incidencias.csv no tiene una columna de ronda explicita:
    QA reevalua defectos y a veces cambia el ID (sufijo 'v2.0'/'- V3') y a
    veces solo lo menciona en las notas ('Reevaluacion V2 ...',
    'revision 3 ejecutada...', archivo '..._reevaluacion_V2.md'). Sin esto,
    el dashboard acumula todo en una sola tabla y no se ve la mejora entre
    rondas -- se ve como si los defectos nunca bajaran.

    Filtra menciones de version de herramientas ("Newman v6.2.2",
    "Cypress v14.5.4") para que no se confundan con una ronda de QA."""
    texto = f"{id_incidencia} {notas}"
    ronda = 1
    for anterior, mayor, _menor in _RONDA_VERSION_PUNTO.findall(texto):
        if anterior.strip(":,()[]").lower() in _STOP_TOOLS_VERSION:
            continue
        ronda = max(ronda, int(mayor))
    for m in _RONDA_VERSION_SUELTA.finditer(texto):
        grupo = next((g for g in m.groups() if g), None)
        if grupo:
            ronda = max(ronda, int(grupo))
    return ronda


def _leer_incidencias() -> list:
    if not INCIDENCIAS_CSV.exists():
        return []
    with INCIDENCIAS_CSV.open(encoding="utf-8-sig", newline="") as f:
        filas = list(csv.DictReader(f))
    for fila in filas:
        fila["ronda_evaluacion"] = _ronda_incidencia(
            fila.get("ID", ""), fila.get("Evidencia / Notas", "")
        )
    return filas


def _leer_asignaciones() -> dict:
    """Lee asignaciones.csv (TC -> Responsable) para poder mostrar cuantos
    casos ha ejecutado cada quien. Si el archivo no existe todavia, todos
    los casos quedan como 'Sin asignar' en vez de romper el escaneo."""
    if not ASIGNACIONES_CSV.exists():
        return {}
    with ASIGNACIONES_CSV.open(encoding="utf-8-sig", newline="") as f:
        filas = list(csv.DictReader(f))
    return {
        _normalizar_tc_id(fila["TC"].strip()): fila["Responsable"].strip()
        for fila in filas
        if fila.get("TC")
    }


def _leer_declarados() -> dict:
    """Lee declarados_sin_evidencia.csv: casos que el Excel maestro marca
    Aprobado/Rechazado pero cuya carpeta no tiene ningun archivo de
    reporte guardado en el repo. Se usan como respaldo SOLO cuando no
    hay evidencia real, y quedan marcados con su propio tipo_reporte
    para no confundirse con un resultado verificado por archivo."""
    if not DECLARADOS_CSV.exists():
        return {}
    with DECLARADOS_CSV.open(encoding="utf-8-sig", newline="") as f:
        filas = list(csv.DictReader(f))
    return {
        _normalizar_tc_id(fila["TC"].strip()): {
            "estado": fila["Estado"].strip(),
            "nota": fila.get("Nota", "").strip(),
        }
        for fila in filas
        if fila.get("TC")
    }


def _normalizar_error(msg: str) -> str:
    m = re.sub(r"\d+", "#", msg)
    m = re.sub(r"\s+", " ", m).strip()
    return m[:180]


_HTTP_NOMBRE = {
    "200": "OK", "201": "creado", "202": "aceptado", "204": "sin contenido",
    "400": "petición inválida", "401": "no autenticado", "403": "sin permiso",
    "404": "no encontrado", "409": "conflicto o duplicado", "412": "condición previa fallida",
    "422": "datos rechazados", "423": "recurso bloqueado", "429": "demasiadas solicitudes",
    "500": "error interno del servidor", "503": "servicio no disponible",
}


def _explicar_error(msg: str) -> str:
    """Traduce el mensaje crudo de Newman/Cypress/Pytest a una frase en lenguaje
    simple para poder exponer los resultados sin tecnicismos. Espejo de
    explicarError() en dashboard.html. Si no reconoce el patron, limpia prefijos."""
    raw = re.sub(r"\s+", " ", msg or "").strip()
    nom = lambda c: _HTTP_NOMBRE.get(c, f"código {c}")

    m = re.search(r"status code (\d{3}).*?but got (\d{3})", raw, re.I)
    if m:
        esp, got = m.group(1), m.group(2)
        if got == "500":
            return f'El servidor falló con un error interno (500) cuando debía responder "{nom(esp)}" ({esp}).'
        if got == "503":
            return f'El servidor no estaba disponible (503); se esperaba "{nom(esp)}".'
        if esp in ("200", "201", "202", "204") and got in ("400", "401", "403", "404", "409", "422"):
            return f'El servidor rechazó la operación con "{nom(got)}" ({got}) cuando debía completarla.'
        if esp in ("401", "403", "404", "409", "412", "422", "423", "429") and got in ("200", "201", "204"):
            return f'El servidor aceptó ({got}) una operación que debía rechazar con "{nom(esp)}" ({esp}).'
        return f'El servidor respondió {got} ("{nom(got)}") en vez del {esp} ("{nom(esp)}") esperado.'

    m = re.search(r"expected (\d{3}) to be within 2\d\d\.\.2\d\d", raw, re.I)
    if m:
        return (f"El servidor falló con un error ({m.group(1)}) cuando debía responder correctamente (2xx)."
                if m.group(1)[0] == "5"
                else f"El servidor respondió {m.group(1)} cuando debía responder con un código exitoso (2xx).")

    reglas = [
        (r"injectaxe", "Falta un complemento en la herramienta de pruebas de accesibilidad; hay que ajustar el proyecto de automatización (no es un fallo del producto)."),
        (r"is not a function|is not iterable|cannot read propert|target cannot be null|undefined \(reading",
         "La prueba automatizada se cortó por un dato faltante (normalmente porque un paso anterior no devolvió lo esperado); revisar el script antes de darlo por defecto del producto."),
        (r"teardown \d* ?omitido|no se obtuvo id_", "No se pudieron limpiar los datos de prueba porque el registro previo falló."),
        (r"bloqueado|no est[aá] disponible en (la interfaz|configuraci)", "La funcionalidad todavía no existe en la pantalla, así que la prueba no se pudo ejecutar."),
        (r"expected nan|\bnan\b", "Un cálculo del servidor devolvió un valor no numérico (NaN) donde debía haber un número."),
        (r"to be above \+?0|expected \+?0 to be above|al menos 1 (coincidencia|entrada|resultado)",
         "Se esperaba encontrar al menos un registro y no se encontró ninguno."),
        (r"expected undefined .*(to be a number|number)", "Un valor numérico que la prueba esperaba llegó vacío en la respuesta."),
        (r"expected undefined (to|not)|to (not )?have property", "La respuesta del servidor no incluye un dato que la prueba esperaba (llegó vacío o con otra estructura)."),
        (r"expected false to be true", "Una verificación del sistema no se cumplió (dio falso donde se esperaba verdadero)."),
        (r"not to (deeply )?equal|to (deeply )?equal", "Un valor de la respuesta no coincide con el valor esperado."),
    ]
    for patron, texto in reglas:
        if re.search(patron, raw, re.I):
            return texto

    m = re.search(r"expected (\d+) to be at most (\d+)", raw, re.I)
    if m:
        return f"Un valor superó el límite permitido: fue {m.group(1)}, el máximo aceptable es {m.group(2)} (posible problema de rendimiento)."

    limpio = re.sub(r"^(AssertionError|TypeError|Error|CypressError|RuntimeError)[:\s-]*", "", raw, flags=re.I)
    limpio = re.sub(r"^(CP-?\d+|Checkpoint \d+|Sub-?caso \d+)[:\s.-]*", "", limpio, flags=re.I).strip()
    return (limpio[:237] + "…") if len(limpio) > 240 else (limpio or raw)


def _agrupar_por_rf(resultados: list[ResultadoTC]) -> list:
    agrupado: dict[str, dict] = {}
    for r in resultados:
        g = agrupado.setdefault(r.rf, {"rf": r.rf, "total": 0, "aprobados": 0, "rechazados": 0, "pendientes": 0})
        g["total"] += 1
        if r.estado == "Aprobado":
            g["aprobados"] += 1
        elif r.estado == "Rechazado":
            g["rechazados"] += 1
        else:
            g["pendientes"] += 1
    return sorted(agrupado.values(), key=lambda g: (len(g["rf"]), g["rf"]))


def _agrupar_por_responsable(resultados: list[ResultadoTC]) -> list:
    agrupado: dict[str, dict] = {}
    for r in resultados:
        g = agrupado.setdefault(r.responsable, {
            "responsable": r.responsable, "asignados": 0, "ejecutados": 0,
            "aprobados": 0, "rechazados": 0, "pendientes": 0,
        })
        g["asignados"] += 1
        if r.ejecutado:
            g["ejecutados"] += 1
        if r.estado == "Aprobado":
            g["aprobados"] += 1
        elif r.estado == "Rechazado":
            g["rechazados"] += 1
        else:
            g["pendientes"] += 1
    for g in agrupado.values():
        g["pct_ejecutado"] = round(100 * g["ejecutados"] / g["asignados"], 1) if g["asignados"] else 0.0
    # Sin asignar al final; el resto ordenado por mas ejecutados primero.
    return sorted(
        agrupado.values(),
        key=lambda g: (g["responsable"] == SIN_ASIGNAR, -g["ejecutados"], g["responsable"]),
    )


def _deduplicar_por_tc(resultados: list[ResultadoTC]) -> list[ResultadoTC]:
    """Un mismo TC puede aparecer dos veces: por carpetas duplicadas bajo dos
    RF distintos (una con evidencia, otra huerfana y vacia), o porque el caso
    se probo tanto en backend como en frontend (ej. TC-M09-G110). Se deja una
    sola entrada por TC, con esta prioridad: ejecutado antes que Pendiente y,
    entre ejecutados, Rechazado antes que Aprobado -- si cualquiera de las
    corridas fallo, el caso cuenta como Rechazado, no se esconde tras la que
    paso."""
    orden = {"Rechazado": 3, "Aprobado": 2, "Pendiente": 1}

    def prioridad(r: ResultadoTC) -> tuple:
        return (orden.get(r.estado, 0), r.total)

    mejores: dict[str, ResultadoTC] = {}
    for r in resultados:
        actual = mejores.get(r.tc_id)
        if actual is None or prioridad(r) > prioridad(actual):
            mejores[r.tc_id] = r
    return list(mejores.values())


def scan() -> dict:
    asignaciones = _leer_asignaciones()
    declarados = _leer_declarados()
    resultados = (
        _escanear_raiz(BACKEND_TESTS, "backend", asignaciones, declarados)
        + _escanear_raiz(FRONTEND_TESTS, "frontend", asignaciones, declarados)
    )
    resultados = _deduplicar_por_tc(resultados)

    total_tcs = len(resultados)
    aprobados = sum(1 for r in resultados if r.estado == "Aprobado")
    rechazados = sum(1 for r in resultados if r.estado == "Rechazado")
    pendientes = sum(1 for r in resultados if r.estado == "Pendiente")

    contador_errores: Counter = Counter()
    ejemplo_por_error: dict[str, dict] = {}
    for r in resultados:
        for e in r.errores:
            clave = _normalizar_error(e)
            if not clave:
                continue
            contador_errores[clave] += 1
            ejemplo_por_error.setdefault(clave, {"mensaje_original": e, "tc": r.tc_id})

    errores_top = [
        {
            "patron": clave,
            "veces": veces,
            "ejemplo": ejemplo_por_error[clave]["mensaje_original"],
            "explicacion": _explicar_error(ejemplo_por_error[clave]["mensaje_original"]),
            "tc_ejemplo": ejemplo_por_error[clave]["tc"],
        }
        for clave, veces in contador_errores.most_common(12)
    ]

    def pct(n: int) -> float:
        return round(100 * n / total_tcs, 1) if total_tcs else 0.0

    return {
        "generado_en": datetime.now().isoformat(timespec="seconds"),
        "resumen": {
            "total_tcs": total_tcs,
            "aprobados": aprobados,
            "rechazados": rechazados,
            "pendientes": pendientes,
            "pct_aprobados": pct(aprobados),
            "pct_rechazados": pct(rechazados),
            "pct_pendientes": pct(pendientes),
        },
        "por_rf": _agrupar_por_rf(resultados),
        "por_responsable": _agrupar_por_responsable(resultados),
        "casos": [
            {
                "tc_id": r.tc_id, "rf": r.rf, "modulo": r.modulo, "origen": r.origen,
                "tipo_reporte": r.tipo_reporte, "total": r.total, "passed": r.passed,
                "failed": r.failed, "estado": r.estado, "carpeta": r.carpeta,
                "responsable": r.responsable,
                "errores": r.errores[:5],
            }
            for r in sorted(resultados, key=lambda r: (r.estado != "Rechazado", r.rf, r.tc_id))
        ],
        "errores_frecuentes": errores_top,
        "incidencias": _leer_incidencias(),
    }


if __name__ == "__main__":
    print(json.dumps(scan(), indent=2, ensure_ascii=False))
