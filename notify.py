import requests
import os
import re
from datetime import datetime

# ── 配置区，从环境变量读取 ──────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# ── 数据获取函数 ──────────────────────────────────────

def get_btc_price():
    """换成 CoinGecko 公开 API，彻底解决 GitHub Actions 的 451 锁 IP 问题"""
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "ids": "bitcoin",
        "order": "market_cap_desc",
        "per_page": 1,
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "24h"
    }
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    d = r.json()[0]
    
    price = float(d["current_price"])
    change = float(d["price_change_percentage_24h"])
    volume = float(d["total_volume"])  # USD 计价成交量
    return price, change, volume


def get_fear_greed():
    """Alternative.me 免费 API，行业标准"""
    r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
    r.raise_for_status()
    d = r.json()["data"][0]
    value = int(d["value"])
    label = d["value_classification"]
    return value, label


def get_mstr_price():
    """Yahoo Finance 非官方 API，免费无 key"""
    url = "https://query1.finance.yahoo.com/v8/finance/chart/MSTR"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    meta = r.json()["chart"]["result"][0]["meta"]
    price = meta["regularMarketPrice"]
    prev_close = meta.get("previousClose") or meta.get("chartPreviousClose")
    change_pct = (price - prev_close) / prev_close * 100
    return price, change_pct


def get_mnav():
    """从 saylortracker 抓取最新 mNAV 数值"""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        # 先尝试抓主页，查找 mNAV 数值
        r = requests.get("https://saylortracker.com/", headers=headers, timeout=15)
        text = r.text

        # 找 "0.98x" 格式或 JSON 格式的 navPremium
        m = re.search(r'"navPremium"\s*:\s*([\d.]+)', text)
        if m:
            return float(m.group(1))

        m = re.search(r'([\d.]+)x\s*(?:Multiple to Net Asset|mNAV)', text)
        if m:
            return float(m.group(1))

    except Exception:
        pass
    return None


# ── 格式化工具 ─────────────────────────────────────────

def emoji_change(pct):
    if pct >= 5:   return "🚀"
    if pct >= 2:   return "📈"
    if pct >= 0:   return "🟢"
    if pct >= -2:  return "🔴"
    if pct >= -5:  return "📉"
    return "💥"


def emoji_fg(value):
    if value <= 25: return "😱"   # 极度恐惧
    if value <= 45: return "😨"   # 恐惧
    if value <= 55: return "😐"   # 中性
    if value <= 75: return "😏"   # 贪婪
    return "🤑"                   # 极度贪婪


def mnav_label(v):
    if v is None:  return "⚠️ 获取失败"
    if v < 1.0:    return f"{v:.2f}x 🔵 折价中（低于净值，相对便宜）"
    if v < 1.3:    return f"{v:.2f}x 🟡 小幅溢价"
    if v < 1.8:    return f"{v:.2f}x 🟠 中度溢价"
    return             f"{v:.2f}x 🔴 高度溢价，注意风险"


def build_message(btc_price, btc_change, btc_vol,
                  fg_value, fg_label,
                  mstr_price, mstr_change,
                  mnav):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    vol_b = btc_vol / 1e9

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
    print("📡 获取比特币价格（CoinGecko）...")
    btc_price, btc_change, btc_vol = get_btc_price()
    print(f"   BTC: ${btc_price:,.0f}  {btc_change:+.2f}%")

    print("📡 获取恐慌贪婪指数（alternative.me）...")
    fg_value, fg_label = get_fear_greed()
    print(f"   F&G: {fg_value} ({fg_label})")

    print("📡 获取微策略股价（Yahoo Finance）...")
    mstr_price, mstr_change = get_mstr_price()
    print(f"   MSTR: ${mstr_price:.2f}  {mstr_change:+.2f}%")

    print("📡 获取 mNAV（saylortracker）...")
    mnav = get_mnav()
    print(f"   mNAV: {mnav}")

    msg = build_message(btc_price, btc_change, btc_vol,
                        fg_value, fg_label,
                        mstr_price, mstr_change,
                        mnav)

    print("\n📨 发送 Telegram 消息...")
    result = send_telegram(msg)
    print(f"   ✅ 发送成功！message_id: {result['result']['message_id']}")


if __name__ == "__main__":
    main()
