# Recorded BTC pullback

`btc_confirmed_pullback.csv` contains unmodified BTCUSDm OHLC and tick-volume
observations from the local MT5 evidence capture made on 2026-09-22 at
23:43:14 UTC (`.runtime/btc_broker_evidence.py`). That read-only capture used
`copy_rates_from_pos(..., 1, 3000)`, excluding the forming candle. The original
files are `.runtime/btc-M5-evidence.csv`, `btc-M15-evidence.csv` and
`btc-H1-evidence.csv`; the regression has no dependency on those local files,
MT5, credentials, or network access.

The fixture retains 100 M5 bars and 120 bars each of M15/H1, all completed by
2026-09-15 19:30 UTC. The M5 setup opens at 19:20 and its confirmation at
19:25. Context rows are selected by their **close time**, so an unfinished
M15/H1 candle cannot leak into the analysis. Prices and tick volumes are copied
verbatim; only the timeframe column was added and the unused spread column
was omitted. The test supplies the captured 10-price-unit spread and a fixed
clock at the confirmation close.

The unchanged BTC preset produces a sell Trend Pullback with score
0.865134112473406, entry 75861.21, structural stop 76215.83 and stop distance
0.4674589293%. The test exercises real preset application, timeframe resolution,
M15/H1 analysis, strategy selection, EMA/ATR/fractals, confirmation, and persisted
Signal/Decision creation. Dispatch is deferred, so it places no orders. This is
a reachability regression, not a profitability or live-execution claim.

Two separate tests deliberately modify the confirmation candle to check failure
to confirm and a confirmed setup rejected by the unchanged 0.35% minimum stop.
