"""Seed demo data (real + simnow) into monitor.db for local testing.

Usage:
    python -m web_backend.seed_demo [db_path]

If db_path is omitted, reads MONITOR_DB_PATH env or defaults to ~/vnpy-data/monitor.db.
"""

import os
import sys
from datetime import datetime, timedelta

db_path = (
    sys.argv[1]
    if len(sys.argv) > 1
    else os.getenv(
        "MONITOR_DB_PATH",
        os.path.join(os.path.expanduser("~"), "vnpy-data", "monitor.db"),
    )
)
os.environ["MONITOR_DB_PATH"] = db_path

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from web_backend import (  # noqa: E402
    Account,
    AccountIntradayPnl,
    AccountPosition,
    Base,
    Contract,
    DailyPnl,
    Kline,
    Log,
    Order,
    Position,
    StrategyIntraday,
    StrategyStatus,
    SystemMetric,
    Trade,
    TradeRound,
)

STRATEGIES = [
    ("MultiLayer_m", "m2701.DCE", "DCE"),
    ("MultiLayer_SR", "SR701.CZCE", "CZCE"),
    ("MultiLayer_rb", "rb2610.SHFE", "SHFE"),
    ("MultiLayer_TA", "TA701.CZCE", "CZCE"),
    ("MultiLayer_v", "v2701.DCE", "DCE"),
]
ACCOUNTS = [("real", "60012345"), ("simnow", "089270")]
CONTRACTS = {
    "m2701.DCE": ("DCE", "豆粕", "m", 10, 1),
    "SR701.CZCE": ("CZCE", "白糖", "SR", 10, 1),
    "rb2610.SHFE": ("SHFE", "螺纹钢", "rb", 10, 1),
    "TA701.CZCE": ("CZCE", "PTA", "TA", 5, 2),
    "v2701.DCE": ("DCE", "PVC", "v", 5, 1),
}


def seed() -> None:
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.now().strftime("%Y-%m-%d")

    for sym, (exch, name, product, size, pricetick) in CONTRACTS.items():
        session.add(
            Contract(
                symbol=sym,
                exchange=exch,
                name=name,
                product=product,
                size=size,
                pricetick=pricetick,
                update_time=now,
            )
        )

    for account, accountid in ACCOUNTS:
        session.add(
            Account(
                account=account,
                accountid=accountid,
                balance=1000000,
                available=500000,
                frozen=0,
                margin=200000,
                pnl=15000,
                snapshot_time=now,
            )
        )

        for i, (sname, sym, exch) in enumerate(STRATEGIES):
            direction = "LONG" if i % 2 == 0 else "SHORT"
            pos = 2 + i % 3
            session.add(
                StrategyStatus(
                    strategy=sname,
                    account=account,
                    symbol=sym,
                    status="running",
                    pos=pos if direction == "LONG" else -pos,
                    long_pos=pos if direction == "LONG" else 0,
                    short_pos=pos if direction == "SHORT" else 0,
                    parameters={"layers": 6, "step": 5},
                    variables={"bars": 1024},
                    update_time=now,
                )
            )
            session.add(
                Position(
                    strategy=sname,
                    account=account,
                    vt_symbol=sym,
                    direction=direction,
                    volume=pos,
                    avg_price=3100 + i * 50,
                    pnl=200 + i * 100,
                    trading_day=today,
                    snapshot_time=now,
                )
            )
            session.add(
                TradeRound(
                    strategy=sname,
                    symbol=sym,
                    exchange=exch,
                    account=account,
                    direction=direction,
                    entry_price=3100 + i * 50,
                    exit_price=3200 + i * 50,
                    volume=pos,
                    pnl=100 * pos * 10,
                    holding_seconds=3600,
                    open_time="2026-09-20 09:00:00",
                    close_time="2026-09-20 10:00:00",
                    trading_day="2026-09-20",
                )
            )
            session.add(
                AccountPosition(
                    account=account,
                    accountid=accountid,
                    vt_symbol=sym,
                    direction=direction,
                    volume=pos,
                    price=3100 + i * 50,
                    pnl=200 + i * 100,
                    snapshot_time=now,
                )
            )
            oid = f"{account}-{sname}-1"
            session.add(
                Order(
                    vt_orderid=oid,
                    strategy=sname,
                    account=account,
                    symbol=sym,
                    exchange=exch,
                    direction=direction,
                    offset="OPEN",
                    price=3100 + i * 50,
                    volume=pos,
                    traded=pos,
                    status="ALLTRADED",
                    submit_time=now,
                    delay_ms=5,
                )
            )
            session.add(
                Trade(
                    vt_tradeid=f"{oid}-t",
                    vt_orderid=oid,
                    strategy=sname,
                    account=account,
                    symbol=sym,
                    exchange=exch,
                    price=3100 + i * 50,
                    volume=pos,
                    slippage=1,
                    delay_ms=5,
                    trade_time=now,
                )
            )

        for d in range(30):
            day = (datetime.now() - timedelta(days=29 - d)).strftime("%Y-%m-%d")
            for sname, _, _ in STRATEGIES:
                realized = round(100 - (d % 5) * 50 + (ord(sname[0]) % 7) * 20, 2)
                session.add(
                    DailyPnl(
                        trading_day=day,
                        strategy=sname,
                        account=account,
                        realized_pl=realized,
                        cumulative_pl=realized,
                    )
                )

        for i, (sname, sym, _) in enumerate(STRATEGIES):
            for m in range(3):
                ts = (datetime.now() - timedelta(minutes=15 * (2 - m))).strftime(
                    "%Y-%m-%d %H:%M:00"
                )
                session.add(
                    StrategyIntraday(
                        strategy=sname,
                        account=account,
                        symbol=sym,
                        realized_pl=100 + i * 20,
                        floating_pl=200 + i * 30,
                        equity=300 + i * 50,
                        snapshot_time=ts,
                    )
                )

        session.add(
            AccountIntradayPnl(
                account=account,
                accountid=accountid,
                trading_day=today,
                realized_pl=15000,
                floating_pl=5000,
                balance=1000000,
                snapshot_time=now,
            )
        )

        for i, (sname, sym, exch) in enumerate(STRATEGIES):
            base = 3100 + i * 50
            for m in range(120):
                ts = (datetime.now() - timedelta(minutes=119 - m)).strftime(
                    "%Y-%m-%d %H:%M:00"
                )
                o = base + (m % 7) - 3
                c = o + (m % 3) - 1
                session.add(
                    Kline(
                        symbol=sym,
                        exchange=exch,
                        account=account,
                        interval="1m",
                        datetime=ts,
                        open=o,
                        high=o + 3,
                        low=o - 3,
                        close=c,
                        volume=100 + m,
                    )
                )

        for lvl, msg in [
            ("INFO", "策略启动"),
            ("INFO", "CTP 连接成功"),
            ("ERROR", "订单被拒 m2701.DCE price=3100"),
        ]:
            session.add(Log(ts=now, level=lvl, strategy="", account=account, message=msg))
        session.add(SystemMetric(ts=now, cpu=23.5, mem=45.2, disk=30.1, uptime="12h 30m"))

    session.commit()
    print(f"seed 完成 → {db_path}")


if __name__ == "__main__":
    seed()
