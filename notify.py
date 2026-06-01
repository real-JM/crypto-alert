import requests
import os
from datetime import datetime

# ── 配置区，从环境变量读取 ──────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

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
    return float(d["lastPrice"]), float(d["priceChangePercent"]), float(d["quoteVolume"])


def get_fear_greed():
    """Alternative.me 免费 API"""
    r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
    r.raise_for_status()
    d = r.json()["data"][0]
    return int(d["value"]), d["value_classification"]


def get_mstr_data():
    """
    Yahoo Finance 拿股价 + 股本数据
    sharesOutstanding = 基础股本（用于算 Sats per Share，与官方口径一致）
    """
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    # 实时股价
    r1 = requests.get(
        "https://query1.finance.yahoo.com/v8/finance/chart/MSTR",
        headers=headers, timeout=10,
    )
    r1.raise_for_status()
    meta = r1.json()["chart"]["result"][0]["meta"]
    price = meta["regularMarketPrice"]
    prev  = meta.get("previousClose") or meta.get("chartPreviousClose")
    change_pct = (price - prev) / prev * 100

    # 基础股本 + 市值（用于算 mNAV）
    r2 = requests.get(
        "https://query1.finance.yahoo.com/v10/finance/quoteSummary/MSTR",
        params={"modules": "defaultKeyStatistics,summaryDetail"},
        headers=headers, timeout=10,
    )
    r2.raise_for_status()
    stats = r2.json()["quoteSummary"]["result"][0]

    # sharesOutstanding = 基础流通股（与 strategy.com 基础股本口径最接近）
    shares_basic = stats["defaultKeyStatistics"]["sharesOutstanding"]["raw"]

    # 稀释股本（用于 mNAV，与 saylortracker 一致）
    shares_diluted = stats["defaultKeyStatistics"].get("impliedSharesOutstanding", {}).get("raw") \
                  or stats["defaultKeyStatistics"].get("sharesOutstanding", {}).get("raw")

    return price, change_pct, shares_basic, shares_diluted


def get_mstr_btc_holdings():
    """
    从 strategy.com 官网抓最新 BTC 持仓量
    备用：hardcode 最新已知值
    """
    FALLBACK_BTC = 843_738  # 2026-05-25 官方公告值

    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        r = requests.get("https://www.strategy.com/", headers=headers, timeout=12)
        import re
        # 页面上通常有 "843,738 BTC" 或 JSON 格式
        m = re.search(r'"totalBitcoin"\s*:\s*([\d,]+)', r.text)
        if m:
            return int(m.group(1).replace(",", ""))
        m = re.search(r'([\d,]{6,})\s*BTC', r.text)
        if m:
            val = int(m.group(1).replace(",", ""))
            if 500_000 < val < 5_000_000:  # 合理范围校验
                return val
    except Exception:
        pass

    return FALLBACK_BTC


# ── 计算指标 ─────────────────────────────────────────

def calc_metrics(mstr_price, shares_basic, shares_diluted, btc_holdings, btc_price):
    """
    mNAV   = MSTR稀释市值 ÷ BTC持仓净值
    Sats/Share = BTC持仓 × 1亿sats ÷ 基础股本
    """
    btc_nav        = btc_holdings * btc_price
    mstr_mktcap    = mstr_price * shares_diluted
    mnav           = mstr_mktcap / btc_nav

    sats_per_share = int(btc_holdings * 100_000_000 / shares_basic)

    return mnav, sats_per_share, btc_nav


# ── 格式化工具 ─────────────────────────────────────────

def emoji_change(pct):
    if pct >= 5:  return "🚀"
    if pct >= 2:  return "📈"
    if pct >= 0:  return "🟢"
    if pct >= -2: return "🔴"
    if pct >= -5: return "📉"
    return "💥"

def emoji_fg(v):
    if v <= 25: return "😱"
    if v <= 45: return "😨"
    if v <= 55: return "😐"
    if v <= 75: return "😏"
    return "🤑"

def mnav_label(v):
    if v < 1.0:  return f"{v:.2f}x 🔵 折价（低于净值）"
    if v < 1.3:  return f"{v:.2f}x 🟡 小幅溢价"
    if v < 1.8:  return f"{v:.2f}x 🟠 中度溢价"
    return           f"{v:.2f}x 🔴 高度溢价"


def build_message(btc_price, btc_change, btc_vol,
                  fg_value, fg_label,
                  mstr_price, mstr_change,
                  btc_holdings, btc_nav,
                  mnav, sats_per_share):

    now    = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    vol_b  = btc_vol / 1e9
    nav_b  = btc_nav  / 1e9

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
🪙 持仓：{btc_holdings:,} BTC（${nav_b:.1f}B）

━━━━━━━━━━━━━━━
🔬 *mNAV 溢价*
{mnav_label(mnav)}

🫙 *含币量 Sats/Share*
每股含 {sats_per_share:,} sats
≈ {sats_per_share/100_000_000:.6f} BTC/股
📎 [查看详情](https://saylortracker.com/?tab=charts&charts=navPremium)

━━━━━━━━━━━━━━━
📌 [BTC数据](https://www.coinglass.com/) | [恐惧指数](https://www.coinglass.com/pro/i/FearGreedIndex)"""


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

    print("📡 获取恐慌贪婪指数...")
    fg_value, fg_label = get_fear_greed()
    print(f"   F&G: {fg_value} ({fg_label})")

    print("📡 获取微策略股价+股本（Yahoo Finance）...")
    mstr_price, mstr_change, shares_basic, shares_diluted = get_mstr_data()
    print(f"   MSTR: ${mstr_price:.2f}  {mstr_change:+.2f}%")
    print(f"   基础股本: {shares_basic:,}  稀释股本: {shares_diluted:,}")

    print("📡 获取 MSTR BTC 持仓（strategy.com）...")
    btc_holdings = get_mstr_btc_holdings()
    print(f"   BTC持仓: {btc_holdings:,}")

    print("🧮 计算 mNAV & Sats/Share...")
    mnav, sats_per_share, btc_nav = calc_metrics(
        mstr_price, shares_basic, shares_diluted, btc_holdings, btc_price
    )
    print(f"   mNAV: {mnav:.3f}x")
    print(f"   Sats/Share: {sats_per_share:,}")

    msg = build_message(
        btc_price, btc_change, btc_vol,
        fg_value, fg_label,
        mstr_price, mstr_change,
        btc_holdings, btc_nav,
        mnav, sats_per_share,
    )

    print("\n📨 发送 Telegram 消息...")
    result = send_telegram(msg)
    print(f"   ✅ 发送成功！message_id: {result['result']['message_id']}")


if __name__ == "__main__":
    main()
