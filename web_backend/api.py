"""Read-only REST API for the monitor database.

All endpoints only query monitor.db; the backend never touches the
trading process or CTP. account/strategy query params filter rows;
omit them to return all accounts.
"""

from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import get_db
from .models import (
    Account,
    AccountIntradayPnl,
    AccountPosition,
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

router = APIRouter(prefix="/api")


def _to_dict(obj) -> dict:
    return {
        c.name: getattr(obj, c.name) for c in obj.__table__.columns if c.name != "id"
    }


# Variety code -> Chinese product name. CTP returns the Chinese name only on
# live broker frontends; the simnow test frontend returns the raw code
# (e.g. "v2701"), so we map the variety code extracted from the symbol.
PRODUCT_NAME = {
    # DCE (lowercase)
    "m": "豆粕", "y": "豆油", "a": "豆一", "b": "豆二", "c": "玉米",
    "cs": "玉米淀粉", "p": "棕榈油", "i": "铁矿石", "j": "焦炭", "jm": "焦煤",
    "v": "PVC", "l": "聚乙烯", "pp": "聚丙烯", "eg": "乙二醇", "eb": "苯乙烯",
    "pg": "液化石油气", "rr": "粳米", "jd": "鸡蛋", "lh": "生猪",
    # CZCE (uppercase)
    "SR": "白糖", "TA": "PTA", "MA": "甲醇", "CF": "棉花", "CY": "棉纱",
    "OI": "菜籽油", "RM": "菜粕", "FG": "玻璃", "SA": "纯碱", "UR": "尿素",
    "AP": "苹果", "CJ": "红枣", "PK": "花生", "SF": "硅铁", "SM": "锰硅",
    "PF": "短纤", "PX": "对二甲苯", "PR": "瓶片", "SH": "烧碱",
    # SHFE (lowercase)
    "rb": "螺纹钢", "hc": "热轧卷板", "cu": "铜", "al": "铝", "zn": "锌",
    "pb": "铅", "ni": "镍", "sn": "锡", "au": "黄金", "ag": "白银",
    "ru": "天然橡胶", "bu": "石油沥青", "fu": "燃料油", "sp": "纸浆",
    "ss": "不锈钢", "wr": "线材", "ao": "氧化铝", "br": "丁二烯橡胶",
    # INE (lowercase)
    "sc": "原油", "lu": "低硫燃料油", "nr": "20号胶", "ec": "集运指数", "bc": "国际铜",
    # GFEX (lowercase)
    "si": "工业硅", "lc": "碳酸锂",
    # CFFEX (uppercase)
    "IF": "沪深300", "IH": "上证50", "IC": "中证500", "IM": "中证1000",
    "T": "10年期国债", "TF": "5年期国债", "TS": "2年期国债", "TL": "30年期国债",
}


def _product_name(symbol: str) -> str:
    """Map a vt_symbol to a Chinese product name via the variety code."""
    variety = "".join(ch for ch in symbol.split(".")[0] if ch.isalpha())
    return PRODUCT_NAME.get(variety, variety)


def _trading_day(ts: str | None) -> str:
    """Order submit time string -> trading day (night session >= 20:00 rolls to next day)."""
    if not ts or len(ts) < 13:
        return ""
    d = datetime.strptime(ts[:10], "%Y-%m-%d").date()
    if ts[11:13] >= "20":
        d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


def _current_trading_day() -> str:
    """Beijing now -> trading day string."""
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    d = now.date()
    if now.hour >= 20:
        d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


@router.get("/account/latest")
def account_latest(account: str | None = None, db: Session = Depends(get_db)):
    maxid = (
        select(func.max(Account.id).label("mid"))
        .group_by(Account.account)
        .subquery()
    )
    stmt = select(Account).join(maxid, Account.id == maxid.c.mid)
    if account:
        stmt = stmt.where(Account.account == account)
    rows = db.scalars(stmt).all()

    # 账户已实现盈亏 = 当天平仓盈亏合计（trade_rounds 永久保留，重启/换日不丢）
    today = _current_trading_day()
    realized_map = {
        row.account: (row.total or 0.0)
        for row in db.execute(
            select(TradeRound.account, func.sum(TradeRound.pnl).label("total"))
            .where(TradeRound.trading_day == today)
            .group_by(TradeRound.account)
        ).all()
    }
    result = []
    for r in rows:
        d = _to_dict(r)
        d["realized_pl"] = round(realized_map.get(r.account, 0.0), 2)
        result.append(d)
    return result


@router.get("/positions")
def positions(
    account: str | None = None,
    strategy: str | None = None,
    db: Session = Depends(get_db),
):
    maxid = (
        select(func.max(Position.id).label("mid"))
        .group_by(
            Position.account, Position.strategy, Position.vt_symbol, Position.direction
        )
        .subquery()
    )
    stmt = select(Position).join(maxid, Position.id == maxid.c.mid)
    if account:
        stmt = stmt.where(Position.account == account)
    if strategy:
        stmt = stmt.where(Position.strategy == strategy)
    return [_to_dict(r) for r in db.scalars(stmt).all()]


@router.get("/account-positions")
def account_positions(
    account: str | None = None,
    db: Session = Depends(get_db),
):
    # 每账户最新一次快照的完整持仓（当前 CTP 持仓，非累积历史）
    latest_time = (
        select(
            AccountPosition.account,
            func.max(AccountPosition.snapshot_time).label("t"),
        )
        .group_by(AccountPosition.account)
        .subquery()
    )
    stmt = select(AccountPosition).join(
        latest_time,
        (AccountPosition.account == latest_time.c.account)
        & (AccountPosition.snapshot_time == latest_time.c.t),
    )
    if account:
        stmt = stmt.where(AccountPosition.account == account)
    return [_to_dict(r) for r in db.scalars(stmt).all() if r.volume > 0]


@router.get("/account-intraday-pnl")
def account_intraday_pnl(
    account: str | None = None,
    db: Session = Depends(get_db),
):
    maxid = (
        select(func.max(AccountIntradayPnl.id).label("mid"))
        .group_by(AccountIntradayPnl.account)
        .subquery()
    )
    stmt = select(AccountIntradayPnl).join(maxid, AccountIntradayPnl.id == maxid.c.mid)
    if account:
        stmt = stmt.where(AccountIntradayPnl.account == account)
    rows = db.scalars(stmt).all()

    # realized_pl 改为 trade_rounds 当天 SUM（重启/换日不丢，不依赖内存变量快照）
    today = _current_trading_day()
    realized_map = {
        row.account: (row.total or 0.0)
        for row in db.execute(
            select(TradeRound.account, func.sum(TradeRound.pnl).label("total"))
            .where(TradeRound.trading_day == today)
            .group_by(TradeRound.account)
        ).all()
    }
    result = []
    for r in rows:
        d = _to_dict(r)
        d["realized_pl"] = round(realized_map.get(r.account, 0.0), 2)
        result.append(d)
    return result


@router.get("/strategies")
def strategies(
    account: str | None = None,
    strategy: str | None = None,
    db: Session = Depends(get_db),
):
    maxid = (
        select(func.max(StrategyStatus.id).label("mid"))
        .group_by(StrategyStatus.account, StrategyStatus.strategy)
        .subquery()
    )
    stmt = select(StrategyStatus).join(maxid, StrategyStatus.id == maxid.c.mid)
    if account:
        stmt = stmt.where(StrategyStatus.account == account)
    if strategy:
        stmt = stmt.where(StrategyStatus.strategy == strategy)
    rows = db.scalars(stmt).all()
    result = []
    for r in rows:
        d = _to_dict(r)
        d["name"] = _product_name(r.symbol)
        realized = (
            db.scalar(
                select(func.sum(TradeRound.pnl)).where(
                    TradeRound.account == r.account, TradeRound.strategy == r.strategy
                )
            )
            or 0.0
        )
        floating = d.get("pnl", 0.0) or 0.0
        d["realized_pnl"] = round(realized, 2)
        d["pnl"] = round(realized + floating, 2)
        result.append(d)
    return result


@router.get("/orders")
def orders(
    account: str | None = None,
    strategy: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    maxid = (
        select(func.max(Order.id).label("mid"))
        .group_by(Order.vt_orderid)
        .subquery()
    )
    stmt = (
        select(Order)
        .join(maxid, Order.id == maxid.c.mid)
        .order_by(Order.id.desc())
        .limit(limit)
    )
    if account:
        stmt = stmt.where(Order.account == account)
    if strategy:
        stmt = stmt.where(Order.strategy == strategy)
    # keep the latest status per order id (filled orders hide prior working state)
    return [_to_dict(r) for r in db.scalars(stmt).all()]


@router.get("/orders/stats")
def order_stats(account: str | None = None, db: Session = Depends(get_db)):
    """今日订单状态统计：按 vt_orderid 取最新状态（去重）再按账户分组。

    active（活跃挂单）= 最新状态 ∈ {SUBMITTING, NOTTRADED, PARTTRADED} 的订单数，
    跨天统计（挂单可跨夜，昨日报单未终态仍算当前挂单）。
    total/cancelled/rejected 按今日交易日过滤（夜盘归下一交易日），按唯一订单去重。
    """
    today = _current_trading_day()

    latest_id = (
        select(func.max(Order.id).label("max_id")).group_by(Order.vt_orderid).subquery()
    )
    stmt = (
        select(Order.account, Order.status, Order.submit_time).join(
            latest_id, Order.id == latest_id.c.max_id
        )
    )
    if account:
        stmt = stmt.where(Order.account == account)
    rows = db.execute(stmt).all()

    agg: dict = defaultdict(
        lambda: {
            "active": 0,
            "total": 0,
            "cancelled": 0,
            "rejected": 0,
            "by_status": defaultdict(int),
        }
    )
    for acct, status, submit_time in rows:
        a = agg[acct]
        a["by_status"][status] += 1
        if status in ("SUBMITTING", "NOTTRADED", "PARTTRADED"):
            a["active"] += 1
        if _trading_day(submit_time) == today:
            a["total"] += 1
            if status == "CANCELLED":
                a["cancelled"] += 1
            elif status == "REJECTED":
                a["rejected"] += 1

    result = []
    for acct in sorted(agg):
        a = agg[acct]
        total = a["total"]
        cancelled = a["cancelled"]
        result.append(
            {
                "account": acct,
                "trading_day": today,
                "total": total,
                "active": a["active"],
                "cancelled": cancelled,
                "rejected": a["rejected"],
                "cancel_rate": round(cancelled / total, 4) if total else 0.0,
                "by_status": dict(a["by_status"]),
            }
        )
    return result


@router.get("/orders/daily_freq")
def orders_daily_freq(
    account: str | None = None,
    strategy: str | None = None,
    days: int = 30,
    db: Session = Depends(get_db),
):
    """按日 + 账户 + 策略聚合下单/撤单频次，按 vt_orderid 取最新状态（去重）。

    total = 当日该策略的下单数（唯一订单），cancelled/rejected 按最新终态统计。
    """
    since = (datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(days=days)).strftime(
        "%Y-%m-%d"
    )

    latest_id = (
        select(func.max(Order.id).label("max_id")).group_by(Order.vt_orderid).subquery()
    )
    stmt = (
        select(Order.submit_time, Order.account, Order.strategy, Order.status).join(
            latest_id, Order.id == latest_id.c.max_id
        )
    )
    if account:
        stmt = stmt.where(Order.account == account)
    if strategy:
        stmt = stmt.where(Order.strategy == strategy)
    rows = db.execute(stmt).all()

    agg: dict = defaultdict(lambda: {"total": 0, "cancelled": 0, "rejected": 0})
    for submit_time, acct, strat, status in rows:
        day = _trading_day(submit_time)
        if not day or day < since:
            continue
        key = (day, acct, strat)
        agg[key]["total"] += 1
        if status == "CANCELLED":
            agg[key]["cancelled"] += 1
        elif status == "REJECTED":
            agg[key]["rejected"] += 1

    result = []
    for (day, acct, strat), v in sorted(agg.items()):
        v["cancel_rate"] = round(v["cancelled"] / v["total"], 4) if v["total"] else 0.0
        result.append({"day": day, "account": acct, "strategy": strat, **v})
    return result


@router.get("/trades")
def trades(
    account: str | None = None,
    strategy: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    stmt = select(Trade).order_by(Trade.id.desc()).limit(limit)
    if account:
        stmt = stmt.where(Trade.account == account)
    if strategy:
        stmt = stmt.where(Trade.strategy == strategy)
    return [_to_dict(r) for r in db.scalars(stmt).all()]


@router.get("/daily_pnl")
def daily_pnl(
    account: str | None = None,
    strategy: str | None = None,
    db: Session = Depends(get_db),
):
    stmt = select(DailyPnl).order_by(DailyPnl.trading_day)
    if account:
        stmt = stmt.where(DailyPnl.account == account)
    if strategy:
        stmt = stmt.where(DailyPnl.strategy == strategy)
    return [_to_dict(r) for r in db.scalars(stmt).all()]


@router.get("/logs")
def logs(
    account: str | None = None, limit: int = 200, db: Session = Depends(get_db)
):
    stmt = select(Log).order_by(Log.id.desc()).limit(limit)
    if account:
        stmt = stmt.where(Log.account == account)
    return [_to_dict(r) for r in db.scalars(stmt).all()]


@router.get("/system/latest")
def system_latest(db: Session = Depends(get_db)):
    stmt = select(SystemMetric).order_by(SystemMetric.id.desc()).limit(1)
    r = db.scalars(stmt).first()
    return _to_dict(r) if r else {}


@router.get("/time")
def server_time():
    """服务器当前北京时间"""
    return {
        "datetime": datetime.now(ZoneInfo("Asia/Shanghai")).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    }


@router.get("/klines")
def klines(
    symbol: str | None = None,
    account: str | None = None,
    interval: str = "1m",
    limit: int = 500,
    db: Session = Depends(get_db),
):
    stmt = select(Kline).where(Kline.interval == "1m").order_by(Kline.datetime)
    if symbol:
        stmt = stmt.where(Kline.symbol == symbol)
    if account:
        stmt = stmt.where(Kline.account == account)
    rows = db.scalars(stmt).all()

    n = {"3m": 3, "5m": 5, "15m": 15, "30m": 30, "60m": 60}.get(interval, 1)
    if n == 1:
        return [_to_dict(r) for r in rows[-limit:]]

    result, bucket = [], []
    for r in rows:
        bucket.append(r)
        if len(bucket) == n:
            result.append(_aggregate_bar(bucket, interval))
            bucket = []
    if bucket:
        result.append(_aggregate_bar(bucket, interval))
    return result[-limit:]


def _aggregate_bar(bars, interval: str) -> dict:
    first, last = bars[0], bars[-1]
    return {
        "symbol": first.symbol,
        "exchange": first.exchange,
        "account": first.account,
        "interval": interval,
        "datetime": last.datetime,
        "open": first.open,
        "high": max(b.high for b in bars),
        "low": min(b.low for b in bars),
        "close": last.close,
        "volume": sum(b.volume for b in bars),
    }


def _aggregate_account(rows, freq: str) -> list:
    """Bucket Account snapshots by day (daily) or 15min (intraday), keep last."""
    seen: dict = {}
    for r in rows:
        if freq == "daily":
            key = r.snapshot_time[:10]
        else:
            dt = datetime.strptime(r.snapshot_time, "%Y-%m-%d %H:%M:%S")
            key = dt.replace(minute=dt.minute // 15 * 15, second=0).strftime(
                "%Y-%m-%d %H:%M"
            )
        seen[key] = r
    return [(k, seen[k]) for k in sorted(seen)]


@router.get("/stats/summary")
def stats_summary(
    account: str | None = None,
    strategy: str | None = None,
    db: Session = Depends(get_db),
):
    stmt = select(TradeRound)
    if account:
        stmt = stmt.where(TradeRound.account == account)
    if strategy:
        stmt = stmt.where(TradeRound.strategy == strategy)
    rounds = db.scalars(stmt).all()

    groups: dict = defaultdict(list)
    for r in rounds:
        groups[(r.account, r.strategy)].append(r)

    # 账户权益（收益率分母）：每账户最新一条
    maxid = (
        select(func.max(Account.id).label("mid"))
        .group_by(Account.account)
        .subquery()
    )
    balances: dict = {}
    for b in db.scalars(
        select(Account).join(maxid, Account.id == maxid.c.mid)
    ).all():
        balances[b.account] = b.balance

    def sub(rs, balance: float = 0.0):
        if not rs:
            return {
                "rounds": 0,
                "win_rate": 0,
                "pnl": 0,
                "profit_factor": 0,
                "avg_holding_seconds": 0,
                "return_rate": 0,
            }
        wins = [r for r in rs if r.pnl > 0]
        losses = [r for r in rs if r.pnl <= 0]
        avg_win = sum(r.pnl for r in wins) / len(wins) if wins else 0.0
        avg_loss = abs(sum(r.pnl for r in losses) / len(losses)) if losses else 0.0
        total_pnl = round(sum(r.pnl for r in rs), 2)
        return {
            "rounds": len(rs),
            "win_rate": round(len(wins) / len(rs), 4),
            "pnl": total_pnl,
            "profit_factor": round(avg_win / avg_loss, 2) if avg_loss else round(avg_win, 2),
            "avg_holding_seconds": round(
                sum(r.holding_seconds for r in rs) / len(rs), 2
            ),
            "return_rate": round(total_pnl / balance, 4) if balance else 0.0,
        }

    result = []
    for (acct, strat), rs in groups.items():
        balance = balances.get(acct, 0.0)
        overall = sub(rs, balance)
        trading_days = len({r.trading_day for r in rs})
        result.append(
            {
                "account": acct,
                "strategy": strat,
                "total_rounds": overall["rounds"],
                "avg_trades_per_day": round(
                    overall["rounds"] / trading_days, 2
                )
                if trading_days
                else 0,
                "win_rate": overall["win_rate"],
                "profit_factor": overall["profit_factor"],
                "avg_holding_seconds": overall["avg_holding_seconds"],
                "pnl": overall["pnl"],
                "return_rate": overall["return_rate"],
                "long": sub([r for r in rs if r.direction == "LONG"], balance),
                "short": sub([r for r in rs if r.direction == "SHORT"], balance),
            }
        )
    return result


@router.get("/equity")
def equity(
    account: str | None = None,
    strategy: str | None = None,
    freq: str = "daily",
    db: Session = Depends(get_db),
):
    if strategy:
        if freq == "intraday":
            stmt = select(StrategyIntraday).order_by(StrategyIntraday.id)
            if account:
                stmt = stmt.where(StrategyIntraday.account == account)
            stmt = stmt.where(StrategyIntraday.strategy == strategy)
            rows = db.scalars(stmt).all()
            return [
                {
                    "trading_day": r.snapshot_time[:16],
                    "account": r.account,
                    "strategy": r.strategy,
                    "equity": r.equity,
                }
                for r in rows
            ]
        stmt = select(DailyPnl).order_by(DailyPnl.trading_day, DailyPnl.id)
        if account:
            stmt = stmt.where(DailyPnl.account == account)
        stmt = stmt.where(DailyPnl.strategy == strategy)
        rows = db.scalars(stmt).all()
        result, cum = [], 0.0
        for r in rows:
            cum += r.realized_pl
            result.append(
                {
                    "trading_day": r.trading_day,
                    "account": r.account,
                    "strategy": r.strategy,
                    "equity": round(cum, 2),
                }
            )
        return result

    # 账户净值 = 动态权益 balance + 浮动盈亏（日频/日内聚合）
    stmt = select(Account).order_by(Account.id)
    if account:
        stmt = stmt.where(Account.account == account)
    rows = db.scalars(stmt).all()
    return [
        {"trading_day": key, "account": r.account, "equity": r.balance + (r.pnl or 0.0)}
        for key, r in _aggregate_account(rows, freq)
    ]


@router.get("/account/margin_series")
def margin_series(
    account: str | None = None,
    freq: str = "daily",
    db: Session = Depends(get_db),
):
    stmt = select(Account).order_by(Account.id)
    if account:
        stmt = stmt.where(Account.account == account)
    rows = db.scalars(stmt).all()
    return [
        {
            "trading_day": key,
            "account": r.account,
            "balance": r.balance,
            "margin": r.margin,
            "margin_pct": round(r.margin / r.balance * 100, 2) if r.balance else 0.0,
        }
        for key, r in _aggregate_account(rows, freq)
    ]


@router.get("/contract")
def contract(symbol: str, db: Session = Depends(get_db)):
    row = db.get(Contract, symbol)
    if row is None:
        return {
            "symbol": symbol,
            "product": _product_name(symbol),
            "exchange": "",
            "name": "",
            "size": 0,
            "pricetick": 0,
            "margin_rate": 0,
        }
    return {
        "symbol": row.symbol,
        "product": _product_name(row.symbol),
        "exchange": row.exchange,
        "name": row.name,
        "size": row.size,
        "pricetick": row.pricetick,
        "margin_rate": 0,  # CTP does not expose a per-contract margin rate
    }
