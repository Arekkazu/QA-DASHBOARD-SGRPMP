"""Parseo de titulos de Taiga con los casos reales de M03/M04.

    python3 test_taiga.py
"""
import taiga

CASOS = [
    ("[INC-M03-104-G165][RF-62]El endpoint POST /iot/calidad/{id_telemetria}/evaluar presenta cuatro defectos",
     "INC-M03-104-G165", 3, "RF-62", "El endpoint POST /iot/calidad/{id_telemetria}/evaluar presenta cuatro defectos"),
    ("[INC-M03-193-G121 [RF-59]] En el historial, la linea", "INC-M03-193-G121", 3, "RF-59", "En el historial, la linea"),
    ("[INC-M03-182-G112 [59]] GET /iot/monitoreo/historial", "INC-M03-182-G112", 3, "RF-59", "GET /iot/monitoreo/historial"),
    ("[INC-M03-206-G93][RLS] Consultas de usuario", "INC-M03-206-G93", 3, "", "Consultas de usuario"),
    ("[INC-M03-179-G57F [RF-57]] El detalle", "INC-M03-179-G57F", 3, "RF-57", "El detalle"),
    ("[INC-M03-RF62][RF-62] Índice de calidad", "INC-M03-RF62", 3, "RF-62", "Índice de calidad"),
    ("INC-M04-207-G46 Concurrencia y modos", "INC-M04-207-G46", 4, "", "Concurrencia y modos"),
    ("INC-M03-131-G23El buffer_sequence_id asignado", "INC-M03-131-G23", 3, "", "El buffer_sequence_id asignado"),
    ("INC-MM03-159-G47 Cálculo THI", "INC-M03-159-G47", 3, "", "Cálculo THI"),
    ("[TC-M03-122-G87][RF-58] Dashboard pierde", "TC-M03-122-G87", 3, "RF-58", "Dashboard pierde"),
    ("[INC-M04-195-G024 [RF-66]] BLOQUEO", "INC-M04-195-G024", 4, "RF-66", "BLOQUEO"),
]

for subject, id_, modulo, rf, titulo in CASOS:
    p = taiga.parsear_titulo(subject)
    assert p == {"id": id_, "modulo": modulo, "rf": rf, "titulo": titulo}, (subject, p)

# Sin ID reconocible: no es una incidencia parseable (se reporta como omitida).
for subject in ("[M03][TEST][DBA] Preparar datos semilla", "[M03][RF-60] Re-sembrar dispositivos", "Algo sin id"):
    assert taiga.parsear_titulo(subject) is None, subject

# El modulo sale del ID, no de menciones sueltas en el titulo.
assert taiga.parsear_titulo("[INC-M02-52-G92][M04][TEST] Provisionar identidad M04")["modulo"] == 2
print("ok")

assert taiga._excluida("Miguel Angel Ortiz Escobar") and taiga._excluida(None, "miguel ángel ortiz escobar ")
assert not taiga._excluida("Laura Lopez", "Daniela Castillo Vargas")
print("ok excluidos")
