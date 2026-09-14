"""Market data tools: FX (open.er-api.com), stocks (Finnhub), crypto (Coinbase).

Same contract as every tool: return a string, never raise. Each one guards the
"empty data with HTTP 200" case, not only the status code.
"""

import logging
import re
from datetime import datetime, timezone

import httpx
from langchain_core.tools import tool
from pydantic import BaseModel, Field

import settings

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 10
FX_URL = "https://open.er-api.com/v6/latest/USD"
FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"
COINBASE_RATES_URL = "https://api.coinbase.com/v2/exchange-rates"
COINBASE_FIAT_URL = "https://api.coinbase.com/v2/currencies"

SYMBOL_RE = re.compile(r"^[A-Z0-9.\-]{1,12}$")

# Coinbase answers exchange-rates for fiat bases too (USD, EUR, COP). Without
# this list get_crypto_price would silently work as a fiat converter.
_fiat_codes: frozenset[str] | None = None


async def load_fiat_codes() -> bool:
    """Fetch Coinbase's fiat currency list. Called at startup; retried lazily on failure."""
    global _fiat_codes
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            r = await client.get(COINBASE_FIAT_URL)
        r.raise_for_status()
        codes = frozenset(c["id"].upper() for c in r.json().get("data", []) if c.get("id"))
        if not codes:
            raise ValueError("empty fiat currency list")
        _fiat_codes = codes
        logger.info("Loaded %d fiat currency codes from Coinbase", len(codes))
        return True
    except Exception:
        logger.warning("Could not load Coinbase fiat currency list; will retry on next crypto query", exc_info=True)
        return False


def _normalize_code(value: str) -> str:
    return value.strip().upper()


def _utc_date(epoch: int | float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d")


# ── FX ──────────────────────────────────────────────────────────────────────
class UsdRateInput(BaseModel):
    currency: str = Field(
        default="COP",
        description="ISO 4217 code of the currency to price one US dollar in, e.g. 'COP', 'EUR', 'MXN'",
    )


@tool("get_usd_rate", args_schema=UsdRateInput)
async def get_usd_rate(currency: str = "COP") -> str:
    """Consulta el tipo de cambio del dólar estadounidense (USD) frente a una moneda
    tradicional; por defecto, cuántos pesos colombianos (COP) vale un dólar.
    Usar cuando el usuario pregunte a cuánto está el dólar, la tasa de cambio o
    cuántos pesos (u otra moneda tradicional) vale un dólar.
    NO usar para criptomonedas como bitcoin o ethereum, ni siquiera expresadas en
    pesos (esa es get_crypto_price). NO usar para acciones (esa es get_stock_quote).
    Es una tasa de mercado de referencia, no la TRM certificada.
    currency: código ISO de la moneda destino, p. ej. 'COP', 'EUR'."""
    code = _normalize_code(currency)
    if not re.fullmatch(r"[A-Z]{3}", code):
        return f"'{currency}' no es un código de moneda válido (usa códigos como COP o EUR)."
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            r = await client.get(FX_URL)
        if r.status_code != 200:
            logger.warning("open.er-api returned HTTP %s", r.status_code)
            return "No se pudo consultar el tipo de cambio en este momento."

        data = r.json()
        # A failed lookup still comes back as 200 with result="error".
        if data.get("result") != "success":
            logger.warning("open.er-api result=%s", data.get("result"))
            return "No se pudo consultar el tipo de cambio en este momento."

        rate = data.get("rates", {}).get(code)
        if not rate:
            return f"No se encontró la moneda '{code}' en la tabla de tipos de cambio."

        updated = data.get("time_last_update_unix")
        date_note = f" Tasa actualizada el {_utc_date(updated)} (UTC); se publica una vez al día." if updated else ""
        rate = float(rate)
        shown = f"{rate:,.4f}" if rate < 10 else f"{rate:,.2f}"  # 0.86 EUR hides real precision
        return f"1 USD = {shown} {code}.{date_note}"
    except Exception:
        logger.exception("get_usd_rate failed")
        return "No se pudo consultar el tipo de cambio en este momento."


# ── Stocks ──────────────────────────────────────────────────────────────────
class StockQuoteInput(BaseModel):
    symbol: str = Field(description="US-listed ticker symbol in uppercase, e.g. 'AAPL', 'EC'")


@tool("get_stock_quote", args_schema=StockQuoteInput)
async def get_stock_quote(symbol: str) -> str:
    """Consulta la cotización de una acción listada en bolsa de EE. UU.: último precio,
    apertura, máximo, mínimo y cierre de la sesión anterior, con sus fechas.
    Usar cuando el usuario pregunte por el precio o el cierre de la acción de una
    empresa. Para empresas colombianas usar sus ADR en NYSE: 'EC' (Ecopetrol),
    'CIB' (Bancolombia), 'AVAL' (Grupo Aval).
    NO usar para criptomonedas (esa es get_crypto_price) ni para el dólar u otras
    divisas (esa es get_usd_rate).
    symbol: ticker en mayúsculas, p. ej. 'AAPL' (Apple), 'MSFT' (Microsoft)."""
    ticker = _normalize_code(symbol)
    if not SYMBOL_RE.fullmatch(ticker):
        return f"'{symbol}' no parece un ticker válido."
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            # Token in a header, not the query string: httpx logs request URLs.
            r = await client.get(
                FINNHUB_QUOTE_URL,
                params={"symbol": ticker},
                headers={"X-Finnhub-Token": settings.FINNHUB_API_KEY},
            )
        if r.status_code == 429:
            return "Se alcanzó el límite de consultas de bolsa por minuto. Intenta de nuevo en un momento."
        if r.status_code != 200:
            logger.warning("Finnhub returned HTTP %s for %s", r.status_code, ticker)
            return "No se pudo consultar la bolsa en este momento."

        d = r.json()
        # Unknown or delisted tickers come back as 200 with every field in zero.
        if not d.get("c") and not d.get("pc"):
            return f"No se encontró el ticker '{ticker}'. Verifica que sea una acción listada en EE. UU."

        # 't' is the time of the last price. After the close or on weekends 'c' is
        # already that session's close, and 'pc' the close of the session before it.
        session = _utc_date(d["t"]) if d.get("t") else "fecha no disponible"
        return (
            f"{ticker} (precios en USD). "
            f"Último precio: {d['c']:,.2f} (sesión del {session}; si el mercado ya cerró, es el cierre de esa sesión). "
            f"Cierre de la sesión anterior a esa: {d['pc']:,.2f}. "
            f"Apertura: {d['o']:,.2f}. Máximo: {d['h']:,.2f}. Mínimo: {d['l']:,.2f}."
        )
    except Exception:
        logger.exception("get_stock_quote failed for %s", ticker)
        return "No se pudo consultar la bolsa en este momento."


# ── Crypto ──────────────────────────────────────────────────────────────────
class CryptoPriceInput(BaseModel):
    ticker: str = Field(description="Crypto asset ticker in uppercase, e.g. 'BTC', 'ETH', 'SOL'")
    currency: str = Field(
        default="USD",
        description="Currency to express the crypto price in, e.g. 'USD', 'COP'. Default USD",
    )


@tool("get_crypto_price", args_schema=CryptoPriceInput)
async def get_crypto_price(ticker: str, currency: str = "USD") -> str:
    """Consulta el precio actual de una criptomoneda, expresado en la moneda que se pida.
    Usar SOLO para criptomonedas: bitcoin (BTC), ethereum (ETH), solana (SOL), USDT, etc.
    Para "¿a cuánto está el bitcoin en pesos?" usar currency='COP' en esta misma
    tool, sin llamar a get_usd_rate.
    NO usar para acciones de bolsa (esa es get_stock_quote). NO usar para el dólar
    ni para convertir entre dos monedas tradicionales como dólar, euro o peso
    (esa es get_usd_rate): currency solo expresa el precio de una cripto.
    ticker: símbolo en mayúsculas, p. ej. 'BTC', 'ETH'.
    currency: moneda del precio, p. ej. 'COP'. Por defecto USD."""
    base = _normalize_code(ticker)
    target = _normalize_code(currency)
    if not SYMBOL_RE.fullmatch(base) or not SYMBOL_RE.fullmatch(target):
        return f"'{ticker}' / '{currency}' no parecen símbolos válidos."

    if _fiat_codes is None:
        await load_fiat_codes()
    if _fiat_codes is not None and base in _fiat_codes:
        return (
            f"'{base}' es una moneda tradicional, no una criptomoneda. "
            "Para tipos de cambio usa get_usd_rate."
        )
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            r = await client.get(COINBASE_RATES_URL, params={"currency": base})
        # Unknown tickers come back as 400 "base currency not recognized".
        if r.status_code in (400, 404):
            return f"No se encontró la criptomoneda '{base}' en Coinbase."
        if r.status_code != 200:
            logger.warning("Coinbase returned HTTP %s for %s", r.status_code, base)
            return "No se pudo consultar el mercado cripto en este momento."

        rates = r.json().get("data", {}).get("rates", {})
        price = rates.get(target)
        if not price or float(price) <= 0:
            return f"No se encontró el precio de '{base}' en {target}."

        return f"1 {base} = {float(price):,.2f} {target} (precio de referencia de Coinbase en este momento)."
    except Exception:
        logger.exception("get_crypto_price failed for %s/%s", base, target)
        return "No se pudo consultar el mercado cripto en este momento."


MARKET_TOOLS = [get_usd_rate, get_stock_quote, get_crypto_price]
