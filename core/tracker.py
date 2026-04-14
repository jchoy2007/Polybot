"""
PolyBot - Win Rate Tracker
============================
Rastrea resultados de apuestas de forma persistente.
Muestra win rate, profit/loss real en cada ciclo.

Se guarda en data/trade_results.json
"""

import os
import json
import logging
import aiohttp
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger("polybot.tracker")

RESULTS_FILE = "data/trade_results.json"
DATA_API_URL = "https://data-api.polymarket.com"
GAMMA_API_URL = "https://gamma-api.polymarket.com"


class WinRateTracker:
    def __init__(self):
        self.trades: List[Dict] = []
        self._load()

    def _load(self):
        try:
            with open(RESULTS_FILE, "r") as f:
                self.trades = json.load(f)
            self._recalculate_won_profits()
        except (FileNotFoundError, json.JSONDecodeError):
            self.trades = []

    def _recalculate_won_profits(self):
        """
        Re-calcula profits de trades WON que pudieran tener valores
        incorrectos por el bug de cur_value. Cada token ganador = $1.
        """
        fixed = 0
        for t in self.trades:
            if t.get("result") != "WON":
                continue
            amount = t.get("amount", 0) or 0
            buy_price = t.get("price", 0) or 0
            if amount <= 0 or buy_price <= 0:
                continue
            correct_tokens = amount / buy_price
            correct_profit = round(correct_tokens - amount, 2)
            current_profit = t.get("profit", 0) or 0
            # Si el profit actual es mucho menor al correcto (>$0.5 diff), arreglar
            if abs(correct_profit - current_profit) > 0.5:
                t["profit"] = correct_profit
                t["payout"] = round(correct_tokens, 2)
                fixed += 1
        if fixed > 0:
            logger.info(f"🔧 Recalculados {fixed} profits de trades WON (bug cur_value)")
            self._save()

    def _save(self):
        os.makedirs("data", exist_ok=True)
        with open(RESULTS_FILE, "w") as f:
            json.dump(self.trades, f, indent=2)

    def add_trade(self, market_id: str, question: str, side: str,
                  amount: float, price: float, strategy: str = "IA"):
        """Registra un trade nuevo (pendiente de resultado)."""
        # No duplicar
        for t in self.trades:
            if t.get("market_id") == market_id and t.get("strategy") == strategy:
                return

        trade = {
            "market_id": market_id,
            "question": question[:60],
            "side": side,
            "amount": amount,
            "price": price,
            "strategy": strategy,
            "timestamp": datetime.now().isoformat(),
            "result": "PENDING",  # PENDING, WON, LOST
            "payout": 0.0,
            "profit": 0.0,
        }
        self.trades.append(trade)
        self._save()

    async def check_results(self, address: str):
        """Verifica posiciones en Data API para actualizar resultados."""
        # Obtener posiciones actuales
        current_positions = {}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
            addresses = [addr for addr in [funder, address] if addr]
            for addr in addresses:
                try:
                    async with session.get(
                        f"{DATA_API_URL}/positions?user={addr.lower()}"
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data:
                                for pos in data:
                                    cid = pos.get("conditionId", "")
                                    if cid:
                                        current_positions[cid] = pos
                                break
                except:
                    continue

        # Revisar trades pendientes
        updated = False
        for trade in self.trades:
            if trade["result"] != "PENDING":
                continue

            # Buscar en posiciones actuales por título Y LADO correcto
            # Bug anterior: matcheaba solo por título, entonces si YES y NO
            # del mismo mercado aparecían, agarraba el primero (podía ser
            # el lado perdedor) y marcaba nuestro trade como LOST
            # incorrectamente.
            found = False
            trade_side_upper = (trade.get("side", "") or "").upper()

            for cid, pos in current_positions.items():
                title = pos.get("title") or pos.get("question") or ""
                pos_side = (pos.get("outcome") or pos.get("side") or "").upper()

                if trade["question"][:30].lower() not in title.lower():
                    continue

                # CRÍTICO: verificar que sea el MISMO lado que compramos.
                # Polymarket puede devolver posiciones de YES y NO del mismo
                # mercado. Solo nos interesa el lado donde apostamos.
                if trade_side_upper and pos_side:
                    # Para markets YES/NO estándar
                    yes_no_match = (
                        (trade_side_upper in ("YES", "NO") and
                         pos_side in ("YES", "NO") and
                         trade_side_upper == pos_side)
                    )
                    # Para markets con outcomes específicos (Over/Under,
                    # nombres de equipos), matchea exacto o por sustring
                    specific_match = (
                        trade_side_upper in pos_side or
                        pos_side in trade_side_upper
                    )
                    if not (yes_no_match or specific_match):
                        continue  # Es el lado contrario, ignorar

                cur_value = float(pos.get("currentValue") or 0)
                size = float(pos.get("size") or 0)
                cur_price = float(pos.get("curPrice") or 0)

                if size <= 0:
                    continue

                # Perdió: valor actual casi 0 (en NUESTRO lado)
                if cur_value <= 0.01 and size > 0:
                    trade["result"] = "LOST"
                    trade["payout"] = 0
                    trade["profit"] = -trade["amount"]
                    updated = True
                    logger.info(f"   ❌ PERDIDA: {trade['question'][:40]} | -${trade['amount']:.2f}")
                # Ganó: precio actual >= 95¢ (casi resuelto a favor)
                elif cur_price >= 0.95:
                    trade["result"] = "WON"
                    buy_price = trade.get("price", 0) or 0.5
                    tokens = trade["amount"] / buy_price if buy_price > 0 else 0
                    trade["payout"] = round(tokens, 2)
                    trade["profit"] = round(tokens - trade["amount"], 2)
                    updated = True
                    logger.info(f"   ✅ GANADA: {trade['question'][:40]} | +${trade['profit']:.2f}")

                found = True
                break

            # Posición desapareció = ya fue cobrada (redeem)
            if not found:
                trade_time = trade.get("timestamp", "")
                if trade_time:
                    try:
                        trade_dt = datetime.fromisoformat(trade_time)
                        hours_ago = (datetime.now() - trade_dt).total_seconds() / 3600

                        # Si tiene más de 2 horas y desapareció, buscar en Gamma API
                        if hours_ago > 2:
                            try:
                                async with aiohttp.ClientSession(
                                    timeout=aiohttp.ClientTimeout(total=10)
                                ) as session:
                                    # Buscar mercado por ID
                                    mid = trade.get("market_id", "")
                                    if mid:
                                        async with session.get(
                                            f"{GAMMA_API_URL}/markets/{mid}"
                                        ) as resp:
                                            if resp.status == 200:
                                                market = await resp.json()
                                                resolved = market.get("closed", False) or market.get("resolved", False)
                                                if resolved:
                                                    # Verificar resultado
                                                    outcome = market.get("outcome", "")
                                                    winning_outcome = market.get("winningOutcome", "")
                                                    resolution = (outcome or winning_outcome or "").upper()
                                                    trade_side = trade.get("side", "").upper()

                                                    if resolution and trade_side:
                                                        if trade_side in resolution or resolution in trade_side:
                                                            trade["result"] = "WON"
                                                            tokens = trade["amount"] / trade["price"]
                                                            trade["payout"] = round(tokens, 2)
                                                            trade["profit"] = round(tokens - trade["amount"], 2)
                                                            updated = True
                                                            logger.info(f"   ✅ COBRADA/GANADA: {trade['question'][:40]} | +${trade['profit']:.2f}")
                                                        else:
                                                            trade["result"] = "LOST"
                                                            trade["payout"] = 0
                                                            trade["profit"] = -trade["amount"]
                                                            updated = True
                                                            logger.info(f"   ❌ COBRADA/PERDIDA: {trade['question'][:40]} | -${trade['amount']:.2f}")
                                                    elif hours_ago > 48:
                                                        # Más de 48h sin posición = posible pérdida,
                                                        # pero no podemos estar seguros.
                                                        # Antes marcábamos automáticamente como LOST,
                                                        # pero eso causaba falsos negativos (trades que
                                                        # GANARON y fueron cobrados quedaban marcados
                                                        # como LOST). Mejor dejar como PENDING y avisar.
                                                        logger.warning(
                                                            f"   ⚠️ AMBIGUO: {trade['question'][:40]} "
                                                            f"({hours_ago:.0f}h sin posición) — "
                                                            f"verificar manualmente en Polymarket"
                                                        )
                                                        continue
                                                    # Rama vieja mantenida solo para el flujo existente:
                                                    if False:
                                                        trade["result"] = "LOST"
                                                        trade["payout"] = 0
                                                        trade["profit"] = -trade["amount"]
                                                        updated = True
                                                        logger.info(f"   ❌ DESAPARECIDA: {trade['question'][:40]} | -${trade['amount']:.2f}")
                            except Exception as e:
                                logger.debug(f"   Error buscando mercado: {e}")
                    except:
                        pass

        if updated:
            self._save()

    def get_summary(self) -> str:
        """Retorna resumen de win rate para mostrar en el ciclo."""
        won = [t for t in self.trades if t["result"] == "WON"]
        lost = [t for t in self.trades if t["result"] == "LOST"]
        pending = [t for t in self.trades if t["result"] == "PENDING"]

        total_resolved = len(won) + len(lost)
        win_rate = len(won) / total_resolved * 100 if total_resolved > 0 else 0

        total_profit = sum(t["profit"] for t in won)
        total_loss = sum(t["profit"] for t in lost)
        net_pnl = total_profit + total_loss

        # Por estrategia
        strategies = {}
        for t in self.trades:
            s = t.get("strategy", "IA")
            if s not in strategies:
                strategies[s] = {"won": 0, "lost": 0, "pending": 0, "profit": 0}
            if t["result"] == "WON":
                strategies[s]["won"] += 1
                strategies[s]["profit"] += t["profit"]
            elif t["result"] == "LOST":
                strategies[s]["lost"] += 1
                strategies[s]["profit"] += t["profit"]
            else:
                strategies[s]["pending"] += 1

        lines = []
        lines.append(f"📊 WIN RATE: {len(won)}/{total_resolved} ({win_rate:.0f}%) | "
                     f"Profit: ${total_profit:+.2f} | Pérdidas: ${total_loss:.2f} | "
                     f"Neto: ${net_pnl:+.2f} | Pendientes: {len(pending)}")

        for s, data in strategies.items():
            s_total = data["won"] + data["lost"]
            s_wr = data["won"] / s_total * 100 if s_total > 0 else 0
            lines.append(f"   {s}: {data['won']}/{s_total} ({s_wr:.0f}%) | "
                        f"P&L: ${data['profit']:+.2f} | Pendientes: {data['pending']}")

        return "\n".join(lines)

    def mark_won(self, question_fragment: str, payout: float = 0):
        """Marca manualmente un trade como ganado."""
        for t in self.trades:
            if t["result"] == "PENDING" and question_fragment.lower() in t["question"].lower():
                t["result"] = "WON"
                t["payout"] = payout if payout > 0 else t["amount"] / t["price"]
                t["profit"] = t["payout"] - t["amount"]
                self._save()
                return True
        return False

    def mark_lost(self, question_fragment: str):
        """Marca manualmente un trade como perdido."""
        for t in self.trades:
            if t["result"] == "PENDING" and question_fragment.lower() in t["question"].lower():
                t["result"] = "LOST"
                t["payout"] = 0
                t["profit"] = -t["amount"]
                self._save()
                return True
        return False
