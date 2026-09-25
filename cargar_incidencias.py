#!/usr/bin/env python3
"""Carga incidencias pegadas desde Excel (TSV) en incidencias.csv.

Uso:
    python3 cargar_incidencias.py hoja.tsv                 # solo modulo 2 (por defecto)
    python3 cargar_incidencias.py hoja.tsv --modulos 1 2 9
    python3 cargar_incidencias.py hoja.tsv --simular       # muestra el informe sin escribir

El TSV es lo que Excel deja en el portapapeles al copiar el rango: columnas
separadas por tabulador, celdas con saltos de linea entre comillas. Se acepta
que la hoja traiga varias veces la fila de encabezado (versiones pegadas una
tras otra).

Normalizaciones (todas se listan en el informe, ninguna toca el texto de
"Descripcion del error" ni de "Evidencia / Notas"):
  - ID: se quitan espacios y guiones no separables (U+2011), "NC-" pasa a "INC-",
    y un ID sin prefijo INC (p. ej. "TC-M02-G08") pasa a "INC-M02-?-G08", igual
    que la convencion "INC-M02-?-g35" que ya usa el equipo.
  - Modulo: se deriva del ID (el panel agrupa por el patron INC-M0<n>-).
  - Estado: se lleva a la lista del manual (Abierto / Corregido / Descartado).
  - Una incidencia con el mismo ID que otra ya cargada la reemplaza.
"""
import argparse
import csv
import io
import re
import sys
from collections import Counter
from pathlib import Path

AQUI = Path(__file__).resolve().parent
DESTINO = AQUI / "incidencias.csv"
COLUMNAS = ["Modulo", "ID", "Descripcion del error", "RF relacionado", "Categoria del error",
            "Equipo responsable", "Severidad", "Tiempo maximo de solucion", "Fecha deteccion",
            "Fecha limite", "Estado", "Fecha correccion real", "Evidencia / Notas", "Evaluador"]

# Estados de la hoja -> lista cerrada del manual (seccion 6.5).
ESTADOS = {
    "abierto": "Abierto", "abierta": "Abierto", "pendiente": "Abierto",
    "rechazado de nuevo": "Abierto",   # reabierta tras una retroprueba fallida
    "": "Abierto",                     # sin estado = aun sin trabajar
    "corregido": "Corregido", "cerrado": "Corregido", "cerrada": "Corregido",
    "descartado": "Descartado", "descartada": "Descartado",
}


def limpiar_id(bruto: str):
    """Devuelve (id_normalizado, modulo) o (None, None) si no hay ID usable."""
    s = re.sub(r"[‐‑‒–—]", "-", bruto or "")
    s = re.sub(r"\s+", " ", s).strip().strip('"').strip()
    if not s:
        return None, None
    s = re.sub(r"^NC-", "INC-", s, flags=re.I)
    m = re.match(r"^(?:TC|INC)-M0?(\d+)-(.+)$", s, flags=re.I)
    if not m:
        return s, None
    modulo, resto = m.group(1), m.group(2)
    if not s.upper().startswith("INC-"):
        s = f"INC-M0{modulo}-?-{re.sub(r'^\?-', '', resto)}"
    return s, int(modulo)


def leer_hoja(texto: str):
    filas = list(csv.reader(io.StringIO(texto), delimiter="\t", quotechar='"'))
    salida, omitidas = [], []
    for n, f in enumerate(filas, 1):
        f = [c.strip() for c in f] + [""] * (len(COLUMNAS) - len(f))
        if f[0].lower() == "modulo":          # encabezado repetido
            continue
        if not any(f[1:]):                    # fila vacia (p. ej. "2" suelto)
            continue
        salida.append((n, dict(zip(COLUMNAS, f[:len(COLUMNAS)]))))
    return salida, omitidas


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("hoja", help="archivo TSV con las incidencias")
    ap.add_argument("--modulos", type=int, nargs="+", default=[2], help="modulos a cargar (default: 2)")
    ap.add_argument("--simular", action="store_true", help="no escribe incidencias.csv")
    a = ap.parse_args()

    texto = Path(a.hoja).read_text(encoding="utf-8-sig")
    filas, _ = leer_hoja(texto)
    nuevas, cambios_id, cambios_estado, sin_modulo, fuera = {}, [], Counter(), [], Counter()
    for n, r in filas:
        id_norm, modulo = limpiar_id(r["ID"])
        if id_norm is None or modulo is None:
            sin_modulo.append((n, r["ID"][:40]))
            continue
        if modulo not in a.modulos:
            fuera[modulo] += 1
            continue
        if id_norm != r["ID"].strip():
            cambios_id.append((r["ID"].strip()[:38], id_norm))
        est_orig = r["Estado"].strip()
        est = ESTADOS.get(est_orig.lower())
        if est is None:
            sin_modulo.append((n, f"estado desconocido: {est_orig!r}"))
            est = "Abierto"
        if est_orig != est:
            cambios_estado[(est_orig or "(vacio)", est)] += 1
        f_lim = r["Fecha limite"].replace("!", "1")
        r.update({"ID": id_norm, "Modulo": str(modulo), "Estado": est, "Fecha limite": f_lim})
        if id_norm in nuevas:
            print(f"AVISO: ID repetido dentro de la hoja, se conserva el ultimo: {id_norm}")
        nuevas[id_norm] = r

    existentes = []
    if DESTINO.exists():
        with DESTINO.open(encoding="utf-8-sig", newline="") as f:
            existentes = list(csv.DictReader(f))
    ids_previos = {e["ID"] for e in existentes}
    reemplazadas = [i for i in nuevas if i in ids_previos]
    mantenidas = [e for e in existentes if e["ID"] not in nuevas]
    final = mantenidas + list(nuevas.values())

    print(f"Leidas {len(filas)} filas de la hoja; se cargan {len(nuevas)} (modulos {a.modulos}).")
    print(f"  nuevas: {len(nuevas) - len(reemplazadas)} | reemplazan una existente: {len(reemplazadas)} | ya en el archivo y no tocadas: {len(mantenidas)}")
    if fuera:
        print("  omitidas por otro modulo:", dict(sorted(fuera.items())))
    print("  Estado final:", dict(Counter(r["Estado"] for r in nuevas.values())))
    if cambios_estado:
        print("  Estados normalizados:", {f"{k[0]} -> {k[1]}": v for k, v in cambios_estado.items()})
    if cambios_id:
        print(f"  IDs normalizados ({len(cambios_id)}):")
        for o, n in cambios_id:
            print(f"     {o!r} -> {n!r}")
    for n, d in sin_modulo:
        print(f"  Revisar fila {n}: {d}")

    if a.simular:
        print("(simulacion: no se escribio nada)")
        return
    respaldo = DESTINO.with_suffix(".csv.bak")
    if DESTINO.exists():
        respaldo.write_bytes(DESTINO.read_bytes())
    with DESTINO.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS, lineterminator="\r\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(final)
    print(f"Escrito {DESTINO.name} ({len(final)} filas). Respaldo: {respaldo.name}")


if __name__ == "__main__":
    sys.exit(main())
