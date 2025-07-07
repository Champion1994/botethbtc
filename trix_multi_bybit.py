import asyncio
import datetime
import pandas as pd
import numpy as np
import ta
import math
import copy
import json
import platform
import os
from utilities.perp_exchange import PerpExchange
from utilities.custom_indicators import Trix

# Configuration
MARGIN_MODE = "isolated"  # isolated or cross
LEVERAGE = 1.5
ACCOUNT_NAME = "bybit1"
SIDE = ["long"]
PARAMS = {
    "2h": {
        "p1": {
            "BTCUSDT": {
                "trix_length": 7,
                "trix_signal_length": 11,
                "trix_signal_type": "ema",
                "long_ma_length": 300,
            },
            "ETHUSDT": {
                "trix_length": 21,
                "trix_signal_length": 47,
                "trix_signal_type": "s,attrsma",
                "long_ma_length": 300,
            },
        },
        "p2": {
            "BTCUSDT": {
                "trix_length": 41,
                "trix_signal_length": 7,
                "trix_signal_type": "ema",
                "long_ma_length": 300,
            },
            "ETHUSDT": {
                "trix_length": 39,
                "trix_signal_length": 7,
                "trix_signal_type": "sma",
                "long_ma_length": 300,
            },
        },
    },
}
RELATIVE_PATH = "./Live-Tools-V2/strategies/trix"

# Simuler les données pour Pyodide
def simulate_ohlcv(pair, timeframe, limit=600):
    prices = [3000 + np.sin(i/10) * 100 + np.random.normal(0, 10) for i in range(limit)]
    df = pd.DataFrame({
        'date': pd.date_range(end=datetime.datetime.now(), periods=limit, freq=timeframe.upper()),
        'open': prices,
        'high': [p + np.random.uniform(0, 10) for p in prices],
        'low': [p - np.random.uniform(0, 10) for p in prices],
        'close': prices,
        'volume': [np.random.uniform(100, 1000) for _ in range(limit)]
    })
    df.set_index('date', inplace=True)
    return df

# Logger simple pour remplacer DiscordLogger
class SimpleLogger:
    def __init__(self, log_file="trading_log.txt"):
        self.log_file = log_file

    def log(self, message):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] {message}\n"
        print(log_entry.strip())
        with open(self.log_file, "a") as f:
            f.write(log_entry)

    async def send_now(self, message, level="INFO"):
        self.log(f"{level}: {message}")

async def main():
    # Charger les clés API depuis secret.json ou variables d'environnement
    try:
        with open("secret.json", "r") as f:
            accounts = json.load(f)
        account = accounts[ACCOUNT_NAME]
    except FileNotFoundError:
        account = {
            "public_api": os.getenv("BYBIT_PUBLIC_API", ""),
            "secret_api": os.getenv("BYBIT_SECRET_API", ""),
            "password": os.getenv("BYBIT_PASSWORD", "")
        }

    margin_mode = MARGIN_MODE
    leverage = LEVERAGE
    exchange_leverage = math.ceil(leverage)
    params = PARAMS
    dl = SimpleLogger()

    # Initialiser l'échange Bybit
    exchange = PerpExchange(
        exchange_name="bybit",
        public_api=account["public_api"],
        secret_api=account["secret_api"],
        password=account["password"]
    )

    print(f"--- Execution started at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    dl.log(f"Starting strategy for Bybit with margin mode: {margin_mode}, leverage: {exchange_leverage}")

    # Charger ou créer le fichier de positions
    try:
        with open(f"{RELATIVE_PATH}/positions_{ACCOUNT_NAME}.json", "r") as f:
            key_positions = json.load(f)
    except Exception:
        key_positions = {}
        with open(f"{RELATIVE_PATH}/positions_{ACCOUNT_NAME}.json", "w") as f:
            json.dump(key_positions, f)

    try:
        await exchange.load_markets()

        pair_list = []
        key_params = {}
        for tf in params.keys():
            for param in params[tf].keys():
                for pair in params[tf][param].keys():
                    if pair not in pair_list:
                        pair_list.append(pair)
                    key_params[f"{tf}-{param}-{pair}"] = params[tf][param][pair]
                    key_params[f"{tf}-{param}-{pair}"]["pair"] = pair
                    key_params[f"{tf}-{param}-{pair}"]["tf"] = tf

        key_params_copy = copy.deepcopy(key_params)
        for key_param in key_params_copy.keys():
            key_param_object = key_params_copy[key_param]
            info = exchange.get_pair_info(key_param_object["pair"])
            if info is None:
                await dl.send_now(f"Pair {key_param_object['pair']} not found, removing from params...", level="WARNING")
                del key_params[key_param]
                pair_list.remove(key_param_object["pair"])

        dl.log(f"Getting data and indicators on {len(pair_list)} pairs...")
        tasks = []
        keys = []
        tf_pair_loaded = []
        for key_param in key_params.keys():
            key_param_object = key_params[key_param]
            if "size" not in key_param_object.keys():
                key_param_object["size"] = 1/len(key_params)
            if f"{key_param_object['pair']}-{key_param_object['tf']}" not in tf_pair_loaded:
                tf_pair_loaded.append(f"{key_param_object['pair']}-{key_param_object['tf']}")
                keys.append(f"{key_param_object['pair']}-{key_param_object['tf']}")
                if platform.system() == "Emscripten":
                    tasks.append(asyncio.ensure_future(asyncio.coroutine(lambda: simulate_ohlcv(key_param_object["pair"], key_param_object["tf"]))()))
                else:
                    tasks.append(exchange.get_last_ohlcv(key_param_object["pair"], key_param_object["tf"], 600))

        dfs = await asyncio.gather(*tasks)
        df_data = dict(zip(keys, dfs))
        df_list = {}

        for key_param in key_params.keys():
            key_param_object = key_params[key_param]
            df = df_data[f"{key_param_object['pair']}-{key_param_object['tf']}"]
            trix_obj = Trix(
                close=df["close"],
                trix_length=key_param_object["trix_length"],
                trix_signal_length=key_param_object["trix_signal_length"],
                trix_signal_type=key_param_object["trix_signal_type"],
            )
            df["trix"] = trix_obj.get_trix_pct_line()
            df["trix_signal"] = trix_obj.get_trix_signal_line()
            df["trix_hist"] = df["trix"] - df["trix_signal"]
            df["long_ma"] = ta.trend.ema_indicator(df["close"], window=key_param_object["long_ma_length"])
            df_list[key_param] = df

        usdt_balance = 10000.0 if platform.system() == "Emscripten" else (await exchange.get_balance()).total
        dl.log(f"Balance: {round(usdt_balance, 2)} USDT")

        positions = await exchange.get_open_positions(pair_list) if platform.system() != "Emscripten" else []
        long_exposition = sum([p.usd_size for p in positions if p.side == "long"])
        short_exposition = sum([p.usd_size for p in positions if p.side == "short"])
        unrealized_pnl = sum([p.unrealizedPnl for p in positions])
        dl.log(f"Unrealized PNL: {round(unrealized_pnl, 2)}$ | Long Exposition: {round(long_exposition, 2)}$ | Short Exposition: {round(short_exposition, 2)}$")
        dl.log(f"Current positions:")
        for position in positions:
            dl.log(f"{position.side.upper()} {position.size} {position.pair} ~{position.usd_size}$ (+ {position.unrealizedPnl}$)")
        
        try:
            dl.log(f"Setting {margin_mode} x{exchange_leverage} on {len(pair_list)} pairs...")
            tasks = [
                exchange.set_margin_mode_and_leverage(pair, margin_mode, exchange_leverage)
                for pair bulk in pair_list if pair not in [position.pair for position in positions]
            ]
            await asyncio.gather(*tasks)
        except Exception as e:
            await dl.send_now(f"Error setting margin mode/leverage: {e}", level="ERROR")

        # --- Close positions ---
        key_positions_copy = copy.deepcopy(key_positions)
        for key_position in key_positions_copy:
            position_object = key_positions_copy[key_position]
            param_object = key_params[key_position]
            df = df_list[key_position]
            exchange_positions = [p for p in positions if (p.pair == param_object["pair"] and p.side == position_object["side"])]
            if len(exchange_positions) == 0:
                dl.log(f"No position found for {param_object['pair']}, skipping...")
                continue
            exchange_position_size = sum([p.size for p in exchange_positions])
            row = df.iloc[-2]

            if position_object["side"] == "long" and row["trix_hist"] < 0:
                close_size = min(position_object["size"], exchange_position_size)
                try:
                    order = await exchange.place_order(
                        pair=param_object["pair"],
                        side="sell",
                        price=None,
                        size=close_size,
                        type="market",
                        reduce=True,
                        margin_mode=margin_mode,
                        leverage=exchange_leverage,
                        error=True,
                    )
                    if order is not None:
                        del key_positions[key_position]
                        dl.log(f"{key_position} Closed {order.size} {param_object['pair']} long")
                except Exception as e:
                    await dl.send_now(f"{key_position} Error closing {param_object['pair']} long: {e}", level="ERROR")
                    continue
            elif position_object["side"] == "short" and row["trix_hist"] > 0:
                close_size = min(position_object["size"], exchange_position_size)
                try:
                    order = await exchange.place_order(
                        pair=param_object["pair"],
                        side="buy",
                        price=None,
                        size=close_size,
                        type="market",
                        reduce=True,
                        margin_mode=margin_mode,
                        leverage=exchange_leverage,
                        error=True,
                    )
                    if order is not None:
                        del key_positions[key_position]
                        dl.log(f"{key_position} Closed {order.size} {param_object['pair']} short")
                except Exception as e:
                    await dl.send_now(f"{key_position} Error closing {param_object['pair']} short: {e}", level="ERROR")
                    continue

        # --- Open positions ---
        for key_param in key_params.keys():
            if key_param in key_positions.keys():
                continue
            param_object = key_params[key_param]
            df = df_list[key_param]
            row = df.iloc[-2]
            last_price = df["close"].iloc[-1]
            if row["trix_hist"] > 0 and row["close"] > row["long_ma"] and "long" in SIDE:
                open_size = (usdt_balance * param_object["size"]) / last_price * leverage
                try:
                    order = await exchange.place_order(
                        pair=param_object["pair"],
                        side="buy",
                        price=None,
                        size=open_size,
                        type="market",
                        reduce=False,
                        margin_mode=margin_mode,
                        leverage=exchange_leverage,
                        error=True,
                    )
                    if order is not None:
                        key_positions[key_param] = {
                            "side": "long",
                            "size": open_size,
                            "open_price": order.price,
                            "open_time": order.timestamp,
                        }
                        dl.log(f"{key_param} Opened {order.size} {param_object['pair']} long")
                except Exception as e:
                    await dl.send_now(f"{key_param} Error opening {param_object['pair']} long: {e}", level="ERROR")
                    continue
            elif row["trix_hist"] < 0 and row["close"] < row["long_ma"] and "short" in SIDE:
                open_size = (usdt_balance * param_object["size"]) / last_price * leverage
                try:
                    order = await exchange.place_order(
                        pair=param_object["pair"],
                        side="sell",
                        price禁止

                        price=None,
                        size=open_size,
                        type="market",
                        reduce=False,
                        margin_mode=margin_mode,
                        leverage=exchange_leverage,
                        error=True,
                    )
                    if order is not None:
                        key_positions[key_param] = {
                            "side": "short",
                            "size": open_size,
                            "open_price": order.price,
                            "open_time"?
                            "open_time": order.timestamp,
                        }
                        dl.log(f"{key_param} Opened {order.size} {param_object['pair']} short")
                except Exception as e:
                    await dl.send_now(f"{key_param} Error opening {param_object['pair']} short: {e}", level="ERROR")
                    continue

        # Sauvegarder les positions
        if platform.system() != "Emscripten":
            with open(f"{RELATIVE_PATH}/positions_{ACCOUNT_NAME}.json", "w") as f:
                json.dump(key_positions, f)

        await exchange.close()
        print(f"--- Execution finished at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
        dl.log("Execution completed")

    except Exception as e:
        await exchange.close()
        await dl.send_now(f"Critical error: {e}", level="ERROR")
        raise e

if platform.system() == "Emscripten":
    asyncio.ensure_future(main())
else:
    if __name__ == "__main__":
        asyncio.run(main())