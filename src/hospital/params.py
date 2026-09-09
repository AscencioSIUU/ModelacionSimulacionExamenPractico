"""
Datos de partida y supuestos.

Los valores se transcriben del Excel `data/raw/Grupo2_SistemaHospitalario.xlsx`
(hoja `Datos_Grupo2`).

El módulo separa tres cosas:

1. Datos del Excel (no se tocan sin cambiar la fuente).
2. Supuestos — un único bloque editable, delimitado por marcas.
   Todo lo que el Excel no da y el modelo necesita vive ahí.
3. Contrato de mecánica — nombres de recursos, esquemas de los tres logs y
   parámetros del integrador.

Convención de unidades: TODO se normaliza a base horaria.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np

RAIZ = Path(__file__).resolve().parents[2]
XLSX = RAIZ / "data" / "raw" / "Grupo2_SistemaHospitalario.xlsx"
JSON_OUT = RAIZ / "data" / "processed" / "parametros.json"

ZONAS = ["Z1", "Z2", "Z3", "Z4", "Z5"]
GRAVEDADES = ["leve", "moderado", "grave"]

HORIZONTE_H = 72
BLOQUE_H = 6
N_BLOQUES = HORIZONTE_H // BLOQUE_H  # 12


@dataclass(frozen=True)
class Instalacion:
    nombre: str
    zona: str
    camas: int          # camas generales
    uci: int            # camas de cuidados intensivos
    quirofanos: int
    operativa: bool


@dataclass(frozen=True)
class ZonaHeridos:
    zona: str
    leve: int
    moderado: int
    grave: int
    fallecidos: int     # ya fallecidos en T0, no entran al sistema

    @property
    def total_atencion(self) -> int:
        return self.leve + self.moderado + self.grave

    def proporciones(self) -> dict[str, float]:
        t = self.total_atencion
        return {g: getattr(self, g) / t for g in GRAVEDADES}


@dataclass(frozen=True)
class Suministro:
    nombre: str
    unidad: str
    stock_inicial: float
    consumo_dia_base: float   # consumo/día agregado del escenario base (Excel)

    @property
    def consumo_hora_base(self) -> float:
        return self.consumo_dia_base / 24.0


# =============================================================================
# DATOS DEL EXCEL
# =============================================================================

# --- 1. Infraestructura hospitalaria (Excel 1) --------------------------------
INSTALACIONES: list[Instalacion] = [
    Instalacion("Hospital General UVG", "Z1", 320, 24, 6, True),
    Instalacion("Clínica Z2", "Z2", 80, 4, 1, True),
    Instalacion("Centro Salud Z3", "Z3", 45, 0, 0, False),   # CERRADO — daños estructurales
    Instalacion("Hospital Regional Este", "Z4", 210, 18, 4, True),
    Instalacion("Puesto Salud Z5", "Z5", 20, 0, 0, True),    # capacidad mínima
]

# --- 2. Heridos por zona en T0 (Excel 2) --------------------------------------
HERIDOS_T0: list[ZonaHeridos] = [
    ZonaHeridos("Z1", 2840, 890, 320, 145),
    ZonaHeridos("Z2", 680, 210, 58, 22),
    ZonaHeridos("Z3", 1120, 380, 140, 67),
    ZonaHeridos("Z4", 410, 120, 35, 12),
    ZonaHeridos("Z5", 3200, 980, 390, 178),
]

# --- 3. Tasas y tiempos (Excel 3) ---------------------------------------------
# Tiempo medio de atención por gravedad, en horas.
T_ATENCION_H = {"leve": 0.5, "moderado": 3.0, "grave": 8.0}

# Tasa de llegada al hospital como fracción/hora del pool restante, por tramo.
LAMBDA_LLEGADA_H = {
    (0, 6): 0.18,
    (6, 24): 0.07,
    (24, HORIZONTE_H): 0.02,
}
LAMBDA_MAX = max(LAMBDA_LLEGADA_H.values())  # cota para thinning (Lewis–Shedler)

MEDICOS = 87
ENFERMERAS = 142
PAC_POR_MEDICO_NORMAL = 4
PAC_POR_MEDICO_EMERGENCIA = 8

# --- 4. Suministros médicos (Excel 4) -----------------------------------------
SUMINISTROS: list[Suministro] = [
    Suministro("sangre", "unidades", 480, 120),
    Suministro("solucion_salina", "litros", 2800, 600),
    Suministro("analgesicos", "dosis", 1200, 280),
    Suministro("antibioticos", "dosis", 3500, 450),
    Suministro("material_quirurgico", "kits", 85, 18),
    Suministro("combustible_generadores", "galones", 1800, 320),
]


# =============================================================================
# SUPUESTOS
# =============================================================================

# --- S-1. Personal: presentismo, reparto por instalación y modo emergencia ----
# El Excel da un total de ciudad (87 médicos) y los ratios pac/médico, pero no
# dice cómo se reparte entre instalaciones ni cuántos se presentan tras el sismo.
PRESENTISMO_PERSONAL = 0.85            # fracción del personal que llega a trabajar
UMBRAL_EMERGENCIA = 0.85               # ocupación que dispara el ratio de emergencia
MEDICOS_MINIMOS_POR_INSTALACION = 1    # ninguna instalación operativa queda sin médico

# --- S-2. Consumo de insumos: cantidad fija por procedimiento -----------------
# El Excel solo da consumo agregado por día del escenario base. R5 exige una
# cantidad fija por procedimiento, así que se asigna por gravedad según la vía
# clínica que sigue cada paciente:
#   - Leve: curación básica, sin insumo inventariado.
#   - Moderado: suero y antibiótico.
#   - Grave: cirugía completa (kit, sangre, suero, morfina y antibiótico).
CONSUMO_POR_PROCEDIMIENTO: dict[str, dict[str, float]] = {
    "leve":     {},
    "moderado": {"solucion_salina": 0.5, "antibioticos": 1.0},
    "grave":    {"sangre": 3.0, "material_quirurgico": 1.0, "solucion_salina": 5.0,
                 "analgesicos": 4.0, "antibioticos": 4.0},
}

# El combustible no se consume por procedimiento: es un flujo continuo por
# instalación operativa (320 gal/día / 4 operativas / 24 h).
COMBUSTIBLE_GAL_H_POR_INSTALACION = 320 / 4 / 24

# Reposición externa por hora. En 0 no llega ayuda.
TASA_REABASTECIMIENTO_H: dict[str, float] = {s.nombre: 0.0 for s in SUMINISTROS}
TOL_STOCK = 1e-9       # tolerancia de comparación de stocks (ruido de punto flotante)
EPS_STOCK = 1e-6       # término saturante del flujo continuo: el outflow se frena en 0

# --- S-3. Geografía: distancias entre zonas y tiempo de traslado τ ------------
# El Excel no trae distancias. Z1 es el centro histórico; Z4 y Z5 son extremos
# opuestos. Distancias en km, simétricas, diagonal 0.
DIST_ZONAS_KM: dict[str, dict[str, float]] = {
    "Z1": {"Z1": 0.0, "Z2": 5.0,  "Z3": 7.0,  "Z4": 12.0, "Z5": 9.0},
    "Z2": {"Z1": 5.0, "Z2": 0.0,  "Z3": 9.0,  "Z4": 15.0, "Z5": 13.0},
    "Z3": {"Z1": 7.0, "Z2": 9.0,  "Z3": 0.0,  "Z4": 14.0, "Z5": 11.0},
    "Z4": {"Z1": 12.0, "Z2": 15.0, "Z3": 14.0, "Z4": 0.0,  "Z5": 18.0},
    "Z5": {"Z1": 9.0, "Z2": 13.0, "Z3": 11.0, "Z4": 18.0, "Z5": 0.0},
}
VELOCIDAD_TRASLADO_KMH = 25.0   # ambulancia con vías dañadas
TAU_FIJO_H = 0.25               # despacho y triaje en sitio, aun sin desplazarse

# --- S-4. Ventana de supervivencia en cola (mortalidad evitable, R6) ----------
# Media de una exponencial: tiempo que tolera un herido sin atención.
T_TOLERANCIA_COLA_H = {"leve": 120.0, "moderado": 24.0, "grave": 4.0}

# --- S-5. Mortalidad clínica inevitable al terminar la atención ---------------
P_MORTALIDAD_CLINICA = {"leve": 0.001, "moderado": 0.02, "grave": 0.15}

# --- S-6. Estocasticidad: forma de las distribuciones y ruido paramétrico -----
# Los tiempos de servicio son exponenciales con las medias del Excel.
# Sin ruido paramétrico, el IC 95 % mediría la precisión del simulador y no la
# incertidumbre del escenario. Cada réplica escala parámetros con un
# multiplicador lognormal de mediana 1: m = exp(N(0, sigma)).
SEMILLA_BASE = 20241114
PERTURBACION_MC = {
    "heridos_t0":        0.10,   # por zona (heterogeneidad del censo)
    "lambda_llegada":    0.15,   # global (un multiplicador para los 3 tramos)
    "t_atencion":        0.10,   # global
    "stock_inicial":     0.10,   # por suministro
    "p_mort_clinica":    0.20,   # global
    "t_tolerancia_cola": 0.15,   # global
    "presentismo":       0.05,   # aditivo N(0, 0.05), truncado a [0.60, 1.00]
}
PRESENTISMO_MIN, PRESENTISMO_MAX = 0.60, 1.00

# --- S-7. Fatiga del personal (sustrato SD) -----------------------------------
FATIGA_ALFA = 0.08          # desgaste por unidad de carga relativa, por hora
FATIGA_BETA = 0.02          # recuperación hacia energía plena, por hora
FATIGA_FACTOR_MAX = 2.0     # multiplicador del tiempo de servicio con energía 0


# =============================================================================
# CONTRATO DE MECÁNICA — recursos, integrador y esquemas de salida
# =============================================================================

DT_SD_H = 0.25                  # paso de integración del sustrato SD
METODO_INTEGRACION = "euler"    # Euler explícito en producción

# Recursos servibles que un paciente ocupa mientras se le atiende.
RECURSOS_SERVIBLES = ("cama_general", "cama_uci", "quirofano", "medico")

# R3 — qué recursos exige cada gravedad, todos simultáneamente.
RECURSOS_POR_GRAVEDAD: dict[str, tuple[str, ...]] = {
    "leve":     ("medico",),
    "moderado": ("medico", "cama_general"),
    "grave":    ("medico", "quirofano", "cama_uci"),
}

# Recursos cuyas horas de bloqueo se contabilizan: servibles + insumos.
RECURSOS_BLOQUEO = RECURSOS_SERVIBLES + tuple(s.nombre for s in SUMINISTROS)

# Esquemas de los tres logs.
COLS_PATIENT_LOG = ("replica", "pid", "zona", "triage", "t_llegada", "t_inicio",
                    "t_fin", "instalacion", "estado", "espera")
COLS_STATE_LOG = ("replica", "t", "instalacion", "recurso", "ocupados", "capacidad", "cola")
COLS_STOCK_LOG = ("replica", "t", "suministro", "nivel")

# Estados en los que un paciente sigue dentro del sistema al cortar el horizonte.
ESTADOS_EN_SISTEMA = ("en_traslado", "en_cola", "en_atencion")
ESTADOS_PACIENTE = ESTADOS_EN_SISTEMA + ("atendido", "muerte_clinica", "muerte_evitable")


def make_rng(seed: int) -> np.random.Generator:
    """Único punto de aleatoriedad del modelo. Nada de `np.random` global."""
    return np.random.default_rng(seed)


def reparto_personal(
    instalaciones: Sequence[Instalacion],
    medicos_totales: int,
) -> dict[str, int]:
    """
    S-1. Reparte `medicos_totales` entre las instalaciones operativas de forma
    proporcional a sus camas operativas (generales + UCI), por mayor resto.

    Garantiza que la suma es exacta y que ninguna operativa queda por debajo de
    MEDICOS_MINIMOS_POR_INSTALACION.
    """
    operativas = [i for i in instalaciones if i.operativa]
    if not operativas or medicos_totales <= 0:
        return {i.nombre: 0 for i in operativas}

    minimo = MEDICOS_MINIMOS_POR_INSTALACION
    if medicos_totales < minimo * len(operativas):
        raise ValueError("No hay médicos suficientes para el mínimo por instalación")

    camas = {i.nombre: i.camas + i.uci for i in operativas}
    total_camas = sum(camas.values())
    repartibles = medicos_totales - minimo * len(operativas)

    # Cuota de Hare: parte entera + mayor resto sobre lo que sobra del mínimo.
    exacto = {n: repartibles * c / total_camas for n, c in camas.items()}
    reparto = {n: minimo + int(v) for n, v in exacto.items()}
    faltan = medicos_totales - sum(reparto.values())
    for nombre in sorted(exacto, key=lambda n: (-(exacto[n] % 1), -camas[n], n))[:faltan]:
        reparto[nombre] += 1

    assert sum(reparto.values()) == medicos_totales, "el reparto no suma el total"
    return reparto


def tau_traslado(
    zona_origen: str,
    zona_destino: str,
    dist: dict[str, dict[str, float]] | None = None,
) -> float:
    """
    S-3. Tiempo de traslado en horas: despacho fijo + distancia / velocidad.
    Intra-zona (origen == destino) cuesta solo el despacho.
    """
    d = (dist or DIST_ZONAS_KM)[zona_origen][zona_destino]
    return TAU_FIJO_H + d / VELOCIDAD_TRASLADO_KMH


def matriz_tau(dist: dict[str, dict[str, float]] | None = None) -> dict[str, dict[str, float]]:
    """Vuelca τ para todas las parejas de zonas (reporte y `volcar_json`)."""
    return {o: {d: tau_traslado(o, d, dist) for d in ZONAS} for o in ZONAS}


def capacidad_instalada(instalacion: Instalacion, recurso: str) -> int:
    """Capacidad nominal de un recurso servible en una instalación."""
    if recurso == "cama_general":
        return instalacion.camas
    if recurso == "cama_uci":
        return instalacion.uci
    if recurso == "quirofano":
        return instalacion.quirofanos
    if recurso == "medico":
        return 1 if instalacion.operativa else 0   # el cupo real lo fija el reparto S-1
    raise KeyError(f"recurso desconocido: {recurso}")


def orden_derivacion(
    instalaciones: Sequence[Instalacion],
    dist: dict[str, dict[str, float]] | None = None,
) -> dict[tuple[str, str], tuple[str, ...]]:
    """
    R2. `(zona_origen, gravedad) -> instalaciones candidatas ordenadas por cercanía`.

    Una instalación es candidata si está operativa Y tiene capacidad > 0 en TODOS
    los recursos que exige esa gravedad. Excluir las de capacidad 0 evita el
    bloqueo permanente.

    Orden: τ ascendente; a igual τ, primero la de mayor capacidad crítica.
    """
    tabla: dict[tuple[str, str], tuple[str, ...]] = {}
    for zona in ZONAS:
        for gravedad, recursos in RECURSOS_POR_GRAVEDAD.items():
            candidatas = [
                i for i in instalaciones
                if i.operativa and all(capacidad_instalada(i, r) > 0 for r in recursos)
            ]
            candidatas.sort(key=lambda i: (
                tau_traslado(zona, i.zona, dist),
                -min(capacidad_instalada(i, r) for r in recursos),
                i.nombre,
            ))
            assert candidatas, f"sin instalación candidata para ({zona}, {gravedad})"
            tabla[(zona, gravedad)] = tuple(i.nombre for i in candidatas)
    return tabla


@dataclass
class ParamsSistema:
    """Paquete de parámetros que consumen des.py, sd.py, simulacion.py y montecarlo.py."""
    instalaciones: list[Instalacion] = field(default_factory=lambda: list(INSTALACIONES))
    heridos_t0: list[ZonaHeridos] = field(default_factory=lambda: list(HERIDOS_T0))
    suministros: list[Suministro] = field(default_factory=lambda: list(SUMINISTROS))
    t_atencion_h: dict = field(default_factory=lambda: dict(T_ATENCION_H))
    lambda_llegada_h: dict = field(default_factory=lambda: dict(LAMBDA_LLEGADA_H))
    medicos: int = MEDICOS
    enfermeras: int = ENFERMERAS
    pac_por_medico_normal: int = PAC_POR_MEDICO_NORMAL
    pac_por_medico_emergencia: int = PAC_POR_MEDICO_EMERGENCIA
    umbral_emergencia: float = UMBRAL_EMERGENCIA
    presentismo: float = PRESENTISMO_PERSONAL
    t_tolerancia_cola_h: dict = field(default_factory=lambda: dict(T_TOLERANCIA_COLA_H))
    p_mortalidad_clinica: dict = field(default_factory=lambda: dict(P_MORTALIDAD_CLINICA))
    consumo_por_procedimiento: dict = field(
        default_factory=lambda: {g: dict(d) for g, d in CONSUMO_POR_PROCEDIMIENTO.items()})
    tasa_reabastecimiento_h: dict = field(default_factory=lambda: dict(TASA_REABASTECIMIENTO_H))
    combustible_gal_h: float = COMBUSTIBLE_GAL_H_POR_INSTALACION
    dist_zonas_km: dict = field(default_factory=lambda: {o: dict(d) for o, d in DIST_ZONAS_KM.items()})
    dt_sd_h: float = DT_SD_H
    metodo_integracion: str = METODO_INTEGRACION
    fatiga_alfa: float = FATIGA_ALFA
    fatiga_beta: float = FATIGA_BETA
    fatiga_factor_max: float = FATIGA_FACTOR_MAX

    def instalacion(self, nombre: str) -> Instalacion:
        return next(i for i in self.instalaciones if i.nombre == nombre)

    def zona_de(self, nombre: str) -> str:
        return self.instalacion(nombre).zona

    def tau(self, zona_origen: str, zona_destino: str) -> float:
        """S-3, con la matriz de distancias de esta instancia."""
        return tau_traslado(zona_origen, zona_destino, self.dist_zonas_km)

    def orden_derivacion(self) -> dict[tuple[str, str], tuple[str, ...]]:
        """R2, con las instalaciones de esta instancia (cambian con la intervención)."""
        return orden_derivacion(self.instalaciones, self.dist_zonas_km)

    def medicos_efectivos(self) -> int:
        """S-1. Médicos que se presentan a trabajar."""
        return round(self.medicos * self.presentismo)


@dataclass(frozen=True)
class Intervencion:
    """
    Palanca de política que se aplica a una corrida. `None` = escenario sin
    intervención. La consumen `simulacion.correr`, `outputs.tabla_recursos_minimos`
    y `montecarlo.sensibilidad_recursos`.
    """
    camas_extra: dict = field(default_factory=dict)          # nombre_instalacion -> +camas
    uci_extra: dict = field(default_factory=dict)            # nombre_instalacion -> +camas UCI
    quirofanos_extra: dict = field(default_factory=dict)     # nombre_instalacion -> +quirófanos
    medicos_extra: int = 0                                    # médicos adicionales a repartir
    sangre_extra: float = 0.0                                 # unidades añadidas al stock inicial
    desde_bloque: int = 1                                     # bloque en que entra en vigor (1..12)
    derivar_leves_aparte: bool = False                        # triaje de leves fuera del hospital


def cargar() -> ParamsSistema:
    return ParamsSistema()


def volcar_json(path: Path = JSON_OUT) -> Path:
    p = cargar()
    d = {
        "instalaciones": [asdict(i) for i in p.instalaciones],
        "heridos_t0": [asdict(z) | {"total_atencion": z.total_atencion,
                                    "proporciones": z.proporciones()} for z in p.heridos_t0],
        "suministros": [asdict(s) | {"consumo_hora_base": s.consumo_hora_base}
                        for s in p.suministros],
        "t_atencion_h": p.t_atencion_h,
        "lambda_llegada_h": {f"{a}-{b}": v for (a, b), v in p.lambda_llegada_h.items()},
        "personal": {"medicos": p.medicos, "enfermeras": p.enfermeras,
                     "medicos_efectivos": p.medicos_efectivos(),
                     "reparto": reparto_personal(p.instalaciones, p.medicos_efectivos()),
                     "pac_por_medico_normal": p.pac_por_medico_normal,
                     "pac_por_medico_emergencia": p.pac_por_medico_emergencia},
        "distancias_km": p.dist_zonas_km,
        "tau_traslado_h": matriz_tau(p.dist_zonas_km),
        "orden_derivacion": {f"{z}|{g}": list(v) for (z, g), v in p.orden_derivacion().items()},
        "consumo_por_procedimiento": p.consumo_por_procedimiento,
        "integrador": {"dt_h": p.dt_sd_h, "metodo": p.metodo_integracion},
        "horizonte_h": HORIZONTE_H, "bloque_h": BLOQUE_H, "n_bloques": N_BLOQUES,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, indent=2, ensure_ascii=False))
    return path


if __name__ == "__main__":
    print("Escrito:", volcar_json())
