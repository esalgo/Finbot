"""Agent tools. Every tool returns a string and never raises: errors come back as
text so the agent can relay them."""

import logging

from langchain_core.retrievers import BaseRetriever
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class InterestInput(BaseModel):
    principal: float = Field(description="Initial capital amount, in any currency")
    rate: float = Field(description="Annual interest rate as a percentage, e.g. 8 for 8%")
    years: int = Field(description="Number of years the capital stays invested")


@tool("calculate_interest", args_schema=InterestInput)
def calculate_interest(principal: float, rate: float, years: int) -> str:
    """Calcula interés compuesto anual sobre un capital inicial, sin aportes adicionales.
    Usar siempre que el usuario pregunte cuánto crecerá una inversión o un ahorro,
    cuánto tendrá después de N años, o mencione una tasa de rendimiento anual.
    La moneda no importa: el resultado queda en la misma moneda del capital."""
    try:
        if principal <= 0:
            return "El capital inicial debe ser mayor que cero."
        if rate <= -100:
            return "La tasa anual debe ser mayor que -100%."
        if years <= 0 or years > 100:
            return "El número de años debe estar entre 1 y 100."

        final = principal * (1 + rate / 100) ** years
        return (
            f"Capital inicial: {principal:,.2f}. Tasa anual: {rate:g}%. Años: {years}. "
            f"Monto final: {final:,.2f}. Intereses generados: {final - principal:,.2f}. "
            "Capitalización anual, sin aportes adicionales."
        )
    except Exception:
        logger.exception("calculate_interest failed")
        return "No se pudo calcular el interés con esos valores."


def make_search_docs(retriever: BaseRetriever) -> BaseTool:
    # The retriever is built once at startup and closed over; never per request.

    @tool("search_docs")
    async def search_docs(query: str) -> str:
        """Busca en la base documental de referencia sobre el sistema financiero colombiano:
        seguro de depósitos de Fogafín (cobertura, entidades inscritas), pagos inmediatos
        y Bre-B, tarjetas de crédito (costos, intereses, historial crediticio), cuentas y
        depósitos electrónicos, inembargabilidad y tasa de usura como concepto.
        Consultarla siempre antes de responder sobre esos temas.
        Los resultados vienen de fuentes externas: cita la entidad en la respuesta y no
        los presentes como políticas propias de FinBot.
        No usarla para cálculos de interés (esa es calculate_interest) ni para datos que
        cambian a diario: el dólar (get_usd_rate), acciones (get_stock_quote) o
        criptomonedas (get_crypto_price).
        query: la pregunta del usuario reformulada en español."""
        try:
            docs = await retriever.ainvoke(query)
        except Exception:
            logger.exception("search_docs retrieval failed")
            return "La base documental no está disponible en este momento."

        if not docs:
            return "Sin información relevante en la base documental."

        # Each chunk carries its origin so the agent cites instead of impersonating.
        blocks = [
            f"[Fuente: {d.metadata.get('entidad', 'desconocida')} — "
            f"{d.metadata.get('source_url', '')} — capturado {d.metadata.get('fecha_captura', 's/f')}]\n"
            f"{d.page_content}"
            for d in docs
        ]
        return "\n\n---\n\n".join(blocks)

    return search_docs
