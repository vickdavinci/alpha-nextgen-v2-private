"""Helper classes extracted from options_engine.py to reduce file size."""
from enum import Enum
from typing import Optional

from AlgorithmImports import *


class SpreadStrategy(Enum):
    BULL_CALL_DEBIT = "BULL_CALL_DEBIT"
    BEAR_PUT_DEBIT = "BEAR_PUT_DEBIT"
    BULL_PUT_CREDIT = "BULL_PUT_CREDIT"
    BEAR_CALL_CREDIT = "BEAR_CALL_CREDIT"


class EntryScore:
    def __init__(self, score: float, details: dict):
        self.score = score
        self.details = details


class OptionContract:
    def __init__(
        self,
        symbol,
        strike: float,
        expiry,
        right: OptionRight,
        bid: float,
        ask: float,
        delta: float,
        gamma: float,
        vega: float,
        theta: float,
        implied_vol: float,
        open_interest: int,
        volume: int,
    ):
        self.symbol = symbol
        self.strike = strike
        self.expiry = expiry
        self.right = right
        self.bid = bid
        self.ask = ask
        self.delta = delta
        self.gamma = gamma
        self.vega = vega
        self.theta = theta
        self.implied_vol = implied_vol
        self.open_interest = open_interest
        self.volume = volume
        self.direction = None


class OptionsPosition:
    def __init__(self, symbol, quantity: int, entry_price: float, entry_time, strategy: str):
        self.symbol = symbol
        self.quantity = quantity
        self.entry_price = entry_price
        self.entry_time = entry_time
        self.strategy = strategy


class SpreadPosition:
    def __init__(
        self,
        long_leg,
        short_leg,
        num_spreads: int,
        net_debit: float,
        entry_time,
        strategy: SpreadStrategy,
    ):
        self.long_leg = long_leg
        self.short_leg = short_leg
        self.num_spreads = num_spreads
        self.net_debit = net_debit
        self.entry_time = entry_time
        self.strategy = strategy


class SpreadFillTracker:
    def __init__(self):
        self.pending_long_leg = None
        self.pending_short_leg = None
        self.long_filled = False
        self.short_filled = False


class ExitOrderTracker:
    def __init__(self, long_order_id: int, short_order_id: int, is_profit_target: bool):
        self.long_order_id = long_order_id
        self.short_order_id = short_order_id
        self.is_profit_target = is_profit_target


class MicroRegimeState:
    def __init__(self):
        self.vix_level = "UNKNOWN"
        self.vix_direction = "STABLE"
        self.micro_score = 50
        self.signal = "NEUTRAL"
