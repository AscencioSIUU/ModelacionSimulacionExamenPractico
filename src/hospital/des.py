"""
T1 (P2) — Motor de eventos discretos (DES).

Calendario de eventos por heap binario (`heapq`): cada evento es una tupla
(tiempo, orden, Evento) — `orden` es un contador monotónico que desempata
eventos con el mismo `tiempo` y evita comparar `Evento` entre sí (no define
`__lt__`). Cancelar un evento (p. ej. una muerte en cola cuando el paciente
ya entró a atención) sólo marca su id como cancelado; `Calendario.pop()` lo
descarta perezosamente al sacarlo del heap — evitar borrar del heap a mitad
de camino, que rompería el invariante de heap.

`simulacion.py` (P2) es quien arma el calendario completo y despacha por
`evento.tipo`; este módulo sólo provee los tipos y funciones puras/mecánicas
del motor: llegadas (thinning), reglas de recursos por gravedad, colas con
prioridad y tiempos de servicio.
"""
from __future__ import annotations

import heapq
import itertools
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

from .params import GRAVEDADES, HORIZONTE_H, LAMBDA_MAX, ParamsSistema, ZonaHeridos

# Prioridad de atención: grave > moderado > leve. FIFO dentro de cada nivel.
# El orden de este dict IS el orden de precedencia (Python preserva inserción).
PRIORIDAD: dict[str, int] = {"grave": 0, "moderado": 1, "leve": 2}


# --- calendario de eventos ------------------------------------------------------
@dataclass
class Evento:
    tiempo: float
    tipo: str
    id: int
    datos: dict = field(default_factory=dict)


class Calendario:
    """Cola de prioridad mínima por `tiempo`, con cancelación perezosa por id."""

    def __init__(self) -> None:
        self._heap: list[tuple[float, int, Evento]] = []
        self._contador = itertools.count()
        self._cancelados: set[int] = set()

    def agendar(self, tiempo: float, tipo: str, **datos) -> int:
        id_evento = next(self._contador)
        evento = Evento(tiempo=tiempo, tipo=tipo, id=id_evento, datos=datos)
        heapq.heappush(self._heap, (tiempo, id_evento, evento))
        return id_evento

    def cancelar(self, id_evento: int | None) -> None:
        if id_evento is not None:
            self._cancelados.add(id_evento)

    def pop(self) -> Evento | None:
        """Saca y devuelve el próximo evento no cancelado, o `None` si no queda."""
        while self._heap:
            _, id_evento, evento = heapq.heappop(self._heap)
            if id_evento in self._cancelados:
                continue
            return evento
        return None

    def __len__(self) -> int:
        return len(self._heap)


# --- pacientes y recursos --------------------------------------------------------
@dataclass
class Paciente:
    id: int
    zona: str
    gravedad: str
    instalacion: str = ""
    t_llegada: float = 0.0
    t_inicio_atencion: float | None = None
    t_fin_atencion: float | None = None
    # en_cola -> en_atencion -> {atendido, muerte_clinica} | en_cola -> muerte_evitable
    estado: str = "en_cola"
    id_evento_muerte_cola: int | None = None


@dataclass
class EstadoInstalacion:
    nombre: str
    camas_capacidad: int
    uci_capacidad: int
    quirofanos_capacidad: int
    camas_ocupadas: int = 0
    uci_ocupadas: int = 0
    quirofanos_ocupados: int = 0
    colas: dict[str, deque] = field(default_factory=lambda: {g: deque() for g in GRAVEDADES})
    # pacientes en atención ahora mismo, por gravedad — insumo para el acople DES->SD
    en_atencion: dict[str, int] = field(default_factory=lambda: {g: 0 for g in GRAVEDADES})


@dataclass
class Recursos:
    """
    Estado mutable de capacidad/ocupación de todas las instalaciones + el pool
    de médicos (compartido a nivel ciudad, según el dato agregado del Excel).
    """
    instalaciones: dict[str, EstadoInstalacion]
    medicos_base: int                       # médicos efectivos (presentismo + extra)
    medicos_capacidad: int                  # medicos_base * pac_por_medico vigente (normal/emergencia)
    medicos_ocupados: int = 0
    modo_emergencia: bool = False
    derivar_leves: bool = False             # Intervencion.derivar_leves_aparte
    sangre_bloqueada: bool = False          # acople SD->DES: stock de sangre en 0
    bloqueo_horas: dict[tuple[str, str], float] = field(default_factory=dict)
    _bloqueo_desde: dict[tuple[str, str], float | None] = field(default_factory=dict)

    def marcar_bloqueo(self, t: float, instalacion: str, recurso: str, bloqueado: bool) -> None:
        """Acumula horas con cola y `recurso` agotado en `(instalacion, recurso)`."""
        clave = (instalacion, recurso)
        desde = self._bloqueo_desde.get(clave)
        if bloqueado and desde is None:
            self._bloqueo_desde[clave] = t
        elif not bloqueado and desde is not None:
            self.bloqueo_horas[clave] = self.bloqueo_horas.get(clave, 0.0) + (t - desde)
            self._bloqueo_desde[clave] = None

    def cerrar_bloqueos(self, t_final: float) -> None:
        """Cierra intervalos de bloqueo que seguían abiertos al terminar la corrida."""
        for clave, desde in list(self._bloqueo_desde.items()):
            if desde is not None:
                self.bloqueo_horas[clave] = self.bloqueo_horas.get(clave, 0.0) + (t_final - desde)
                self._bloqueo_desde[clave] = None


def crear_recursos(
    instalaciones: Iterable,
    medicos_base: int,
    pac_por_medico: int,
    *,
    derivar_leves: bool = False,
) -> Recursos:
    estados = {
        i.nombre: EstadoInstalacion(
            nombre=i.nombre,
            camas_capacidad=i.camas,
            uci_capacidad=i.uci,
            quirofanos_capacidad=i.quirofanos,
        )
        for i in instalaciones
    }
    return Recursos(
        instalaciones=estados,
        medicos_base=medicos_base,
        medicos_capacidad=medicos_base * pac_por_medico,
        derivar_leves=derivar_leves,
    )


# --- reglas de recursos por gravedad ---------------------------------------------
# grave -> UCI + médico (+ quirófano si hay); moderado -> cama general + médico;
# leve -> sólo médico (o nada, si `derivar_leves_aparte` los saca a triaje aparte).
def puede_atender(recursos: Recursos, instalacion: str, gravedad: str) -> bool:
    if gravedad == "leve" and recursos.derivar_leves:
        return True
    inst = recursos.instalaciones[instalacion]
    medico_libre = recursos.medicos_ocupados < recursos.medicos_capacidad
    if gravedad == "grave":
        return medico_libre and inst.uci_ocupadas < inst.uci_capacidad and not recursos.sangre_bloqueada
    if gravedad == "moderado":
        return medico_libre and inst.camas_ocupadas < inst.camas_capacidad
    return medico_libre  # leve sin derivar


def tomar_recursos(recursos: Recursos, instalacion: str, gravedad: str) -> None:
    inst = recursos.instalaciones[instalacion]
    inst.en_atencion[gravedad] += 1
    if gravedad == "leve" and recursos.derivar_leves:
        return
    recursos.medicos_ocupados += 1
    if gravedad == "grave":
        inst.uci_ocupadas += 1
        if inst.quirofanos_ocupados < inst.quirofanos_capacidad:
            inst.quirofanos_ocupados += 1
    elif gravedad == "moderado":
        inst.camas_ocupadas += 1


def liberar_recursos(recursos: Recursos, instalacion: str, gravedad: str) -> None:
    inst = recursos.instalaciones[instalacion]
    inst.en_atencion[gravedad] = max(0, inst.en_atencion[gravedad] - 1)
    if gravedad == "leve" and recursos.derivar_leves:
        return
    recursos.medicos_ocupados = max(0, recursos.medicos_ocupados - 1)
    if gravedad == "grave":
        inst.uci_ocupadas = max(0, inst.uci_ocupadas - 1)
        # el quirófano no se rastrea por paciente individual (recurso secundario,
        # ver docstring de clase): al liberar un grave, se libera un slot agregado.
        if inst.quirofanos_ocupados > 0:
            inst.quirofanos_ocupados -= 1
    elif gravedad == "moderado":
        inst.camas_ocupadas = max(0, inst.camas_ocupadas - 1)


def admitir_pendientes(recursos: Recursos, instalacion: str) -> list[Paciente]:
    """
    Recorre las colas de `instalacion` en orden de prioridad (grave, moderado,
    leve) y saca de cola + reserva recursos para todos los pacientes que ya
    caben con la capacidad actual. Quien llama debe iniciarles la atención
    (agendar FIN_ATENCION) y cancelar su evento MUERTE_EN_COLA si tenían uno.
    """
    admitidos: list[Paciente] = []
    inst = recursos.instalaciones[instalacion]
    for g in PRIORIDAD:
        cola = inst.colas[g]
        while cola and puede_atender(recursos, instalacion, g):
            paciente = cola.popleft()
            tomar_recursos(recursos, instalacion, g)
            admitidos.append(paciente)
    return admitidos


def encolar(recursos: Recursos, paciente: Paciente) -> None:
    recursos.instalaciones[paciente.instalacion].colas[paciente.gravedad].append(paciente)


# --- llegadas: Poisson no homogéneo por thinning (Lewis–Shedler) -----------------
def llegadas_thinning(rng, params: ParamsSistema, zona: str, n_pool: int) -> list[float]:
    """
    Genera los tiempos (horas, en [0, HORIZONTE_H)) en que los `n_pool` heridos
    de una zona llegan a pedir atención, vía un proceso de Poisson no homogéneo
    simulado por thinning (Lewis–Shedler, fórmula de clase):

      1. lambda(t) es la tasa PER CÁPITA (fracción/hora) del pool restante en el
         tramo horario de `t` (`params.lambda_llegada_h`); la tasa total del
         proceso en el instante t es lambda(t) * n_restante(t).
      2. Se propone el próximo candidato con un salto Exp(LAMBDA_MAX * n_restante)
         (LAMBDA_MAX = cota superior de lambda(t) sobre todos los tramos).
      3. Se acepta el candidato con probabilidad lambda(t) / LAMBDA_MAX; si se
         acepta, ese herido sale del pool (n_restante -= 1) y su tiempo de
         llegada es el candidato aceptado.

    El pool se agota (o el horizonte termina) antes de que lleguen todos: los
    que no llegan en 72 h quedan fuera del sistema (no se cuentan como
    `generados`, ya que nunca piden atención).
    """
    def tasa(t: float) -> float:
        for (a, b), v in params.lambda_llegada_h.items():
            if a <= t < b:
                return v
        return 0.0

    tiempos: list[float] = []
    t = 0.0
    n_restante = n_pool
    while t < HORIZONTE_H and n_restante > 0:
        lam_total = LAMBDA_MAX * n_restante
        if lam_total <= 0:
            break
        t += rng.exponential(1.0 / lam_total)
        if t >= HORIZONTE_H:
            break
        if rng.uniform() <= tasa(t) / LAMBDA_MAX:
            tiempos.append(t)
            n_restante -= 1
    return tiempos


def asignar_gravedad(rng, zona_heridos: ZonaHeridos) -> str:
    """Severidad de un herido nuevo: multinomial con `ZonaHeridos.proporciones()`."""
    props = zona_heridos.proporciones()
    return str(rng.choice(GRAVEDADES, p=[props[g] for g in GRAVEDADES]))


def tiempo_servicio(
    rng, params: ParamsSistema, gravedad: str,
    factor_fatiga: float = 1.0, factor_escasez: float = 1.0,
) -> float:
    """Exp(1/T_ATENCION_H[g]) escalado por fatiga del personal y escasez de insumos."""
    media = params.t_atencion_h[gravedad] * factor_fatiga * factor_escasez
    return float(rng.exponential(media))


def _ocupacion_global(recursos: Recursos) -> float:
    ocupadas = capacidad = 0
    for inst in recursos.instalaciones.values():
        ocupadas += inst.camas_ocupadas + inst.uci_ocupadas
        capacidad += inst.camas_capacidad + inst.uci_capacidad
    return ocupadas / capacidad if capacidad else 0.0


def actualizar_modo_emergencia(recursos: Recursos, params: ParamsSistema) -> None:
    """Pac/médico 4->8 al cruzar `params.umbral_emergencia` de ocupación camas+UCI."""
    recursos.modo_emergencia = _ocupacion_global(recursos) >= params.umbral_emergencia
    ratio = (params.pac_por_medico_emergencia if recursos.modo_emergencia
             else params.pac_por_medico_normal)
    recursos.medicos_capacidad = recursos.medicos_base * ratio
