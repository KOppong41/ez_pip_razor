from prometheus_client import Counter, Gauge, Histogram

# Ingest
signals_ingested_total = Counter(
    "signals_ingested_total", "Total signals ingested", ["source", "symbol", "timeframe"]
)

# Decisions
decisions_total = Counter(
    "decisions_total", "Decisions by action", ["action"]
)

# Orders
orders_created_total = Counter(
    "orders_created_total", "Orders created from decisions", ["broker", "symbol", "side"]
)
order_status_total = Counter(
    "order_status_total", "Order status transitions", ["status"]
)

# Tasks / errors
task_failures_total = Counter(
    "task_failures_total", "Background task failures", ["task"]
)

open_positions_gauge = Gauge(
    "open_positions", "Open positions count"
)

# MT5 connectivity/errors
mt5_errors_total = Counter(
    "mt5_errors_total",
    "MT5 errors by action",
    ["action"],  # e.g. initialize, login, symbol_select, copy_rates, ipc
)

execution_latency_seconds = Histogram(
    "execution_latency_seconds",
    "End-to-end order execution latency by stage",
    ["stage"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)
