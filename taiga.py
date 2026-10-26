"""
Incidencias de los modulos 3 y 4 leidas en vivo desde Taiga (Issues).

El resto de modulos (1, 2, 9) siguen saliendo de incidencias.csv. Aqui cada
issue de Taiga se convierte a las mismas columnas del CSV para que el
dashboard lo trate igual que cualquier otra incidencia.

Solo usa la libreria estandar. El proyecto es publico: los GET no piden
autenticacion. Si se define TAIGA_TOKEN (token de aplicacion/personal, nunca
en el codigo) se envia como Bearer.

Variables de entorno (todas opcionales):
    TAIGA_ENABLED      "0" apaga la integracion (modulos 3/4 vuelven al CSV)
    TAIGA_API_URL      https://api.taiga.io/api/v1
    TAIGA_PROJECT_ID   1771570
    TAIGA_MODULOS      "3,4"
    TAIGA_TOKEN        token Bearer
    TAIGA_EXCLUIR      reportantes cuyas incidencias NO se muestran, separados
                       por ";" (por defecto: Miguel Angel Ortiz Escobar)
    TAIGA_CACHE_FILE   donde guardar descripciones y ultimo resultado bueno

Contrato del titulo (subject): empieza por el ID de la incidencia, p. ej.
`[INC-M03-104-G165][RF-62] Titulo...` o `INC-M04-207-G46 Titulo...`. El modulo
se toma de ese ID. Los issues de esos modulos cuyo titulo no empieza por un ID
reconocible no se pierden en silencio: salen en `omitidas`.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = os.environ.get("TAIGA_API_URL", "https://api.taiga.io/api/v1").rstrip("/")
PROJECT_ID = os.environ.get("TAIGA_PROJECT_ID", "1771570")
PROJECT_SLUG = "danielruiz7514-proyecto-integrador-iii-2026-1"
MODULOS = {int(m) for m in os.environ.get("TAIGA_MODULOS", "3,4").split(",") if m.strip()}
TOKEN = os.environ.get("TAIGA_TOKEN", "")
HABILITADO = os.environ.get("TAIGA_ENABLED", "1") != "0"
CACHE = Path(
    os.environ.get("TAIGA_CACHE_FILE")
    or (Path(os.environ["CACHE_FILE"]).parent if os.environ.get("CACHE_FILE") else Path(__file__).resolve().parent)
    / "taiga_cache.json"
)
EXCLUIR = [e for e in os.environ.get("TAIGA_EXCLUIR", "Miguel Angel Ortiz Escobar").split(";") if e.strip()]
TIMEOUT = 30
BOGOTA = timezone(timedelta(hours=-5))  # Colombia no tiene horario de verano

# "INC-MM03-..." (typo) tambien cuenta; el dashboard acepta INC/TC/NC como prefijo.
_ID_RE = re.compile(r"^\W*((?:INC|TC|NC)-M+0?(\d)-[^\s\[\]]*)", re.IGNORECASE)
_BLOQUE_RE = re.compile(r"\s*\]*\s*(\[[^\[\]]*\])\]?")
_RF_RE = re.compile(r"^\[\s*(?:RF-?\s*)?(\d{1,3})\s*\]$", re.IGNORECASE)
_RF_TITULO_RE = re.compile(r"\bRF-?\s*(\d{1,3})\b", re.IGNORECASE)

# Severidad de Taiga -> escala del CSV (propuesta de MANUAL_SINCRONIZACION.md).
_SEVERIDAD = {"Critical": "Crítico", "Important": "Severo", "Normal": "Medio", "Minor": "Bajo", "Wishlist": "Bajo"}
_ESTADOS_CERRADOS = {"Closed", "Rejected"}


def _get(ruta: str, **params):
    url = f"{API}/{ruta}" + ("?" + "&".join(f"{k}={v}" for k, v in params.items()) if params else "")
    cabeceras = {"Accept": "application/json", "x-disable-pagination": "True"}
    if TOKEN:
        cabeceras["Authorization"] = f"Bearer {TOKEN}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=cabeceras), timeout=TIMEOUT) as r:
        return json.load(r)


def _norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if not unicodedata.combining(c)).strip().lower()


def _excluida(*reportantes: str) -> bool:
    """True si el creador del issue o el Evaluador es alguien de EXCLUIR."""
    excluidos = {_norm(e) for e in EXCLUIR}
    return any(_norm(r) in excluidos for r in reportantes if r)


def _severidad_attr(v: str) -> str:
    n = _norm(v)
    for prefijo, nombre in (("crit", "Crítico"), ("sever", "Severo"), ("med", "Medio"), ("baj", "Bajo")):
        if n.startswith(prefijo):
            return nombre
    return v.strip()


def _rf_attr(v: str) -> str:
    v = v.strip()
    return f"RF-{v}" if re.fullmatch(r"\d+(\.\d+)*", v) else v


def parsear_titulo(subject: str) -> dict | None:
    """Separa `[ID][RF] titulo` en id/modulo/rf/titulo. None si no empieza
    por un ID de incidencia reconocible."""
    m = _ID_RE.match(subject or "")
    if not m:
        return None
    id_raw = m.group(1).rstrip(".,:;-")
    # "INC-M03-131-G23El buffer..." (sin espacio tras el ID): se corta en la minuscula.
    id_raw = re.sub(r"(-G\d+)[A-Z][a-z].*$", r"\1", id_raw)
    id_norm = re.sub(r"M+0?(\d)-", r"M0\1-", id_raw.upper(), count=1)
    resto = subject[m.start(1) + len(id_raw):]
    rf = ""
    while True:
        b = _BLOQUE_RE.match(resto)
        if not b:
            break
        rf_m = _RF_RE.match(b.group(1))
        if rf_m and not rf:
            rf = f"RF-{rf_m.group(1)}"
        resto = resto[b.end():]
    titulo = resto.lstrip(" ]:.-\t").strip()
    if not rf:
        t = _RF_TITULO_RE.search(titulo)
        rf = f"RF-{t.group(1)}" if t else ""
    return {"id": id_norm, "modulo": int(m.group(2)), "rf": rf, "titulo": titulo}


def _fecha(iso: str | None) -> str:
    """Fecha AAAA-MM-DD en hora de Colombia (ordena bien como texto)."""
    if not iso:
        return ""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(BOGOTA).strftime("%Y-%m-%d")


def _a_fila(issue: dict, p: dict, descripcion: str, nombres: dict, attrs: dict) -> dict:
    estado = nombres["estados"].get(issue["status"], "")
    detalle = (descripcion or "").replace("\\_", "_").replace("\\*", "*").strip()
    descr = p["titulo"] + (f"\n\n{detalle}" if detalle else "")
    persona = lambda info: (info or {}).get("full_name_display", "")
    # Custom attributes de Taiga (los llena QA); si estan vacios se usa lo
    # derivado del issue. El modulo siempre sale del ID, no del atributo.
    a = lambda nombre: (attrs.get(nombre) or "").strip()
    evaluador = a("evaluador").title() or persona(issue.get("owner_extra_info"))
    equipo = a("equipo responsable")
    return {
        "Modulo": str(p["modulo"]),
        "ID": p["id"],
        "Descripcion del error": descr,
        "RF relacionado": _rf_attr(a("rf")) or p["rf"],
        # Si "Código del error" esta vacio, el dashboard estima desde texto_clasificacion.
        "Categoria del error": a("codigo del error"),
        "Equipo responsable": equipo[:1].upper() + equipo[1:],
        "Severidad": _severidad_attr(a("severidad"))
        or _SEVERIDAD.get(nombres["severidades"].get(issue["severity"], ""), ""),
        "Tiempo maximo de solucion": a("tiempo maximo de solucion"),
        "Fecha deteccion": a("fecha de deteccion") or _fecha(issue.get("created_date")),
        "Fecha limite": a("fecha limite") or issue.get("due_date") or "",
        "Estado": "Cerrado" if estado in _ESTADOS_CERRADOS else "Abierto",
        "Fecha correccion real": a("fecha de correccion real") or _fecha(issue.get("finished_date")),
        "Evidencia / Notas": f"Taiga #{issue['ref']} · estado Taiga: {estado}",
        "Evaluador": evaluador,
        # Extras que el dashboard usa si existen:
        "Asignado a": persona(issue.get("assigned_to_extra_info")),
        "fuente": "Taiga",
        "taiga_ref": issue["ref"],
        "taiga_url": f"https://tree.taiga.io/project/{PROJECT_SLUG}/issue/{issue['ref']}",
        "texto_clasificacion": p["titulo"],
    }


def _leer_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _guardar_cache(cache: dict) -> None:
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CACHE)
    except OSError:
        pass  # sin cache solo se pierde velocidad, no datos


def traer_incidencias() -> tuple[list[dict], dict]:
    """Devuelve (filas, estado). `estado` alimenta la linea de fuentes del
    dashboard: {repo, ok, error?, total, omitidas}. Si Taiga no responde se
    devuelven las filas del ultimo intento bueno para no vaciar M03/M04."""
    if not HABILITADO:
        return [], {"repo": "Taiga (desactivado)", "ok": True, "total": 0, "omitidas": [], "desactivado": True}
    cache = _leer_cache()
    try:
        proyecto = _get(f"projects/{PROJECT_ID}")
        nombres = {
            "estados": {x["id"]: x["name"] for x in proyecto["issue_statuses"]},
            "severidades": {x["id"]: x["name"] for x in proyecto["severities"]},
        }
        issues = _get("issues", project=PROJECT_ID)
        atributos = {x["id"]: _norm(x["name"]) for x in _get("issue-custom-attributes", project=PROJECT_ID)}

        elegidos, omitidas = [], []
        for i in issues:
            p = parsear_titulo(i["subject"])
            if p and p["modulo"] in MODULOS:
                elegidos.append((i, p))
            elif not p and any(re.search(rf"\bM0?{m}\b", i["subject"], re.IGNORECASE) for m in MODULOS):
                omitidas.append({"ref": i["ref"], "subject": i["subject"]})

        # La lista no trae la descripcion: se pide el detalle solo de los issues
        # nuevos o modificados desde la ultima vez.
        descripciones = cache.get("descripciones", {})
        pendientes = [i for i, _ in elegidos
                      if descripciones.get(str(i["ref"]), {}).get("modified_date") != i["modified_date"]]
        if pendientes:
            with ThreadPoolExecutor(max_workers=8) as pool:
                detalles = list(pool.map(
                    lambda i: _get("issues/by_ref", ref=i["ref"], project=PROJECT_ID), pendientes))
            for i, d in zip(pendientes, detalles):
                descripciones[str(i["ref"])] = {"modified_date": i["modified_date"], "description": d.get("description") or ""}

        # Editar un custom attribute no siempre cambia modified_date del issue,
        # asi que estos valores se piden en cada lectura (no se cachean).
        with ThreadPoolExecutor(max_workers=8) as pool:
            valores = list(pool.map(
                lambda ip: _get(f"issues/custom-attributes-values/{ip[0]['id']}")["attributes_values"], elegidos))
        filas, excluidas = [], 0
        for (i, p), vals in zip(elegidos, valores):
            attrs = {atributos.get(int(k), k): v for k, v in vals.items()}
            if _excluida((i.get("owner_extra_info") or {}).get("full_name_display"), attrs.get("evaluador")):
                excluidas += 1
                continue
            filas.append(_a_fila(i, p, descripciones.get(str(i["ref"]), {}).get("description", ""), nombres, attrs))
        _guardar_cache({"descripciones": {str(i["ref"]): descripciones[str(i["ref"])] for i, _ in elegidos},
                        "ultimo_ok": filas, "ultimo_ok_en": datetime.now().isoformat(timespec="seconds")})
        return filas, {"repo": "Taiga (M03/M04)", "ok": True, "total": len(filas), "omitidas": omitidas,
                        "excluidas_por_reportante": excluidas}
    except Exception as exc:  # red caida, 5xx, JSON inesperado...
        filas = cache.get("ultimo_ok", [])
        desde = cache.get("ultimo_ok_en", "")
        aviso = f"no se pudo leer Taiga ({exc}); " + (f"se muestran los datos de {desde}" if filas else "sin datos previos")
        return filas, {"repo": "Taiga (M03/M04)", "ok": False, "error": aviso, "total": len(filas), "omitidas": []}


if __name__ == "__main__":
    filas, estado = traer_incidencias()
    print(json.dumps({"estado": estado, "muestra": filas[:2]}, indent=2, ensure_ascii=False))
