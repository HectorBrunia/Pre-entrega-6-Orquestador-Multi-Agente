"""
main.py — Ejecuta el orquestador y muestra, en vivo, quién decidió qué y por qué.

Uso:
    python main.py                                  # escenario por defecto (vacaciones)
    python main.py --escenario horas_extra
    python main.py --pregunta "¿Cuántos días de teletrabajo hay en 4 semanas?"
    python main.py --simular-fallo investigador     # demuestra el camino de fallo
    python main.py --diagrama                       # exporta docs/grafo.mmd (y .png si hay red)

Cada ejecución guarda el log de auditoría completo en logs/ejecucion_<fecha>.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import warnings
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from graph import RECURSION_LIMIT, construir_grafo
from state import estado_inicial

RAIZ = Path(__file__).resolve().parent

# Ruido interno de librerías (aviso de AFC de google-genai, coroutine sin await al cerrar,
# barra de progreso de tqdm en Jupyter): no afecta el resultado. A nivel de módulo para que
# también aplique al importar desde el notebook.
logging.getLogger("google_genai").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", message="coroutine .* was never awaited")
warnings.filterwarnings("ignore", message="IProgress not found")

ESCENARIOS = {
    "vacaciones": (
        "Un empleado va a cumplir 12 años en TechCorp. Tuvo los días de vacaciones que le correspondían "
        "según cada tramo de antigüedad: los primeros 5 años, los siguientes 5 y los últimos 2. "
        "¿Cuántos días de vacaciones acumuló en total en esos 12 años?"
    ),
    "horas_extra": (
        "Un empleado cuya hora normal vale 12 USD hizo este mes 10 horas extra en días hábiles y 6 horas "
        "extra en un feriado. Según la política de TechCorp, ¿cuánto cobra por esas horas extra y está "
        "dentro del máximo mensual permitido?"
    ),
    "capacitacion": (
        "Un líder de equipo de TechCorp no usó su presupuesto de capacitación el año pasado. Este año quiere "
        "hacer un curso de 2300 USD. Considerando lo máximo que puede acumular, ¿cuánto dinero le sobra o le falta?"
    ),
}

ICONOS = {
    "supervisor": "🧭", "investigador": "🔎", "analista": "🧮", "validador": "✅",
    "sintesis": "📝", "escalar_humano": "🧑‍💼",
}


class _AgenteQueFalla:
    """Envuelve un agente para simular que su herramienta/LLM se cae (camino de fallo)."""

    async def ainvoke(self, _entrada):
        raise TimeoutError("fallo simulado: la herramienta del especialista no respondió")


def imprimir_evento(evento) -> None:
    icono = ICONOS.get(evento.nodo, "•")
    print(f"{icono} [{evento.nodo:<14}] {evento.accion}")
    if evento.detalle:
        print(f"{'':19}↳ {evento.detalle[:400]}")


async def ejecutar(pregunta: str, simular_fallo: str | None = None) -> dict:
    componentes = {}
    if simular_fallo:
        componentes[simular_fallo] = _AgenteQueFalla()
    app = construir_grafo(**componentes)

    print("=" * 90)
    print(f"PREGUNTA: {pregunta}")
    print("=" * 90)

    estado = estado_inicial(pregunta)
    final = dict(estado)
    async for actualizacion in app.astream(estado, {"recursion_limit": RECURSION_LIMIT}, stream_mode="updates"):
        for _nodo, update in actualizacion.items():
            for evento in update.get("bitacora", []):
                imprimir_evento(evento)
            for clave, valor in update.items():
                if clave in ("bitacora", "contribuciones", "validaciones", "messages"):
                    final[clave] = final.get(clave, []) + list(valor)
                else:
                    final[clave] = valor

    print("=" * 90)
    print("RESPUESTA FINAL:\n")
    print(final["respuesta_final"])
    print("=" * 90)
    guardar_log(final, simular_fallo)
    return final


def guardar_log(final: dict, simular_fallo: str | None) -> Path:
    carpeta = RAIZ / "logs"
    carpeta.mkdir(exist_ok=True)
    ruta = carpeta / f"ejecucion_{datetime.now():%Y%m%d_%H%M%S}.json"
    registro = {
        "tarea": final["tarea"],
        "simular_fallo": simular_fallo,
        "pasos": final["pasos"],
        "task_completed": final["task_completed"],
        "escalado_humano": final["escalado_humano"],
        "respuesta_final": final["respuesta_final"],
        "bitacora": [e.model_dump() for e in final["bitacora"]],
        "validaciones": [v.model_dump() for v in final["validaciones"]],
        "contribuciones": final["contribuciones"],
        "research_output": final["research_output"].model_dump(exclude={"evidencia"}) if final.get("research_output") else None,
        "analysis_output": final["analysis_output"].model_dump() if final.get("analysis_output") else None,
    }
    ruta.write_text(json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"📁 Log de auditoría guardado en {ruta.relative_to(RAIZ)}")
    return ruta


def exportar_diagrama() -> None:
    """Exporta el diagrama del grafo. No necesita API keys: usa componentes de relleno."""
    marcador = object()
    app = construir_grafo(investigador=marcador, analista=marcador, router=marcador, redactor=marcador)
    docs = RAIZ / "docs"
    docs.mkdir(exist_ok=True)
    grafo = app.get_graph()
    (docs / "grafo.mmd").write_text(grafo.draw_mermaid(), encoding="utf-8")
    print("🗺️  docs/grafo.mmd generado")
    try:
        (docs / "grafo.png").write_bytes(grafo.draw_mermaid_png())
        print("🖼️  docs/grafo.png generado")
    except Exception as e:  # draw_mermaid_png usa la API de mermaid.ink (requiere red)
        print(f"⚠️  No se pudo generar el PNG ({e}); el .mmd igual se renderiza en GitHub.")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Orquestador multi-agente (Supervisor) — TechCorp")
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument("--pregunta", help="Pregunta libre del usuario")
    grupo.add_argument("--escenario", choices=ESCENARIOS, default="vacaciones")
    parser.add_argument("--simular-fallo", choices=["investigador", "analista"], help="Hace fallar a un especialista")
    parser.add_argument("--diagrama", action="store_true", help="Solo exporta el diagrama del grafo")
    args = parser.parse_args()

    if args.diagrama:
        exportar_diagrama()
        return
    asyncio.run(ejecutar(args.pregunta or ESCENARIOS[args.escenario], args.simular_fallo))


if __name__ == "__main__":
    main()
