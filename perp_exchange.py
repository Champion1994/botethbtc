import ccxt.async_support as ccxt
import pandas as pd
import asyncio
from pydantic import BaseModel
from decimal import Decimal

class UsdtBalance(BaseModel):
    total: float
    free: float
    used: float

class Info(BaseModel):
    success: bool
    message: str

class Order(BaseModel):
    id: str
    pair: str
    type: str
    side: str
    price: float
    size: float
    reduce: bool
    filled: float
    remaining: float
    timestamp: int

class Position(BaseModel):
    pair: str
    side: str
    size: float
    usd_size: float
    entry_price: float
    current_price: float
    unrealizedPnl: float
    liquidation_price: float
    margin_mode: str
    leverage: float
    hedge_mode: bool
    open_timestamp: int
    take_profit_price: float
    stop_loss_price: float

class PerpExchange:
    def __init__(self, exchange_name, public_api=None, secret_api=None, password=None):
        self.exchange_name = exchange_name.lower()
        self._auth = bool(public_api and secret_api)
        auth_object = {
            "apiKey": public_api,
            "secret": secret_api,
            "password": password,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"}
        }
        self._session = getattr(ccxt, exchange_name)(auth_object)
        self.market = None

    async def load_markets(self):
        if not self.market:
            self.market = await self._session.load_markets()

    async def close(self):
        await self._session.close()

    def get_pair_info(self, pair):
        pair = self.normalize_pair(pair)
        return self.market.get(pair)

    def normalize_pair(self, pair):
        if self.exchange_name == "bybit":
            return pair.replace("/USDT", "USDT")
        return pair + ":USDT"

    def denormalize_pair(self, pair):
        if self.exchange_name == "bybit":
            return pair.replace("USDT", "/USDT")
        return pair.replace(":USDT", "")

    async def get_last_ohlcv(self, pair, timeframe, limit=1000):
        await self.load_markets()
        pair = self.normalize_pair(pair)
        ts_dict = {
            "1m": 1 * 60 * 1000,
            "5m": 5 * 60 * 1000,
            "15m": 15 * 60 * 1000,
            "1h": 60 * 60 * 1000,
            "2h": 2 * 60 * 60 * 1000,
            "4h": 4 * 60 * 60 * 1000,
            "1d": 24 * 60 * 60 * 1000,
        }
        end_ts = int(datetime.datetime.now().timestamp() * 1000)
        start_ts = end_ts - (limit * ts_dict[timeframe])
        current_ts = start_ts
        tasks = []
        bitmart_limit = 500
        while current_ts < end_ts:
            req_end_ts = min(current_ts + (bitmart_limit * ts_dict[timeframe]), end_ts)
            tasks.append(
                self._session.fetch_ohlcv(
                    pair,
                    timeframe,
                    params={
                        "start_time": str(int(current_ts / 1000)),
                        "end_time": str(int(req_end_ts / 1000)),
                    },
                )
            )
            current_ts += (bitmart_limit * ts_dict[timeframe]) + 1
        ohlcv_unpack = await asyncio.gather(*tasks)
        ohlcv_list = [item for sublist in ohlcv_unpack for item in sublist]
        df = pd.DataFrame(ohlcv_list, columns=["date", "open", "high", "low", "close", "volume"])
        df.set_index("date", inplace=True)
        df.index = pd.to_datetime(df.index, unit="ms")
        df.sort_index(inplace=True)
        return df

    async def get_balance(self):
        if platform.system() == "Emscripten":
            return UsdtBalance(total=10000.0, free=10000.0, used=0.0)
        resp = await self._session.fetch_balance(params={"defaultType": "swap"})
        if self.exchange_name == "bybit":
            usdt_data = resp["info"]["result"]["list"][0]
            return UsdtBalance(
                total=float(usdt_data["totalEquity"]),
                free=float(usdt_data["availableBalance"]),
                used=float(usdt_data["usedMargin"])
            )
        return UsdtBalance(total=0.0, free=0.0, used=0.0)

    async def set_margin_mode_and_leverage(self, pair, margin_mode, leverage):
        if margin_mode not in ["cross", "isolated"]:
            raise Exception("Margin mode must be either 'cross' or 'isolated'")
        pair = self.normalize_pair(pair)
        try:
            await self._session.set_margin_mode(margin_mode, pair)
            await self._session.set_leverage(leverage, pair, params={"positionIdx": 0})
            return Info(success=True, message=f"Margin mode and leverage set to {margin_mode} and {leverage}x")
        except Exception as e:
            raise e

    async def get_open_positions(self, pairs):
        if platform.system() == "Emscripten":
            return []
        pairs = [self.normalize_pair(pair) for pair in pairs]
        resp = await self._session.fetch_positions(symbols=pairs, params={"settleCoin": "USDT"})
        return_positions = []
        for position in resp:
            if float(position["contracts"]) == 0:
                continue
            liquidation_price = float(position["liquidationPrice"]) if position["liquidationPrice"] else 0
            take_profit_price = float(position["takeProfitPrice"]) if position["takeProfitPrice"] else 0
            stop_loss_price = float(position["stopLossPrice"]) if position["stopLossPrice"] else 0
            hedge_mode = bool(position["hedged"]) if "hedged" in position else False
            return_positions.append(
                Position(
                    pair=self.denormalize_pair(position["symbol"]),
                    side=position["side"],
                    size=Decimal(position["contracts"]) * Decimal(position["contractSize"]),
                    usd_size=round(float(position["notional"]), 2),
                    entry_price=float(position["entryPrice"]),
                    current_price=float(position["markPrice"]),
                    unrealizedPnl=float(position["unrealizedPnl"]),
                    liquidation_price=liquidation_price,
                    leverage=float(position["leverage"]),
                    margin_mode=position["info"].get("marginMode", "isolated"),
                    hedge_mode=hedge_mode,
                    open_timestamp=int(position["info"].get("updatedTime", 0)),
                    take_profit_price=take_profit_price,
                    stop_loss_price=stop_loss_price,
                )
            )
        return return_positions

    async def place_order(self, pair, side, price, size, type="market", reduce=False, margin_mode="cross", leverage=1, error=True):
        try:
            pair = self.normalize_pair(pair)
            size = Decimal(self._session.amount_to_precision(pair, size))
            params = {"reduceOnly": reduce, "positionIdx": 0}
            if type == "limit":
                params["price"] = self._session.price_to_precision(pair, price)
            resp = await self._session.create_order(
                symbol=pair,
                type=type,
                side=side,
                amount=size,
                params=params
            )
            order = await self.get_order_by_id(resp["id"], pair)
            return order
        except Exception as e:
            if error:
                raise e
            return None

    async def get_order_by_id(self, order_id, pair):
        pair = self.normalize_pair(pair)
        resp = await self._session.fetch_order(order_id, pair)
        contract_size = float(self.get_pair_info(self.denormalize_pair(pair))["contractSize"])
        reduce = bool(resp["reduceOnly"])
        return Order(
            id=resp["id"],
            pair=self.denormalize_pair(resp["symbol"]),
            type=resp["type"],
            side=resp["side"],
            price=float(resp["price"]) if resp["price"] else 0.0,
            size=Decimal(resp["amount"]) * Decimal(contract_size),
            reduce=reduce,
            filled=Decimal(resp["filled"]) * Decimal(contract_size),
            remaining=Decimal(resp["remaining"]) * Decimal(contract_size),
            timestamp=resp["timestamp"]
        )