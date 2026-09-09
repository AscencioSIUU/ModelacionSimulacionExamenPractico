"""
T1 — Datos de partida del Grupo 2.

Los valores se transcriben del Excel `data/raw/Grupo2_SistemaHospitalario.xlsx`
(hoja `Datos_Grupo2`). El Excel es una plantilla hecha a mano con filas de
encabezado mezcladas con datos; transcribir a constantes tipadas es más robusto
y auditable que parsearlo. Si el Excel cambiara, actualizar aquí.

Convención de unidades: TODO se normaliza a **base horaria**. Las tasas del Excel
que vienen "por día" se dividen entre 24 (`tasa_hora = tasa_dia / 24`).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path

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


# --- 1. Infraestructura hospitalaria (Excel 1) ---------------------------------
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

# --- 3. Tasas y tiempos (Excel 3) -------------------------------------------
# Tiempo medio de atención por gravedad, en horas.
T_ATENCION_H = {"leve": 0.5, "moderado": 3.0, "grave": 8.0}

# Tasa de llegada al hospital como fracción/hora del pool restante, por tramo.
LAMBDA_LLEGADA_H = {
    (0, 6): 0.18,
    (6, 24): 0.07,
    (24, HORIZONTE_H): 0.02,
}
LAMBDA_MAX = max(LAMBDA_LLEGADA_H.values())  # para thinning (Lewis–Shedler)

MEDICOS = 87
ENFERMERAS = 142
PAC_POR_MEDICO_NORMAL = 4
PAC_POR_MEDICO_EMERGENCIA = 8
# Umbral de ocupación que dispara el modo emergencia (feedback DES).
UMBRAL_EMERGENCIA = 0.85
PRESENTISMO_PERSONAL = 0.85   # fracción del personal que efectivamente se presenta (supuesto)

# Tolerancia en cola antes de muerte evitable, por gravedad (horas). Supuesto.
T_TOLERANCIA_COLA_H = {"leve": 120.0, "moderado": 24.0, "grave": 4.0}
# Mortalidad clínica inevitable al terminar la atención, por gravedad. Supuesto.
P_MORTALIDAD_CLINICA = {"leve": 0.001, "moderado": 0.02, "grave": 0.15}

# --- 4. Suministros médicos (Excel 4) --------------------------------------
SUMINISTROS: list[Suministro] = [
    Suministro("sangre", "unidades", 480, 120),
    Suministro("solucion_salina", "litros", 2800, 600),
    Suministro("analgesicos", "dosis", 1200, 280),
    Suministro("antibioticos", "dosis", 3500, 450),
    Suministro("material_quirurgico", "kits", 85, 18),
    Suministro("combustible_generadores", "galones", 1800, 320),
]

# Peso relativo de consumo por gravedad para repartir `consumo_dia_base`.
# Supuesto: un herido grave consume ~6x lo de un leve; moderado ~3x.
PESO_CONSUMO_GRAVEDAD = {"leve": 1.0, "moderado": 3.0, "grave": 6.0}

# Fatiga del personal (sustrato SD).
FATIGA_ALFA = 0.08   # desgaste por unidad de carga relativa, por hora
FATIGA_BETA = 0.02   # recuperación hacia energía plena, por hora

EPS_STOCK = 1e-6     # término saturante: el outflow se detiene solo en 0


def matriz_ruteo() -> dict[str, dict[str, float]]:
    """
    R[zona_origen][nombre_instalacion] = fracción de heridos de esa zona que se
    dirige a esa instalación. Cada fila suma 1.

    Supuestos (documentar en el reporte):
      - Cada zona con instalación operativa envía la mayoría a la suya, con
        spillover a la instalación grande más cercana.
      - Z3 está CERRADA: sus heridos se reparten a Z1 (centro, la mayor) y Z4.
      - Z5 tiene capacidad mínima (20 camas): buena parte se deriva a Z1/Z4.
    """
    op = {i.nombre for i in INSTALACIONES if i.operativa}
    R = {
        "Z1": {"Hospital General UVG": 0.80, "Hospital Regional Este": 0.20},
        "Z2": {"Clínica Z2": 0.55, "Hospital General UVG": 0.45},
        "Z3": {"Hospital General UVG": 0.55, "Hospital Regional Este": 0.45},
        "Z4": {"Hospital Regional Este": 0.85, "Hospital General UVG": 0.15},
        "Z5": {"Puesto Salud Z5": 0.15, "Hospital General UVG": 0.50,
               "Hospital Regional Este": 0.35},
    }
    for z, fila in R.items():
        assert abs(sum(fila.values()) - 1.0) < 1e-9, f"fila {z} no suma 1"
        assert set(fila) <= op, f"fila {z} rutea a instalación no operativa"
    return R


def coef_consumo() -> dict[str, dict[str, float]]:
    """
    c[suministro][gravedad] en unidades por paciente-hora de atención.

    Reparte `consumo_dia_base` de cada suministro entre gravedades según
    PESO_CONSUMO_GRAVEDAD, ponderado por la carga esperada de pacientes-hora de
    cada gravedad en el escenario base, y lo pasa a base horaria.
    """
    # pacientes-hora esperados por gravedad en el escenario base (T0, toda la ciudad)
    ph = {}
    for g in GRAVEDADES:
        n = sum(getattr(z, g) for z in HERIDOS_T0)
        ph[g] = n * T_ATENCION_H[g]
    peso_tot = sum(PESO_CONSUMO_GRAVEDAD[g] * ph[g] for g in GRAVEDADES)

    c: dict[str, dict[str, float]] = {}
    for s in SUMINISTROS:
        c[s.nombre] = {}
        for g in GRAVEDADES:
            frac = PESO_CONSUMO_GRAVEDAD[g] * ph[g] / peso_tot
            # consumo/hora total del suministro atribuido a esta gravedad,
            # dividido entre sus pacientes-hora -> por paciente-hora
            c[s.nombre][g] = (s.consumo_hora_base * frac) / ph[g]
    return c


@dataclass
class ParamsSistema:
    """Paquete inmutable que consumen des.py, sd.py y montecarlo.py."""
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
    ruteo: dict = field(default_factory=matriz_ruteo)
    coef_consumo: dict = field(default_factory=coef_consumo)
    fatiga_alfa: float = FATIGA_ALFA
    fatiga_beta: float = FATIGA_BETA

    def instalacion(self, nombre: str) -> Instalacion:
        return next(i for i in self.instalaciones if i.nombre == nombre)


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
                     "pac_por_medico_normal": p.pac_por_medico_normal,
                     "pac_por_medico_emergencia": p.pac_por_medico_emergencia},
        "ruteo": p.ruteo,
        "coef_consumo_por_paciente_hora": p.coef_consumo,
        "horizonte_h": HORIZONTE_H, "bloque_h": BLOQUE_H, "n_bloques": N_BLOQUES,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, indent=2, ensure_ascii=False))
    return path


if __name__ == "__main__":
    print("Escrito:", volcar_json())
