import requests
import os
import re
import json
from datetime import datetime

TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# MSTR 在 SEC EDGAR 的 CIK（固定不变）
MSTR_CIK = "0001050446"

# 兜底值（SEC API 挂了时用）
FALLBACK_BTC_HOLDINGS   = 843_738
FALLBACK_SHARES_BASIC   = 351_696_000
FALLBACK_SHARES_DILUTED = 381_954_000

# SEC EDGAR 要求 User-Agent 带联系方式
SEC_HEADERS = {
    "User-Agent": "CryptoAlertBot contact@example.com",
    "Accept": "application/json",
}

# ── 数据获取 ──────────────────────────────────────────

def get_btc_price():
    """Kraken 公开 API，无地区限制"""
    r = requests.get(
        "https://api.kraken.com/0/public/Ticker",
        params={"pair": "XBTUSD"}, timeout=10,
    )
    r.raise_for_status()
    d = r.json()["result"]["XXBTZUSD"]
    price      = float(d["c"][0])
    vol24h     = float(d["v"][1])
    open24h    = float(d["o"])
    change_pct = (price - open24h) / open24h * 100
    return price, change_pct, vol24h * price


def get_fear_greed():
    """Alternative.me 免费 API"""
    r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
    r.raise_for_status()
    d = r.json()["data"][0]
    return int(d["value"]), d["value_classification"]


def get_mstr_price():
    """Yahoo Finance v8 chart，稳定无需认证"""
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(
        "https://query1.finance.yahoo.com/v8/finance/chart/MSTR",
        headers=headers, timeout=10,
    )
    r.raise_for_status()
    meta = r.json()["chart"]["result"][0]["meta"]
    price = meta["regularMarketPrice"]
    prev  = meta.get("previousClose") or meta.get("chartPreviousClose")
    return price, (price - prev) / prev * 100


def get_shares_from_sec():
    """
    从 SEC EDGAR CompanyConcept API 拿最新股本数据
    完全免费，官方数据，永远不需要 key
    MSTR CIK: 0001050446
    """
    try:
        url = f"https://data.sec.gov/api/xbrl/companyconcept/{MSTR_CIK}/us-gaap/CommonStockSharesOutstanding.json"
        r = requests.get(url, headers=SEC_HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()

        # 拿 shares 单位下的所有申报记录
        facts = data["units"]["shares"]

        # 只要 10-Q 或 10-K 的最新记录（过滤掉修正版和早期文件）
        quarterly = [
            f for f in facts
            if f.get("form") in ("10-Q", "10-K") and f.get("val", 0) > 100_000_000
        ]

        if quarterly:
            # 按申报日期排序取最新
            latest = sorted(quarterly, key=lambda x: x["filed"], reverse=True)[0]
            shares_basic = latest["val"]
            print(f"   SEC EDGAR 股本: {shares_basic:,}（{latest['filed']} {latest['form']}）")
            return shares_basic

    except Exception as e:
        print(f"   SEC EDGAR 股本获取失败: {e}，使用兜底值")

    return FALLBACK_SHARES_BASIC


def get_btc_holdings_from_sec():
    """
    从 SEC EDGAR 最新 8-K 文件抓 MSTR BTC 持仓
    Strategy 每次买币都要向 SEC 提交 8-K，这里直接读最新一份
    """
    try:
        # 1. 拿最近的 8-K 提交列表
        url = f"https://data.sec.gov/submissions/CIK{MSTR_CIK}.json"
        r = requests.get(url, headers=SEC_HEADERS, timeout=15)
        r.raise_for_status()
        filings = r.json()["filings"]["recent"]

        forms       = filings["form"]
        accessions  = filings["accessionNumber"]
        dates       = filings["filingDate"]

        # 找最近的 8-K
        for i, form in enumerate(forms):
            if form == "8-K":
                accession = accessions[i].replace("-", "")
                date      = dates[i]

                # 2. 拿这份 8-K 的文件列表
                idx_url = f"https://www.sec.gov/Archives/edgar/data/1050446/{accession}/{accessions[i]}-index.json"
                r2 = requests.get(idx_url, headers=SEC_HEADERS, timeout=10)
                if r2.status_code != 200:
                    continue

                # 3. 找主文档（.htm 文件）
                files = r2.json().get("directory", {}).get("item", [])
                htm_file = next(
                    (f["name"] for f in files if f["name"].endswith(".htm") and "8-k" in f["name"].lower()),
                    None
                )
                if not htm_file:
                    htm_file = next((f["name"] for f in files if f["name"].endswith(".htm")), None)

                if not htm_file:
                    continue

                # 4. 下载文件正文，找 BTC 持仓数字
                doc_url = f"https://www.sec.gov/Archives/edgar/data/1050446/{accession}/{htm_file}"
                r3 = requests.get(doc_url, headers=SEC_HEADERS, timeout=15)
                text = r3.text

                # 找 "hodl X BTC" 或 "holds X bitcoin" 等格式
                patterns = [
                    r'hodl\s+([\d,]+)\s*(?:\$\s*)?BTC',
                    r'holds?\s+([\d,]+)\s+bitcoin',
                    r'total.*?([\d,]{6,})\s+bitcoin',
                    r'([\d,]{6,})\s+BTC\s+acquired',
                    r'aggregate.*?([\d,]{6,})\s+bitcoin',
                ]
                for pat in patterns:
                    m = re.search(pat, text, re.IGNORECASE)
                    if m:
                        val = int(m.group(1).replace(",", ""))
                        if 500_000 < val < 5_000_000:
                            print(f"   SEC 8-K BTC持仓: {val:,}（{date}）")
                            return val
                break  # 只试最新一份 8-K

    except Exception as e:
        print(f"   SEC BTC持仓获取失败: {e}，使用兜底值")

    return FALLBACK_BTC_HOLDINGS


# ── 计算指标 ───────────────────────────────────────────

def calc_metrics(mstr_price, shares_basic, btc_holdings, btc_price):
    btc_nav        = btc_holdings * btc_price
    # 稀释股本 = 基础股本 × 1.087（历史稳定比例，保守估算）
    shares_diluted = int(shares_basic * 1.087)
    mstr_mktcap    = mstr_price * shares_diluted
    mnav           = mstr_mktcap / btc_nav
    sats_per_share = int(btc_holdings * 100_000_000 / shares_basic)
    return mnav, sats_per_share, btc_nav


# ── 格式化 ─────────────────────────────────────────────

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
    now   = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    vol_b = btc_vol / 1e9
    nav_b = btc_nav / 1e9

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
    print("📡 获取比特币价格（Kraken）...")
    btc_price, btc_change, btc_vol = get_btc_price()
    print(f"   BTC: ${btc_price:,.0f}  {btc_change:+.2f}%")

    print("📡 获取恐慌贪婪指数...")
    fg_value, fg_label = get_fear_greed()
    print(f"   F&G: {fg_value} ({fg_label})")

    print("📡 获取微策略股价（Yahoo Finance）...")
    mstr_price, mstr_change = get_mstr_price()
    print(f"   MSTR: ${mstr_price:.2f}  {mstr_change:+.2f}%")

    print("📡 获取股本数据（SEC EDGAR）...")
    shares_basic = get_shares_from_sec()
    print(f"   基础股本: {shares_basic:,}")

    print("📡 获取 BTC 持仓（SEC EDGAR 8-K）...")
    btc_holdings = get_btc_holdings_from_sec()
    print(f"   BTC持仓: {btc_holdings:,}")

    print("🧮 计算 mNAV & Sats/Share...")
    mnav, sats_per_share, btc_nav = calc_metrics(
        mstr_price, shares_basic, btc_holdings, btc_price
    )
    print(f"   mNAV: {mnav:.3f}x   Sats/Share: {sats_per_share:,}")

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
