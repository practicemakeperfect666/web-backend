"""WebSocket broadcaster: push snapshots to all connected clients every 3 seconds.

Reads monitor.db on a 3s cadence; skips querying when no client is
connected. Incremental pull: keeps the latest per-key state in memory and
each tick fetches only new rows (id > cursor) instead of a full table scan.
trade_rounds realized P&L is small, so it is still re-aggregated each tick.

Note: incremental pull cannot observe row deletions. If a retention/cleanup
job ever prunes old snapshot rows, this in-memory state must be reset.
"""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select

from .db import SessionLocal
from .models import Account, AccountIntradayPnl, AccountPosition, StrategyStatus, SystemMetric, TradeRound
from .api import _current_trading_day

router = APIRouter()

active: set = set()

# ── in-memory latest state + per-table id cursors (None = not initialized) ──
_state = {
    "accounts": {},      # account -> dict
    "strategies": {},    # (account, strategy) -> dict
}
_cursor = {"accounts": None, "strategies": None}


def _to_dict(obj) -> dict:
    return {
        c.name: getattr(obj, c.name) for c in obj.__table__.columns if c.name != "id"
    }


def _sync(model, key_cols, bucket, keyfn) -> None:
    """Pull new rows for one table and merge the latest per key into state."""
    cursor = _cursor[bucket]
    with SessionLocal() as db:
        if cursor is None:
            # first run: load only the latest row per key
            maxid = select(func.max(model.id)).group_by(
                *[getattr(model, c) for c in key_cols]
            )
            rows = db.scalars(select(model).where(model.id.in_(maxid))).all()
        else:
            rows = db.scalars(
                select(model).where(model.id > cursor).order_by(model.id.asc())
            ).all()
    if not rows:
        return
    _cursor[bucket] = max(r.id for r in rows)
    for r in rows:
        _state[bucket][keyfn(r)] = _to_dict(r)


def build_snapshot() -> dict:
    _sync(Account, ["account"], "accounts", lambda r: r.account)
    _sync(
        StrategyStatus,
        ["account", "strategy"],
        "strategies",
        lambda r: (r.account, r.strategy),
    )

    with SessionLocal() as db:
        # 账户持仓 = 每账户最新一次快照的完整集合（当前 CTP 持仓，非累积）
        latest_time = (
            select(
                AccountPosition.account,
                func.max(AccountPosition.snapshot_time).label("t"),
            )
            .group_by(AccountPosition.account)
            .subquery()
        )
        pos_rows = db.scalars(
            select(AccountPosition).join(
                latest_time,
                (AccountPosition.account == latest_time.c.account)
                & (AccountPosition.snapshot_time == latest_time.c.t),
            )
        ).all()
        positions = [_to_dict(r) for r in pos_rows if r.volume > 0]

        realized_rows = db.execute(
            select(
                TradeRound.account, TradeRound.strategy, func.sum(TradeRound.pnl)
            ).group_by(TradeRound.account, TradeRound.strategy)
        ).all()
        # 账户已实现盈亏 = 当天平仓盈亏合计（trade_rounds 永久保留，重启/换日不丢）
        account_realized_rows = db.execute(
            select(TradeRound.account, func.sum(TradeRound.pnl).label("total"))
            .where(TradeRound.trading_day == _current_trading_day())
            .group_by(TradeRound.account)
        ).all()
        sys_row = db.scalar(
            select(SystemMetric).order_by(SystemMetric.id.desc()).limit(1)
        )
    realized = {(a, s): (p or 0.0) for a, s, p in realized_rows}
    account_realized = {a: (t or 0.0) for a, t in account_realized_rows}

    def with_pnl(d):
        floating = d.get("pnl", 0.0) or 0.0
        realized_pnl = realized.get((d["account"], d.get("strategy", "")), 0.0)
        d = dict(d)
        d["realized_pnl"] = round(realized_pnl, 2)
        d["pnl"] = round(realized_pnl + floating, 2)
        return d

    def with_account_realized(d):
        d = dict(d)
        d["realized_pl"] = round(account_realized.get(d["account"], 0.0), 2)
        return d

    return {
        "type": "snapshot",
        "data": {
            "accounts": [with_account_realized(d) for d in _state["accounts"].values()],
            "positions": positions,
            "strategies": [with_pnl(d) for d in _state["strategies"].values()],
            "system": _to_dict(sys_row) if sys_row else None,
        },
    }


async def broadcast_loop() -> None:
    while True:
        await asyncio.sleep(3)
        if not active:
            continue
        snapshot = build_snapshot()
        for ws in list(active):
            try:
                await ws.send_json(snapshot)
            except Exception:
                active.discard(ws)


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    active.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        active.discard(ws)
