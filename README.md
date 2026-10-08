<div align="center">

# web-backend

**vnpy-domestic 的 Web 监控后端（只读）**

![Python](https://img.shields.io/badge/python-3.10+-blue?style=flat-square&logo=python)
![FastAPI](https://img.shields.io/badge/fastapi-0.100+-009688?style=flat-square)
![SQLAlchemy](https://img.shields.io/badge/sqlalchemy-2.0+-d71f00?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-lightgrey?style=flat-square)

</div>

---

## 📖 简介

`web-backend` 是一个 FastAPI + SQLAlchemy 2.0 服务，把交易进程写入的监控数据以只读 REST API + WebSocket 推流的方式暴露给前端。

交易进程（[vnpy-domestic](https://github.com/practicemakeperfect666/vnpy-domestic)）用 `MonitorWriter` 把数据写进 `monitor.db`；本服务只读这个 SQLite 文件，自带一份表结构副本（`models.py`，与写侧同步），不 import vnpy-domestic、不接 CTP、不碰交易进程——交易挂了监控照样查历史。

## 🏗️ 架构

```
[交易进程 vnpy-domestic]              [本服务]
    │ MonitorWriter 写库                │
    ▼                                    ▼
 monitor.db ──────────────────> FastAPI（REST + WS）
   (SQLite, WAL)             只读        │
                                         ▼
                              web-frontend（Vue 3）
```

## ✨ 特性

- 18 个只读 REST 接口：账户 / 持仓 / 订单 / 成交 / 盈亏 / 策略 / 净值 / K 线 / 日志 / 系统指标 / 合约
- WebSocket `/ws` 每 3 秒增量推送（id 游标，只推新行）
- 双账户感知（`real` / `simnow`），通过 `account` 查询参数过滤
- SQLite WAL 模式 + `busy_timeout`，与写进程安全并发
- demo 数据生成器，无实盘账户也能本地联调

## 📦 环境要求

- Python ≥ 3.10

## 🚀 安装

```bash
cd web-backend
pip install -e .
```

## ▶️ 运行

```bash
# 指向交易进程写的 monitor.db
export MONITOR_DB_PATH="/path/to/monitor.db"

python -m uvicorn web_backend.main:app --host 127.0.0.1 --port 8000
```

交互式 API 文档：http://127.0.0.1:8000/docs

### 造 demo 数据（无需实盘账户）

```bash
python -m web_backend.seed_demo /path/to/monitor.db
```

## 📡 API 概览

所有接口支持可选 `account`（部分支持 `strategy`）查询参数过滤；不传则返回全部账户。

| Method | Path | 说明 |
| :----- | :---------------------- | :--------------------------------------------- |
| GET    | `/api/account/latest`   | 每个账户最新资金快照 |
| GET    | `/api/positions`        | 最新策略持仓 |
| GET    | `/api/account-positions`| 最新 CTP 账户级持仓 |
| GET    | `/api/account-intraday-pnl`| 账户当日已实现/浮动盈亏 |
| GET    | `/api/strategies`       | 最新策略状态 + 已实现/浮动盈亏 |
| GET    | `/api/orders`           | 最近订单（按订单号去重） |
| GET    | `/api/orders/stats`     | 今日订单状态汇总 |
| GET    | `/api/orders/daily_freq`| 按日 + 账户 + 策略的下单/撤单频次 |
| GET    | `/api/trades`           | 最近成交 |
| GET    | `/api/daily_pnl`        | 策略每日盈亏历史 |
| GET    | `/api/logs`             | 最近策略/引擎日志 |
| GET    | `/api/system/latest`    | 最新 CPU/内存/磁盘/运行时长 |
| GET    | `/api/time`             | 服务器北京时间 |
| GET    | `/api/klines`           | K 线（1m 基准，聚合 3/5/15/30/60m） |
| GET    | `/api/stats/summary`    | 策略成交回合统计（胜率/盈亏比等） |
| GET    | `/api/equity`           | 净值曲线（日频/日内） |
| GET    | `/api/account/margin_series` | 账户资金/保证金序列 |
| GET    | `/api/contract`         | 合约信息（按 symbol） |
| WS     | `/ws`                   | 每 3 秒增量推送（id 游标） |

## 📁 项目结构

```
web-backend/
├── pyproject.toml
└── web_backend/
    ├── main.py        # FastAPI 入口 + lifespan
    ├── api.py         # REST 接口
    ├── ws.py          # WebSocket 广播
    ├── db.py          # 引擎 + session 工厂
    ├── models.py      # monitor.db 表结构（读侧副本）
    └── seed_demo.py   # demo 造数
```

> `models.py` 是表结构读侧副本，须与 `vnpy-domestic/vnpy_domestic/monitor/models.py`（写侧）保持同步；本服务不 import vnpy-domestic。

## 🔒 安全说明

无鉴权、CORS 全开（`allow_origins=["*"]`）。本地自用请绑 `127.0.0.1`；要暴露公网必须套反向代理加认证——数据包含账户资金、持仓、成交。

## 🔗 相关项目

- [vnpy-domestic](https://github.com/practicemakeperfect666/vnpy-domestic) —— 写 `monitor.db` 的交易核心
- [web-frontend](https://github.com/practicemakeperfect666/web-frontend) —— 消费本服务的 Vue 3 面板

## 📄 License

本项目采用 [MIT License](./LICENSE)。

```
MIT License

Copyright (c) 2026 web-backend contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
