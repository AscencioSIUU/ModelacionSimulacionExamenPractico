"""
Motor de eventos discretos (DES).

Calendario de eventos por heap binario. Cada evento es una tupla
(tiempo, orden, Evento), donde: `orden` es un contador monotónico que desempata
eventos con el mismo `tiempo` y evita comparar `Evento` entre sí.

Este módulo provee los tipos de eventos y las funciones mecánicas del motor:

- R1: llegadas por thinning y severidad multinomial.
- R2 (`elegir_instalacion`): la operativa más cercana con capacidad libre.
- R3 (`hay_servible_libre`): todos los recursos de la gravedad, a la vez.
- R4 (`admitir_pendientes`): prioridad grave > moderado > leve, FIFO dentro
  de cada nivel.
- R5 (`intentar_iniciar`): verifica el nivel completo de insumos antes de
  iniciar; si no alcanza, libera el recurso servible y el paciente vuelve al
  frente de su cola conservando su espera acumulada.
- R7 (`tiempo_servicio`): escalado por el factor de fatiga que entrega `sd`.
"""

from __future__ import annotations

import heapq
import itertools
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence

from . import params as P
from .params import (GRAVEDADES, HORIZONTE_H, LAMBDA_MAX, TOL_STOCK, Instalacion,
                     ParamsSistema, ZonaHeridos)

# Prioridad de atención: grave > moderado > leve. FIFO dentro de cada nivel.
# El orden de este dict ES el orden de precedencia.
PRIORIDAD: dict[str, int] = {"grave": 0, "moderado": 1, "leve": 2}


class Intento(str, Enum):
    """Desenlace de un intento de iniciar la atención de un paciente."""

    INICIADO = "iniciado"
    SIN_RECURSO = "sin_recurso"     # no hay cama/UCI/quirófano/médico libre
    SIN_INSUMO = "sin_insumo"       # hay recurso, pero el stock no cubre el requerimiento


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
@dataclass(eq=False)   # identidad, no valor: `colas[g].remove(p)` compara por objeto
class Paciente:
    id: int
    zona: str
    gravedad: str
    instalacion: str = ""
    t_llegada: float = 0.0                  # entrada al sistema (evento LLEGADA)
    t_arribo: float | None = None           # llegada física a la instalación (LLEGADA + tau)
    t_inicio_atencion: float | None = None
    t_fin_atencion: float | None = None
    t_salida: float | None = None           # fin de atención o muerte
    # en_traslado -> en_cola -> en_atencion -> {atendido, muerte_clinica}
    #             └-> muerte_evitable
    estado: str = "en_traslado"
    id_evento_muerte_cola: int | None = None
    intentos_insumo_fallidos: int = 0       # diagnóstico de R5


@dataclass
class EstadoInstalacion:
    nombre: str
    zona: str
    camas_capacidad: int
    uci_capacidad: int
    quirofanos_capacidad: int
    medicos_asignados: int                  # S-1, reparto proporcional a camas
    medicos_capacidad: int                  # medicos_asignados * pac_por_medico vigente
    camas_ocupadas: int = 0
    uci_ocupadas: int = 0
    quirofanos_ocupados: int = 0
    medicos_ocupados: int = 0
    modo_emergencia: bool = False
    colas: dict[str, deque] = field(default_factory=lambda: {g: deque() for g in GRAVEDADES})
    # pacientes en atención ahora mismo, por gravedad — insumo del acople DES->SD
    en_atencion: dict[str, int] = field(default_factory=lambda: {g: 0 for g in GRAVEDADES})


@dataclass
class ResultadoAdmision:
    """Lo que devolvió una pasada de `admitir_pendientes` sobre una instalación."""

    admitidos: list[Paciente]
    faltantes: dict[str, tuple[str, ...]]   # gravedad -> suministros que bloquearon


@dataclass
class Recursos:
    """Estado mutable de capacidad, ocupación, colas e insumos de la ciudad."""

    instalaciones: dict[str, EstadoInstalacion]
    orden_derivacion: dict[tuple[str, str], tuple[str, ...]]   # R2, precomputado
    nivel_insumos: dict[str, float]        # copia viva de los stocks entre ticks SD
    consumo_pendiente: dict[str, float]    # buffer R5 que aplica el próximo TICK_SD
    derivar_leves: bool = False
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
    params: ParamsSistema,
    *,
    instalaciones: Sequence[Instalacion] | None = None,
    medicos_extra: int = 0,
    derivar_leves: bool = False,
    nivel_insumos: dict[str, float] | None = None,
) -> Recursos:
    """
    Arma el estado de recursos. El personal se reparte por instalación (S-1), no
    como un pool de ciudad.
    """
    operativas = list(instalaciones) if instalaciones is not None else [
        i for i in params.instalaciones if i.operativa]
    medicos_totales = params.medicos_efectivos() + medicos_extra
    reparto = P.reparto_personal(operativas, medicos_totales)

    estados = {
        i.nombre: EstadoInstalacion(
            nombre=i.nombre,
            zona=i.zona,
            camas_capacidad=i.camas,
            uci_capacidad=i.uci,
            quirofanos_capacidad=i.quirofanos,
            medicos_asignados=reparto.get(i.nombre, 0),
            medicos_capacidad=reparto.get(i.nombre, 0) * params.pac_por_medico_normal,
        )
        for i in operativas
    }
    niveles = (dict(nivel_insumos) if nivel_insumos is not None
               else {s.nombre: float(s.stock_inicial) for s in params.suministros})
    return Recursos(
        instalaciones=estados,
        orden_derivacion=P.orden_derivacion(operativas, params.dist_zonas_km),
        nivel_insumos=niveles,
        consumo_pendiente={k: 0.0 for k in niveles},
        derivar_leves=derivar_leves,
    )


# --- capacidad y ocupación por recurso -------------------------------------------
def capacidad(inst: EstadoInstalacion, recurso: str) -> int:
    if recurso == "cama_general":
        return inst.camas_capacidad
    if recurso == "cama_uci":
        return inst.uci_capacidad
    if recurso == "quirofano":
        return inst.quirofanos_capacidad
    if recurso == "medico":
        return inst.medicos_capacidad
    raise KeyError(f"recurso desconocido: {recurso}")


def ocupados(inst: EstadoInstalacion, recurso: str) -> int:
    if recurso == "cama_general":
        return inst.camas_ocupadas
    if recurso == "cama_uci":
        return inst.uci_ocupadas
    if recurso == "quirofano":
        return inst.quirofanos_ocupados
    if recurso == "medico":
        return inst.medicos_ocupados
    raise KeyError(f"recurso desconocido: {recurso}")


def _sumar(inst: EstadoInstalacion, recurso: str, delta: int) -> None:
    if recurso == "cama_general":
        inst.camas_ocupadas += delta
    elif recurso == "cama_uci":
        inst.uci_ocupadas += delta
    elif recurso == "quirofano":
        inst.quirofanos_ocupados += delta
    elif recurso == "medico":
        inst.medicos_ocupados += delta
    else:
        raise KeyError(f"recurso desconocido: {recurso}")


# --- R3: reglas de recursos por gravedad -----------------------------------------
def _sin_recursos(recursos: Recursos, gravedad: str) -> bool:
    """Los leves derivados a triaje aparte no compiten por recursos del hospital."""
    return gravedad == "leve" and recursos.derivar_leves


def hay_servible_libre(recursos: Recursos, instalacion: str, gravedad: str,
                       params: ParamsSistema) -> bool:
    """R3. Todos los recursos que exige la gravedad, libres simultáneamente."""
    if _sin_recursos(recursos, gravedad):
        return True
    inst = recursos.instalaciones[instalacion]
    return all(ocupados(inst, r) < capacidad(inst, r)
               for r in P.RECURSOS_POR_GRAVEDAD[gravedad])


def tomar_recursos(recursos: Recursos, instalacion: str, gravedad: str,
                   params: ParamsSistema) -> None:
    inst = recursos.instalaciones[instalacion]
    inst.en_atencion[gravedad] += 1
    if _sin_recursos(recursos, gravedad):
        return
    for r in P.RECURSOS_POR_GRAVEDAD[gravedad]:
        _sumar(inst, r, +1)


def liberar_recursos(recursos: Recursos, instalacion: str, gravedad: str,
                     params: ParamsSistema) -> None:
    inst = recursos.instalaciones[instalacion]
    assert inst.en_atencion[gravedad] > 0, f"liberar sin paciente en atención: {gravedad}"
    inst.en_atencion[gravedad] -= 1
    if _sin_recursos(recursos, gravedad):
        return
    for r in P.RECURSOS_POR_GRAVEDAD[gravedad]:
        # Un contador que iba a bajar de 0 es un bug, no algo que redondear.
        assert ocupados(inst, r) > 0, f"liberar {r} sin ocupación en {instalacion}"
        _sumar(inst, r, -1)


# --- R5: insumos de cantidad fija -------------------------------------------------
def hay_insumos(recursos: Recursos, gravedad: str, params: ParamsSistema) -> bool:
    """Verificación explícita del nivel: el stock debe cubrir el requerimiento completo."""
    req = params.consumo_por_procedimiento.get(gravedad, {})
    return all(recursos.nivel_insumos[k] >= q - TOL_STOCK for k, q in req.items())


def faltantes_insumo(recursos: Recursos, gravedad: str, params: ParamsSistema) -> tuple[str, ...]:
    req = params.consumo_por_procedimiento.get(gravedad, {})
    return tuple(k for k, q in req.items() if recursos.nivel_insumos[k] < q - TOL_STOCK)


def consumir_insumos(recursos: Recursos, gravedad: str, params: ParamsSistema) -> None:
    """Descuenta la cantidad fija del procedimiento y la deja en el buffer del SD."""
    for k, q in params.consumo_por_procedimiento.get(gravedad, {}).items():
        recursos.nivel_insumos[k] -= q          # cantidad fija, nunca proporcional al nivel
        recursos.consumo_pendiente[k] += q
        assert recursos.nivel_insumos[k] >= -TOL_STOCK, (
            f"R5 violada: stock negativo {k}={recursos.nivel_insumos[k]:.6g} "
            f"al consumir {q} para gravedad={gravedad}")


def intentar_iniciar(recursos: Recursos, instalacion: str, paciente: Paciente,
                     params: ParamsSistema) -> Intento:
    """
    R5. Reserva el recurso servible, verifica el stock completo y solo entonces
    descuenta. Si el stock no alcanza, deshace la reserva: el recurso queda
    libre y el paciente sigue en cola con su espera acumulada intacta.
    """
    g = paciente.gravedad
    if not hay_servible_libre(recursos, instalacion, g, params):
        return Intento.SIN_RECURSO

    tomar_recursos(recursos, instalacion, g, params)

    if not hay_insumos(recursos, g, params):
        liberar_recursos(recursos, instalacion, g, params)   # rollback completo
        paciente.intentos_insumo_fallidos += 1
        return Intento.SIN_INSUMO

    consumir_insumos(recursos, g, params)
    return Intento.INICIADO


def puede_atender(recursos: Recursos, instalacion: str, gravedad: str,
                  params: ParamsSistema) -> bool:
    """R3 y R5 juntos: hay recurso servible libre y el stock cubre el procedimiento."""
    return (hay_servible_libre(recursos, instalacion, gravedad, params)
            and hay_insumos(recursos, gravedad, params))


def encolar(recursos: Recursos, paciente: Paciente, *, al_frente: bool = False) -> None:
    cola = recursos.instalaciones[paciente.instalacion].colas[paciente.gravedad]
    cola.appendleft(paciente) if al_frente else cola.append(paciente)


def admitir_pendientes(recursos: Recursos, instalacion: str,
                       params: ParamsSistema) -> ResultadoAdmision:
    """
    R4 + R3 + R5. Recorre las colas en orden de prioridad e inicia la atención de
    todos los que caben. Tres guardas evitan que el desabasto degenere en bucle:

    1. Si el stock ya no cubre esa gravedad, no se saca a nadie de la cola.
    2. El paciente que falla vuelve al frente de su cola: conserva posición
       FIFO, `t_llegada` y su evento MUERTE_EN_COLA.
    3. Un solo intento fallido por gravedad y por llamada (`break`).

    Quien llama debe iniciar la atención de `admitidos` y cancelarles el
    MUERTE_EN_COLA.
    """
    admitidos: list[Paciente] = []
    faltantes: dict[str, tuple[str, ...]] = {}
    inst = recursos.instalaciones[instalacion]

    for g in PRIORIDAD:
        cola = inst.colas[g]
        if cola and not hay_insumos(recursos, g, params):    # guarda 1
            faltantes[g] = faltantes_insumo(recursos, g, params)
            continue
        while cola and hay_servible_libre(recursos, instalacion, g, params):
            paciente = cola.popleft()                        # FIFO dentro del nivel
            resultado = intentar_iniciar(recursos, instalacion, paciente, params)
            if resultado is Intento.INICIADO:
                admitidos.append(paciente)
                continue
            cola.appendleft(paciente)                        # guarda 2
            if resultado is Intento.SIN_INSUMO:
                faltantes[g] = faltantes_insumo(recursos, g, params)
            break                                            # guarda 3

    return ResultadoAdmision(admitidos, faltantes)


# --- R2: derivación por cercanía --------------------------------------------------
def capacidad_critica(recursos: Recursos, instalacion: str, gravedad: str,
                      params: ParamsSistema) -> int:
    """Capacidad del recurso más escaso que exige la gravedad en esa instalación."""
    inst = recursos.instalaciones[instalacion]
    return min(capacidad(inst, r) for r in P.RECURSOS_POR_GRAVEDAD[gravedad])


def elegir_instalacion(recursos: Recursos, zona: str, gravedad: str,
                       params: ParamsSistema) -> str:
    """
    R2. La instalación operativa más cercana con capacidad libre del recurso que
    el paciente necesita. Las de capacidad 0 ya están excluidas de la lista de
    candidatas, así que nunca se devuelve una instalación que no puede atender.

    Si ninguna tiene capacidad libre, el paciente se encola en la menos congestionada
    (cola de su gravedad relativa a la capacidad crítica), con la cercanía como desempate.
    """
    candidatas = recursos.orden_derivacion[(zona, gravedad)]
    for nombre in candidatas:
        if hay_servible_libre(recursos, nombre, gravedad, params):
            return nombre

    def congestion(indice: int) -> tuple[float, int]:
        nombre = candidatas[indice]
        cola = len(recursos.instalaciones[nombre].colas[gravedad])
        critica = max(1, capacidad_critica(recursos, nombre, gravedad, params))
        return (cola / critica, indice)   # el índice desempata por cercanía

    return candidatas[min(range(len(candidatas)), key=congestion)]


# --- S-1: régimen de emergencia por instalación -----------------------------------
def actualizar_modo_emergencia(recursos: Recursos, params: ParamsSistema) -> None:
    """Pac/médico 4->8 al cruzar el umbral de ocupación. La carga es local, no de ciudad."""
    for inst in recursos.instalaciones.values():
        cap = inst.camas_capacidad + inst.uci_capacidad
        ocup = (inst.camas_ocupadas + inst.uci_ocupadas) / cap if cap else 0.0
        inst.modo_emergencia = ocup >= params.umbral_emergencia
        ratio = (params.pac_por_medico_emergencia if inst.modo_emergencia
                 else params.pac_por_medico_normal)
        inst.medicos_capacidad = inst.medicos_asignados * ratio


# --- R1: llegadas por Poisson no homogéneo (thinning, Lewis–Shedler) --------------
def llegadas_thinning(rng, params: ParamsSistema, zona: str, n_pool: int) -> list[float]:
    """
    Genera los tiempos (horas, en [0, HORIZONTE_H)) en que los `n_pool` heridos
    de una zona piden atención, vía un proceso de Poisson no homogéneo simulado
    por thinning (Lewis–Shedler):

      1. lambda(t) es la tasa PER CÁPITA (fracción/hora) del pool restante en el
         tramo horario de `t`; la tasa total del proceso es lambda(t)*n_restante.
      2. Se propone el próximo candidato con un salto Exp(LAMBDA_MAX*n_restante),
         donde LAMBDA_MAX acota lambda(t) sobre todos los tramos.
      3. Se acepta con probabilidad lambda(t)/LAMBDA_MAX; al aceptar, ese herido
         sale del pool (n_restante -= 1).

    Los que no llegan dentro de las 72 h quedan fuera del sistema.
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


def tiempo_servicio(rng, params: ParamsSistema, gravedad: str,
                    factor_fatiga: float = 1.0) -> float:
    """S-6 y S-7. Exp(media = T_ATENCION_H[g]) escalada por la fatiga del personal."""
    media = params.t_atencion_h[gravedad] * factor_fatiga
    return float(rng.exponential(media))
