# Manual de sincronización — Panel QA de SGPMP

Guía para que los resultados de pruebas, los CSV y los issues de Taiga se lean y analicen en el dashboard **sin errores silenciosos**.

> **Estado de este documento (2026-09-18).**
> - Las secciones 1–5 describen reglas que el `scanner.py` y el `dashboard.html` **ya aplican hoy** (verificadas leyendo el código y corriendo el escaneo sobre el árbol real).
> - La sección 6 (Taiga) es el **contrato** que deben cumplir los issues para que el conector —que **todavía no está construido**— los lea sin regex frágiles. Los puntos marcados _(propuesta)_ hay que acordarlos en equipo.

---

## 0. Cómo funciona (y por qué falla en silencio)

```
 carpetas de pruebas ──► scanner.py ──► /api/data ──► dashboard.html
 (backend + frontend)        ▲
                             ├── asignaciones.csv            (TC → responsable)
                             ├── declarados_sin_evidencia.csv (TC → veredicto a mano)
                             └── incidencias.csv             (hoy manual; Taiga desde el Módulo 3)
```

Cada vez que se refresca el navegador el scanner **relee todo desde disco** (no hay caché). Lo que el scanner no entiende **no genera error**: el caso cae a `Pendiente`. Por eso el manual insiste en nombres y formatos exactos.

### Las 10 reglas de oro

1. La carpeta del caso se llama `TC-M0<N>-G<n>` (o `TC-M0<N>-<n>`) y vive dentro de `RF-<n>`.
2. **Un solo reporte manda por TC**: el más «final». Todo el TC (todos sus sub-casos) debe ir en ese único reporte.
3. Solo se leen `.json`, `.html`, `.txt`, `.xml`. Un `.md`, `.png` o `.pdf` **no** cuenta como resultado.
4. Los reportes van en `resultados/` (o directo en la raíz de la carpeta del TC). **Nunca** en subcarpetas del TC como `EvaluacionV2/RESULTADOS/`.
5. Cada reintento lleva `reintentoN` en el nombre (no solo la fecha).
6. No guardar la colección Postman ni specs de Cypress dentro de `resultados/`.
7. Veredicto manual → solo en `declarados_sin_evidencia.csv`, con `Aprobado` o `Rechazado` escritos exactamente así.
8. Título de issue en Taiga: `[INC-M0<M>-<NN>-<TC>][RF-<n>] Resumen` — y el resto de datos en campos, no en el título.
9. Categoría, severidad, estado y evaluador salen de **listas cerradas** (sección 6.5), sin texto libre.
10. Fechas en `AAAA-MM-DD`.

---

## 1. Estructura de carpetas y nombres

```
sgpmp-backend/tests/Test_Testing/
  Test_Modulo<N>/
    RF-<nn>/
      TC-M0<N>-G<n>/            ← una carpeta por caso
        resultados/             ← reportes (nombre: resultados o resultado, cualquier mayúscula)
        evidencias/             ← capturas, .md, dumps (NO se leen)

SGPMP-FRONT-END-PWA/testing/test_testing/
  Modulo<N>/RF-<nn>/TC-M0<N>-G<n>/resultados/...
```

| Nivel | Patrón que acepta el scanner | Ejemplos válidos | Se **ignora en silencio** |
|---|---|---|---|
| Módulo | cualquier carpeta; el número sale de `Modulo\d+` | `Test_Modulo1`, `Modulo9` | `Test_Modulo_1`, `Mod1` (el dashboard no saca el número) |
| RF | `^RF-\d+$` | `RF-01`, `RF-30`, `RF-00` (transversales) | `RF-AE01`, `RF-01a`, `RF_01`, `RF01` |
| TC | `^TC-M0(\d+)-(G?)(\d+)$` (no distingue mayúsculas) | `TC-M01-071`, `TC-M09-G22`, `TC-M09-G022` | `TC-M09-158-G83`, `TC-M09_G22`, `INC-M01-24-...` |

Reglas:

- **El ID del TC es exactamente el de la carpeta**, normalizado a 3 dígitos: `G2`, `G02` y `G022` son el mismo caso (`TC-M09-G022`). Pero `TC-M09-22` (sin G) y `TC-M09-G22` son **casos distintos**.
- Módulo 9 y Módulo 2 usan casos agrupados **con G**. Módulo 1 usa número plano. No mezclar dentro del mismo módulo.
- Casos transversales (RF = «Todos») → `RF-00/`. Casos multi-RF → el primer RF de la lista.
- Un mismo TC probado en backend **y** frontend puede tener carpeta en ambos árboles: el dashboard los fusiona y **gana el peor veredicto** (Rechazado > Aprobado > Pendiente).
- Una carpeta vacía (solo `.gitkeep`) = `Pendiente`. Es correcto y esperado para casos aún no ejecutados.
- El responsable se asigna en `asignaciones.csv`, no en el nombre de carpeta.

---

## 2. Archivos de resultados

### 2.1 Formatos aceptados

| Formato | Extensión | Cómo generarlo | Cómo verificar que el scanner lo reconoce |
|---|---|---|---|
| Newman + htmlextra | `.html` | `newman run col.json -e env.json -r htmlextra --reporter-htmlextra-export resultados/resultado_<TC>.html` | El HTML contiene «Total Assertions» y «Total Failed Tests» |
| pytest-html | `.html` | `pytest ... --html=resultados/resultado_<TC>.html --self-contained-html` (pytest-html 4.x) | El HTML contiene `data-jsonblob=` |
| JUnit XML | `.xml` | `pytest ... --junitxml=resultados/resultado_<TC>.xml` | Raíz `<testsuite>`/`<testsuites>` con `tests=` |
| Cypress JSON propio | `.json` | Ver esquema abajo | Tiene la clave `checkpoints` |
| mochawesome | `.json` y/o `.html` | `--reporter mochawesome --reporter-options reportDir=resultados,reportFilename=resultado_<TC>,html=true,json=true` | JSON con `stats.tests` **entero**; el HTML con atributo `data-raw=` |
| Consola de Cypress | `.txt` | `npx cypress run --spec ... > resultados/consola_<TC>.txt` | Contiene «Run Finished» y la caja `(Results)` con Passing/Failing |
| k6 | `.json` | `k6 run --summary-export=resultados/resultado_<TC>.json script.js` | Tiene `metrics.checks`, o `resultados.veredicto` |

**Cypress JSON propio** (lo único que se lee es `checkpoints[].estado`, `paso` y `obtenido`):

```json
{
  "tc": "TC-M02-G10",
  "checkpoints": [
    { "paso": "CP-01 Login", "esperado": "200", "obtenido": "200", "estado": "OK" },
    { "paso": "CP-02 Crear activo", "esperado": "201", "obtenido": "500", "estado": "FALLA" }
  ]
}
```

- `estado` debe ser **exactamente** `OK` o `FALLA` (mayúsculas). Hoy el árbol tiene 9 checkpoints `OBSERVACION` y 10 sin `estado`: no cuentan ni como aprobados ni como fallidos (ver 3.2).
- En PowerShell 5, `>` guarda en UTF-16. El scanner lo entiende pero puede dañar tildes; preferir `... | Out-File -Encoding utf8 resultados/consola_<TC>.txt`.

### 2.2 Nombre de los archivos

```
resultados/resultado_<TC-ID>.<ext>                       primera corrida
resultados/resultado_<TC-ID>_reintento1.<ext>            primer reintento
resultados/resultado_<TC-ID>_reintento2.<ext>            segundo reintento
```

El scanner **no exige** un nombre concreto (lee el contenido), pero **usa el nombre para elegir cuál de varios reportes es el definitivo**, con este orden de prioridad:

| Marca en el nombre | Rango | Nota |
|---|---|---|
| _(ninguna)_ | 0 | primera corrida |
| `reintento`, `reintentoN`, `retest`, `retestN`, `retry`, `retryN`, `faseN`, `parteN` | N (1 si no hay número) | gana el mayor N |
| `v2.0`, `v1.3` (con punto) | mayor×100+menor | `v2` sin punto **no** se reconoce |
| `definitivo` | 1000 | gana siempre |

Desempate: fecha del último commit que tocó el archivo (mtime solo si aún no tiene commit) y, si dos reportes llegaron en el **mismo commit**, el nombre en orden alfabético inverso. Así el resultado es el mismo en cualquier clon.

Consecuencias prácticas:

- **Un sufijo de fecha (`_20260829`) no cuenta como reintento.** Si dos corridas se suben en el mismo commit, gana la de nombre alfabéticamente mayor, no la más nueva (así pasa con TC-M02-G032: `reporte_TC-M02-198.html` y `…-199.html` en el mismo commit). Usar siempre `reintentoN`, o subir cada corrida en su propio commit.
- **Usar un solo esquema de versionado** por TC. Mezclar `reintento2` (rango 2) con `v2.0` (rango 200) hace ganar a `v2.0` aunque sea más viejo.
- **No usar `definitivo` salvo que de verdad lo sea**: gana sobre cualquier reintento posterior (`reintento5` = 5 < 1000).
- **No partir un TC en `parte1`/`parte2`**: solo se lee el de mayor número, no se suman. Un TC agrupado = un reporte con todos sus sub-casos.
- Formato distinto entre corridas está permitido; gana la más «final» por la tabla de arriba.

### 2.3 Qué NO poner en `resultados/`

- La colección Postman (`*.postman_collection.json` o cualquier JSON con `info.schema` + `item`): el scanner la descarta.
- Specs (`*.cy.ts`, `*.cy.js`), `package.json`, `cypress.config.*`, `node_modules/`.
- Evidencia extra parseable (dumps `.json`/`.txt` de requests): va en `evidencias/`. Solo se recorre `resultados/` y la raíz del TC; `evidencias/` no se toca.
- Informes narrativos `.md`: van en `evidencias/` o junto al caso; **no cuentan como resultado** (para dar el veredicto, ver sección 5.2).
- Subcarpetas del TC con reportes (`EvaluacionV2/RESULTADOS/...`): el scanner no entra. Si la reevaluación es lo que vale, copiar su reporte a `resultados/` con `_reintentoN`, o declararlo en `declarados_sin_evidencia.csv`.

---

## 3. Cómo se decide Aprobado / Rechazado / Pendiente

### 3.1 Regla

```
sin carpeta, carpeta vacía, o ningún archivo .json/.html/.txt/.xml   → Pendiente ("sin_datos")
hay archivos pero ninguno reconocido                                 → Pendiente ("no_reconocido")  ← la trampa
≥ 1 aserción/checkpoint fallido                                      → Rechazado
0 fallidos y ≥ 1 aserción/checkpoint contado                         → Aprobado
0 aserciones contadas                                                → Pendiente
```

No existe el estado «Bloqueado»: un caso bloqueado por ambiente es `Pendiente` (o `Rechazado` si el reporte trae aserciones fallidas).

### 3.2 Qué cuenta cada formato

| Formato | Fallido | Aprobado | Omitido (skip/pending) |
|---|---|---|---|
| Newman | «Total Failed Tests» | Total Assertions − fallidas | — |
| pytest-html | `failed`, `error` | `passed` | **cuenta en el total** → un reporte todo-skipped sale **Aprobado** |
| JUnit XML | `failures` + `errors` | resto | se **descuenta** |
| Cypress JSON propio | `estado == "FALLA"` | `estado == "OK"` | otros valores cuentan en total → si son los únicos, sale **Aprobado** |
| mochawesome | `stats.failures` | `stats.passes` | `stats.tests` incluye pending → riesgo igual que pytest-html |
| Consola Cypress | `Failing` | `Passing` | pending/skipped se descuentan |
| k6 | check fallido o umbral incumplido | checks OK | — |

**Regla práctica:** no entregar como evidencia un reporte donde todo el TC quedó omitido (`skip`, `pending`, `OBSERVACION`); si el caso no se pudo ejecutar, no se sube reporte y se deja Pendiente con nota.

### 3.3 Verificar un caso antes de hacer commit

Desde `qa-dashboard/`:

```bash
python3 -c "import scanner as s; print([c for c in s.scan()['casos'] if c['tc_id']=='TC-M09-G022'])"
```

Debe mostrar `tipo_reporte` distinto de `no_reconocido`/`sin_datos`, y `total`, `passed`, `failed` coherentes con lo que ejecutaste. Si `/api/data` da 500, correr ese mismo comando: el banner «No se pudo conectar con el servidor local» es engañoso, el error real sale en la consola.

---

## 4. Casos especiales

| Situación | Qué hacer |
|---|---|
| **Reintento tras corrección** | Guardar `resultado_<TC>_reintentoN.<ext>` en la misma carpeta `resultados/`. El anterior se queda como historial. |
| **Reevaluación V2** | Copiar el reporte a `resultados/` con `_reintentoN`, o (si es un `.md`) usar `declarados_sin_evidencia.csv`. La subcarpeta `EvaluacionV2/RESULTADOS/` no se escanea. |
| **Caso bloqueado por ambiente** | No subir reporte falso. Dejar el TC sin reporte (Pendiente) + `.md` en `evidencias/` con la causa + issue en Taiga si es un defecto de ambiente. |
| **Reporte con varios formatos** | Aceptado; gana el de mayor rango de nombre. Único caso especial: si el ganador es JUnit XML y otro reporte de la misma carpeta trae fallos, gana el que trae fallos. |
| **Caso ejecutado a mano (sin herramienta)** | `declarados_sin_evidencia.csv` con nota que apunte al `.md`/captura. |
| **Reporte nunca subido al repo** | El TC queda Pendiente. Hacer commit del reporte (o declararlo con evidencia real). Caso hoy: TC-M09-G81. |

---

## 5. Archivos CSV manuales (en `qa-dashboard/`)

Todos: UTF-8 (con o sin BOM), separador coma, primera fila = encabezado **exacto**, celdas con comas entre comillas dobles. Se releen al pulsar **Actualizar** en el panel o en la actualización automática (cada 15 min); en el despliegue de Dokploy van dentro de la imagen, así que un cambio exige push + redeploy.

### 5.1 `asignaciones.csv`

```csv
TC,Responsable
TC-M09-G022,Juan Esteban
```

- El nombre se agrupa por **texto exacto**: `Juan Manuel` ≠ `Juan  Manuel` ≠ `juan manuel`. Escribirlo idéntico en todas las filas.
- Valores hoy en uso: Laura Lopez, Daniela Castillo, Juan Manuel, Sebastian, Juan Pablo, Juan Esteban, Juan Parra, Juan Hernando. Estado actual: 377 de 377 TC asignados.
- Un TC que no esté en el archivo sale como «Sin asignar».

### 5.2 `declarados_sin_evidencia.csv`

```csv
TC,Estado,Nota
TC-M09-G022,Aprobado,"Reevaluación V2 (14-sep-2026) en EvaluacionV2/RESULTADOS/.../TC-M09-G22_reevaluacion_V2.md. Defecto corregido en PR #146."
```

- `Estado`: **`Aprobado`** o **`Rechazado`**, con esa mayúscula y sin variantes (`APROBADO`, `Aprobada`, `OK` no suman en los contadores).
- **Gana siempre** sobre lo que el scanner detecte en la carpeta (aunque haya un reporte reconocido). Úsalo solo cuando QA ya revisó todo a mano; si más adelante se sube un reporte nuevo y bueno, **borrar la fila** o seguirá pisándolo.
- `Nota` obligatoria: qué archivo respalda el veredicto, fecha y quién lo corrió.
- No es para tapar un caso sin ejecutar.

### 5.3 `incidencias.csv` (módulos 1, 2 y 9 mientras no exista el conector de Taiga)

Encabezado exacto (14 columnas, sin tildes, en este orden):

```
Modulo,ID,Descripcion del error,RF relacionado,Categoria del error,Equipo responsable,Severidad,Tiempo maximo de solucion,Fecha deteccion,Fecha limite,Estado,Fecha correccion real,Evidencia / Notas,Evaluador
```

Qué lee realmente el dashboard:

| Columna | Uso |
|---|---|
| `ID` | **Define el módulo** (patrón `INC-M0<n>-`); también se busca por texto |
| `Descripcion del error`, `RF relacionado`, `Categoria del error`, `Equipo responsable` | Tabla y buscador |
| `Categoria del error` | Se clasifica en los 15 códigos (sección 6.5) |
| `Severidad`, `Estado`, `Fecha limite`, `Evaluador` | Tabla y filtro por estado |
| `Modulo`, `Tiempo maximo de solucion`, `Fecha deteccion`, `Fecha correccion real`, `Evidencia / Notas` | Se guardan pero **el dashboard hoy no los usa** |

Problemas reales del CSV actual que este manual corrige:

- `Severidad`: existen `Crítico`, `Critico`, `media`, `Medio`, `Severo`, `Baja` → escribir solo los 4 valores de 6.5.
- `Equipo responsable`: una fila tiene una oración entera → solo el nombre del equipo.
- `RF relacionado`: `RF-10 – Consultar Historial…`, `RF-02 (relacionado RF-06)` → solo `RF-NN`.
- `ID` con dos casos: `INC-M01-11-87/89` → un ID por incidencia.
- Una fila tiene 13 campos en lugar de 14 (`INC-M01-01-01`, falta `Evaluador`): revisar que ninguna quede corta.
- **Fechas**: hoy `28/08/26`. El orden por «Fecha límite» es **alfabético** (`5/09/26` queda después de `31/08/26`). Usar `AAAA-MM-DD` (`2026-09-05`) y, si aún no se cambia el dashboard, pedir que acepte ambos formatos.

---

## 6. Taiga — cómo crear issues

Proyecto: `danielruiz7514-proyecto-integrador-iii-2026-1` (id 1771570). Plan: `incidencias.csv` manual para los módulos 1, 2 y 9; conexión por API a Taiga desde el módulo 3 (auth con token en variable de entorno local, nunca pegado en chat ni en el repo).

**Punto de partida real (2026-09-18, 182 issues):**

| Forma del título | Issues |
|---|---|
| `[INC-M0x-NN-TC][RF-nn] ...` (canónica) | 31 |
| `[INC-...` con otras variantes (`v2.0`, `?`, RF dentro del corchete...) | 82 |
| `INC-...` sin corchetes | 27 |
| `TC-...` (usan el ID del caso, no de incidencia) | 19 |
| otros / `NC-` | 23 |

Además: **0 issues con descripción** y **0 campos personalizados** creados. Con esto el conector tendría que adivinar módulo, RF y tipo de error a partir de texto libre — el mismo problema de «cae a Pendiente en silencio» que ya tiene el scanner.

### 6.1 Cuándo crear un issue

- **Tipo `Bug`** para todo defecto encontrado por una prueba. (`Question` y `Enhancement` no cuentan como incidencias del dashboard.)
- Un issue = **un defecto**. Un caso rechazado por un defecto conocido enlaza al issue existente; no crea otro.
- No crear issues para casos solo Pendientes o bloqueados por ambiente, salvo que el bloqueo sea un defecto.

### 6.2 Título (lo único que se parsea por regex)

```
[INC-M0<M>-<NN>-<TC>][RF-<n>] <resumen del defecto>
```

| Parte | Regla | Ejemplo |
|---|---|---|
| `INC-M0<M>` | Módulo 1 dígito, con el `0` | `INC-M02` |
| `<NN>` | Consecutivo del módulo, 2 dígitos, nunca se reutiliza | `39` |
| `<TC>` | El ID del caso **como se llama la carpeta**: `G90` o `071` (sin `TC-M0x-`) | `G90` |
| `[RF-<n>]` | Un solo RF | `[RF-49]` |
| Resumen | Empieza por el síntoma, ≤ 120 caracteres, sin saltos de línea | `POST /activos-biologicos/{id}/sensores exige id_activo obligatorio` |

Regex del contrato: `^\[(INC-M0\d-\d{2,3}-(?:G\d+|\d+))\]\[(RF-\d+)\]\s+(\S.+)$`

Ejemplos:

```
✔  [INC-M02-39-G90][RF-49] POST /activos-biologicos/{id}/sensores graba asociación AMBIENTAL atada a un activo
✘  [INC-M02-?-g52 [RF-41]] POST /activos-biologicos devuelve 500...        (número desconocido, RF anidado)
✘  [NC-M02-40-G89 v2.0][RF-49]El ciclo de vida...                          (prefijo NC, versión en el título)
✘  TC-M02-G13 Error interno durante el registro de activos biológicos     (es un ID de caso, no de incidencia)
✘  INC-M02-G08 — Error 500 al registrar activos biológicos en RF-33        (sin corchetes, sin consecutivo)
✘  [INC-M02-34-G29/G31 v2.0][RF-36]...                                     (dos TC en el ID)
```

Reglas adicionales:

- **Varios TC afectados** → el ID lleva el principal; el resto va en el campo «TCs afectados» (6.3).
- **Ronda/versión (`v2.0`)** → etiqueta (tag) `v2.0` o campo «Ronda de evaluación», nunca en el título.
- **TC agrupado y sub-caso original** (`TC-M09-169-G89`) → en el ID va el agrupado (`G89`); el número original se cita en la descripción.
- **Número consecutivo**: antes de crear, buscar `INC-M0<M>-` en Taiga y tomar el siguiente. Dos personas creando a la vez pueden chocar; el validador (sección 8) detectará IDs duplicados.

### 6.3 Campos personalizados a crear _(propuesta; requiere admin del proyecto)_

Crear en Taiga → Admin → Atributos personalizados → Issues:

| Campo Taiga | Tipo | Columna CSV | Obligatorio |
|---|---|---|---|
| Categoría del error | Desplegable (15 códigos, 6.5) | Categoria del error | Sí |
| Equipo responsable | Desplegable | Equipo responsable | Sí |
| RF relacionado | Texto (`RF-NN`) | RF relacionado | Sí (debe coincidir con el título) |
| Evaluador | Desplegable (nombres de `asignaciones.csv`) | Evaluador | Sí |
| Fecha detección | Fecha | Fecha deteccion | Sí |
| Fecha límite | Fecha | Fecha limite | Sí |
| Fecha corrección real | Fecha | Fecha correccion real | Al pasar a «Corregido» |
| TCs afectados | Texto (`G29, G31`) | — | Si aplica |
| Ronda de evaluación | Texto o tag (`v2.0`) | — | Si aplica |

Campos **nativos** de Taiga que se reutilizan:

| Nativo Taiga | Equivale a |
|---|---|
| Severity | Severidad (mapa en 6.5) |
| Status | Estado (mapa en 6.5) |
| Type | debe ser `Bug` |
| Priority | no se usa (dejar `Normal`) |

Módulo **no** necesita campo: sale del ID. `Tiempo máximo de solución` tampoco: se deduce de la severidad.

### 6.4 Descripción (plantilla)

```markdown
**Caso:** TC-M02-G90 (backend)   **Ronda:** v2.0
**Endpoint / Pantalla:** POST /activos-biologicos/{id_activo}/sensores

**Pasos:**
1. ...
2. ...

**Resultado esperado:** ...
**Resultado obtenido:** ... (HTTP 500, error_code: ...)

**Evidencia (ruta relativa al árbol de pruebas):**
- Test_Modulo2/RF-49/TC-M02-G90/resultados/resultado_TC-M02-G90.html
- (adjuntar captura o request/response en Taiga)

**Causa probable (opcional):** ...
```

Reglas: nunca pegar tokens, contraseñas ni datos personales reales; usar las rutas tal cual aparecen en el repo para poder cruzarlas con el escaneo.

### 6.5 Listas cerradas

**Severidad ↔ tiempo máximo (derivado de los datos actuales)**

| Severidad (dashboard) | Severity (Taiga) | Tiempo máximo | Fecha límite = |
|---|---|---|---|
| Crítico | Critical | 4 horas (mismo día) | fecha detección |
| Severo | Important | 1 día hábil | detección + 1 día hábil |
| Medio | Normal | 2 días hábiles | detección + 2 días hábiles |
| Baja | Minor | 3 días hábiles | detección + 3 días hábiles |

`Wishlist` no se usa. Escribir siempre con tilde: `Crítico`.

**Estado ↔ Status de Taiga** _(propuesta)_

| Status Taiga | Estado en dashboard | Significado |
|---|---|---|
| New | Abierto | Reportado, sin trabajar |
| In progress | En corrección | Desarrollo trabajando |
| Ready for test | Por verificar | Desarrollo dice que está listo; QA debe re-probar |
| Closed | Corregido | **Solo con retroprueba exitosa** y `Fecha corrección real` + evidencia del retest |
| Rejected | Descartado | No es defecto (con motivo en comentario) |
| Needs Info | Requiere información | Falta dato del evaluador |
| Postponed | Aplazado | Decisión explícita de no corregir ahora |

**Categoría del error (15 códigos + uno de reserva).** La celda debe contener **solo el código**; si trae varios, el dashboard toma el que aparece primero.

| Código | Cuándo | Equipo responsable |
|---|---|---|
| `VAL_ENTRADA` | Datos mal formados, obligatorios vacíos, fuera de rango | Desarrollo |
| `UNICIDAD` | Dato válido que viola unicidad o integridad | Desarrollo |
| `ESTADO` | Entidad válida pero en estado incorrecto para la operación | Desarrollo |
| `FLUJO` | Falla en proceso multietapa (tokens, lotes, rollback) | Desarrollo |
| `INFRAESTRUC` | BD, correo, red, almacenamiento, ambiente | Implementación (TEST) / Despliegue (producción) |
| `HTTP_COM` | Contrato de API, timeouts, credenciales de dispositivo | Desarrollo / Implementación |
| `AUTORIZACION` | Autenticado pero sin permiso (RBAC, BOLA) | Desarrollo |
| `AUTENTICACION` | Login o gestión de sesión/token | Desarrollo |
| `AUDITORIA` | Evento no registrado / incompleto / no consultable | Desarrollo |
| `SEGURIDAD` | Otros OWASP: rate limit, exposición de datos | Desarrollo / Implementación |
| `FUNCIONAL` | 2xx pero regla de negocio incorrecta (catch-all) | Desarrollo |
| `UI_ACCESIBILIDAD` | WCAG, consistencia visual, axe-core | Desarrollo |
| `FRONT_RENDER` | La UI no refleja el estado real de los datos | Desarrollo Frontend |
| `FRONT_ESTADO_CLIENTE` | Estado local/global del cliente, doble envío | Desarrollo Frontend |
| `FRONT_PWA` | Service worker, offline, manifest, instalación | Desarrollo Frontend |
| _(`SIN_CLASIFICAR`)_ | Reserva del dashboard, **no** se elige a propósito | — |

Definición completa: sección desplegable «Taxonomía de categorías de error» del dashboard. Si un defecto real no encaja en ninguno, no inventar un código: escribirlo en la descripción y avisar para ampliar la taxonomía.

**Evaluador:** nombre exacto como en `asignaciones.csv` (`Laura Lopez`, `Daniela Castillo`, ...). El CSV actual usa solo el primer nombre (`Daniela`, `Laura`); unificar.

---

## 7. Hallazgos en el árbol actual (para corregir ya)

| # | Dónde | Problema | Efecto | Arreglo |
|---|---|---|---|---|
| 1 | `Test_Modulo7/RF-AE01`, `RF-AE02` | Nombre no sigue `RF-<n>` (y están vacías) | Se ignoran | Renombrar a `RF-nn` o borrar |
| 2 | `Test_Modulo1/RF-03/INC-M01-24-500-nombre-rol/` | Carpeta de incidencia dentro de un RF (contiene script de reproducción) | Se ignora (no es `TC-`) | Mover a `anotaciones/modulo_1/` o `evidencias/` |
| 3 | TC-M09-G29, G30, G77 | Veredicto solo en un `.md` | `no_reconocido` → Pendiente | Fila en `declarados_sin_evidencia.csv` |
| 4 | TC-M09-G81 | Reporte nunca subido | `no_reconocido` → Pendiente | Hacer commit del reporte |
| 5 | Reevaluaciones V2 | Reporte en `EvaluacionV2/RESULTADOS/` | Se sigue mostrando el veredicto V1 | Copiar a `resultados/` con `_reintentoN` (hoy se compensa con filas en `declarados_sin_evidencia.csv`, 15 en total) |
| 6 | `incidencias.csv` | Severidad, RF, equipo y fechas con formatos mezclados; una fila con 13 campos | Filtros inconsistentes; orden por fecha incorrecto | Sección 5.3 |
| 7 | Taiga | 151 de 182 títulos fuera de contrato, sin descripción ni campos | El conector no podría leerlos de forma fiable | Secciones 6.2–6.4 (retitular los abiertos; los cerrados, según prioridad) |

---

## 8. Listas de verificación

**Al cerrar un TC**
- [ ] Carpeta `TC-M0<N>-G<n>` bajo `RF-<nn>` correcto.
- [ ] Reporte en `resultados/` en uno de los 7 formatos, con todos los sub-casos.
- [ ] Reintentos con `reintentoN`.
- [ ] Comando de 3.3: `tipo_reporte` reconocido y `estado` esperado.
- [ ] Responsable en `asignaciones.csv`.
- [ ] Si falló: issue en Taiga creado (6.2) y su ID citado en `evidencias/`.

**Al crear un issue en Taiga**
- [ ] Type = Bug; título con la regex de 6.2; un solo TC en el ID y un solo RF.
- [ ] Severity y Status de las listas cerradas.
- [ ] Categoría (código), Equipo, RF, Evaluador, Fecha detección y Fecha límite llenos.
- [ ] Descripción con la plantilla y rutas de evidencia.
- [ ] Al pasar a Closed: Fecha corrección real + reporte del retest en `resultados/`.

---

## 9. Decisiones pendientes

1. **Crear los campos personalizados de 6.3** (requiere que un admin del proyecto lo haga). Sin ellos el conector tendría que parsear texto libre.
2. **Aprobar el mapa de estados de 6.5** (sobre todo `Ready for test` ≠ `Closed`).
3. **Formato de fecha**: pasar a `AAAA-MM-DD` o hacer que el dashboard acepte ambos.
4. **Quién asigna el consecutivo `NN`** para evitar choques (un responsable de QA por módulo, o derivarlo del número interno de Taiga).
5. **Códigos de categoría**: se ampliaron de 8 a 15 el 2026-09-18; confirmar que ya es la taxonomía oficial y comunicarla a Desarrollo.
6. Si se quiere, un script `validar.py` que revise todo lo de este manual (carpetas, nombres, CSV, títulos de Taiga) y lo reporte **antes** de que llegue al dashboard.
