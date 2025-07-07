import numpy as np
import pandas as pd
import ta

def get_n_columns(df, columns, n=1):
    dt = df.copy()
    for col in columns:
        dt["n"+str(n)+"_"+col] = dt[col].shift(n)
    return dt

def rma(input_data: pd.Series, period: int) -> pd.Series:
    data = input_data.copy()
    alpha = 1 / period
    rma = data.ewm(alpha=alpha, adjust=False).mean()
    return rma

class Trix:
    def __init__(
        self,
        close: pd.Series,
        trix_length: int = 9,
        trix_signal_length: int = 21,
        trix_signal_type: str = "sma"
    ):
        self.close = close
        self.trix_length = trix_length
        self.trix_signal_length = trix_signal_length
        self.trix_signal_type = trix_signal_type
        self._run()

    def _run(self):
        self.trix_line = ta.trend.ema_indicator(
            ta.trend.ema_indicator(
                ta.trend.ema_indicator(
                    close=self.close, window=self.trix_length),
                window=self.trix_length), window=self.trix_length)
        
        self.trix_pct_line = self.trix_line.pct_change() * 100
        if self.trix_signal_type == "sma":
            self.trix_signal_line = ta.trend.sma_indicator(
                close=self.trix_pct_line, window=self.trix_signal_length)
        elif self.trix_signal_type == "ema":
            self.trix_signal_line = ta.trend.ema_indicator(
                close=self.trix_pct_line, window=self.trix_signal_length)
        self.trix_histo = self.trix_pct_line - self.trix_signal_line

    def get_trix_line(self) -> pd.Series:
        return pd.Series(self.trix_line, name="trix_line")

    def get_trix_pct_line(self) -> pd.Series:
        return pd.Series(self.trix_pct_line, name="trix_pct_line")

    def get_trix_signal_line(self) -> pd.Series:
        return pd.Series(self.trix_signal_line, name="trix_signal_line")

    def get_trix_histo(self) -> pd.Series:
        return pd.Series(self.trix_histo, name="trix_histo")