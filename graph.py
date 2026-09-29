"""
graph.py — Construcción del StateGraph (topología jerárquica / patrón Supervisor).

    START → supervisor ─┬─→ investigador ─→ validador ─→ supervisor
                        ├─→ analista ─────→ validador ─→ supervisor
                        ├─→ sintesis ─────→ END
                        └─→ escalar_humano → END

Los especialistas nunca hablan entre sí: todo pasa por el supervisor (y por la puerta de
calidad del validador). Los componentes LLM se inyectan, así los tests pueden reemplazarlos
por dobles determinísticos sin API keys.
"""

from __future__ import annotations

from functools import wraps

from langgraph.graph import END, START, StateGraph

from agents.analyst_agent import crear_nodo_analista
from agents.research_agent import crear_nodo_investigador
from agents.supervisor import crear_nodo_supervisor
from agents.synthesizer import crear_nodo_sintesis, nodo_escalar_humano
from state import MAX_PASOS, AgentState, DecisionSupervisor, DestinoSupervisor, verificar_escritura
from validation import nodo_validador

# Cada paso del supervisor dispara hasta 3 nodos (supervisor → especialista → validador).
# El corte "de negocio" es MAX_PASOS; este límite de LangGraph es solo la red de seguridad.
RECURSION_LIMIT = 3 * MAX_PASOS + 5


def _aislado(nombre: str, nodo):
    """Envuelve un nodo para que solo pueda escribir en su parcela del estado."""

    @wraps(nodo)
    async def envoltura(state: AgentState) -> dict:
        return verificar_escritura(nombre, await nodo(state))

    return envoltura


def enrutar_supervisor(state: AgentState) -> DestinoSupervisor:
    """Arista condicional: lee la decisión de ruteo que dejó el supervisor en el estado."""
    return state["next_agent"]


def construir_grafo(investigador=None, analista=None, router=None, redactor=None):
    """Compila el grafo. Si no se pasan componentes, crea los reales con Gemini."""
    if None in (investigador, analista, router, redactor):
        from agents.analyst_agent import crear_agente_analista
        from agents.common import crear_llm
        from agents.research_agent import crear_agente_investigador

        llm = crear_llm()
        investigador = investigador or crear_agente_investigador(llm)
        analista = analista or crear_agente_analista(llm)
        router = router or llm.with_structured_output(DecisionSupervisor)
        redactor = redactor or llm

    nodos = {
        "supervisor": crear_nodo_supervisor(router),
        "investigador": crear_nodo_investigador(investigador),
        "analista": crear_nodo_analista(analista),
        "validador": nodo_validador,
        "sintesis": crear_nodo_sintesis(redactor),
        "escalar_humano": nodo_escalar_humano,
    }

    grafo = StateGraph(AgentState)
    for nombre, nodo in nodos.items():
        grafo.add_node(nombre, _aislado(nombre, nodo))

    grafo.add_edge(START, "supervisor")
    grafo.add_conditional_edges(
        "supervisor",
        enrutar_supervisor,
        {d: d for d in ("investigador", "analista", "sintesis", "escalar_humano")},
    )
    grafo.add_edge("investigador", "validador")
    grafo.add_edge("analista", "validador")
    grafo.add_edge("validador", "supervisor")
    grafo.add_edge("sintesis", END)
    grafo.add_edge("escalar_humano", END)

    return grafo.compile()
