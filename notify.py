import requests
import os
import re
from datetime import datetime

# ── 配置区，从环境变量读取 ──────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# ── MSTR 持仓常量（来自 strategy.com/shares 官方数据）──────
# 更新日期: 2026-05-25
MSTR_BTC_HOLDINGS = 843_738          # MSTR持有BTC数量
MSTR_DILUTED_SHARES = 381_954_000    # 稀释股本（股）

# ── 数据获取函数 ──────────────────────────────────────

def get_btc_price():
    """Binance 公开 API，无需 key"""
    r = requests.get(
        "https://api.binance.com/api/v3/ticker/24hr",
        params={"symbol": "BTCUSDT"},
        timeout=10,
    )
    r.raise_for_status()
    d = r.json()
    price = float(d["lastPrice"])
    change = float(d["priceChangePercent"])
    volume = float(d["quoteVolume"])
    return price, change, volume


def get_fear_greed():
    """Alternative.me 免费 API"""
    r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
    r.raise_for_status()
    d = r.json()["data"][0]
    return int(d["value"]), d["value_classification"]


def get_mstr_price():
    """Yahoo Finance 非官方 API"""
    url = "https://query1.finance.yahoo.com/v8/finance/chart/MSTR"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    meta = r.json()["chart"]["result"][0]["meta"]
    price = meta["regularMarketPrice"]
    prev = meta.get("previousClose") or meta.get("chartPreviousClose")
    change_pct = (price - prev) / prev * 100
    return price, change_pct


def calc_mnav(mstr_price, btc_price):
    """
    自己算 mNAV，比爬网页稳100倍
    公式：mNAV = MSTR市值 ÷ (持有BTC数量 × BTC价格)
    """
    mstr_market_cap = mstr_price * MSTR_DILUTED_SHARES
    btc_nav = MSTR_BTC_HOLDINGS * btc_price
    return mstr_market_cap / btc_nav


# ── 格式化工具 ─────────────────────────────────────────

def emoji_change(pct):
    if pct >= 5:   return "🚀"
    if pct >= 2:   return "📈"
    if pct >= 0:   return "🟢"
    if pct >= -2:  return "🔴"
    if pct >= -5:  return "📉"
    return "💥"


def emoji_fg(value):
    if value <= 25: return "😱"
    if value <= 45: return "😨"
    if value <= 55: return "😐"
    if value <= 75: return "😏"
    return "🤑"


def mnav_label(v):
    if v < 1.0:  return f"{v:.2f}x 🔵 折价（低于净值）"
    if v < 1.3:  return f"{v:.2f}x 🟡 小幅溢价"
    if v < 1.8:  return f"{v:.2f}x 🟠 中度溢价"
    return           f"{v:.2f}x 🔴 高度溢价，注意风险"


def build_message(btc_price, btc_change, btc_vol,
                  fg_value, fg_label,
                  mstr_price, mstr_change,
                  mnav):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    vol_b = btc_vol / 1e9
    btc_nav_total = MSTR_BTC_HOLDINGS * btc_price / 1e9  # 十亿美元

    return f"""📊 *加密市场快报*
🕐 {now}

━━━━━━━━━━━━━━━
₿ *比特币 BTC*
💰 价格：${btc_price:,.0f}
{emoji_change(btc_change)} 24h 涨跌：{btc_change:+.2f}%
📦 24h 成交量：${vol_b:.1f}B

━━━━━━━━━━━━━━━
{emoji_fg(fg_value)} *恐慌贪婪指数*
📊 数值：{fg_value} / 100
🏷 状态：{fg_label}

━━━━━━━━━━━━━━━
🏦 *MicroStrategy（MSTR）*
💵 股价：${mstr_price:.2f}
{emoji_change(mstr_change)} 日内涨跌：{mstr_change:+.2f}%
🪙 持仓：{MSTR_BTC_HOLDINGS:,} BTC（${btc_nav_total:.1f}B）

━━━━━━━━━━━━━━━
🔬 *mNAV 溢价*
{mnav_label(mnav)}
📎 [查看详情](https://saylortracker.com/?tab=charts&charts=navPremium)

━━━━━━━━━━━━━━━
📌 [BTC 数据](https://www.coinglass.com/) | [恐惧指数](https://www.coinglass.com/pro/i/FearGreedIndex)"""


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }, timeout=10)
    r.raise_for_status()
    return r.json()


# ── 主流程 ────────────────────────────────────────────

def main():
    print("📡 获取比特币价格（Binance）...")
    btc_price, btc_change, btc_vol = get_btc_price()
    print(f"   BTC: ${btc_price:,.0f}  {btc_change:+.2f}%")

    print("📡 获取恐慌贪婪指数（alternative.me）...")
    fg_value, fg_label = get_fear_greed()
    print(f"   F&G: {fg_value} ({fg_label})")

    print("📡 获取微策略股价（Yahoo Finance）...")
    mstr_price, mstr_change = get_mstr_price()
    print(f"   MSTR: ${mstr_price:.2f}  {mstr_change:+.2f}%")

    print("🧮 计算 mNAV...")
    mnav = calc_mnav(mstr_price, btc_price)
    print(f"   mNAV: {mnav:.3f}x")

    msg = build_message(btc_price, btc_change, btc_vol,
                        fg_value, fg_label,
                        mstr_price, mstr_change,
                        mnav)

    print("\n📨 发送 Telegram 消息...")
    result = send_telegram(msg)
    print(f"   ✅ 发送成功！message_id: {result['result']['message_id']}")


if __name__ == "__main__":
    main()
