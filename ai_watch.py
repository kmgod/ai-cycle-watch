#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai_watch.py — AI 사이클 관제 시트 일일 수집기
네오클라우드 / 사모크레딧 / 금리 / GPU 임대료 관련 공개 데이터를 매일 수집해
Markdown 브리핑과 CSV 시계열로 저장한다.

사용법:
    python ai_watch.py              # 오늘자 수집
    python ai_watch.py --selftest   # 각 소스 접근 가능 여부만 점검
    python ai_watch.py --days 3     # 최근 3일치 뉴스까지 포함

출력:
    ./out/brief_YYYY-MM-DD.md   당일 브리핑 (읽는 용도)
    ./out/rates.csv             금리 시계열 누적 (추세 확인용)
    ./out/seen.json             중복 방지용 기록

필요 패키지: requests, feedparser
    pip install requests feedparser
"""

import os, re, sys, json, time, csv, argparse
from datetime import datetime, timedelta, timezone

try:
    import requests, feedparser
except ImportError:
    sys.exit("필요 패키지 설치: pip install requests feedparser")

# ─────────────────────────────────────────────────────────
# 설정 — 이메일은 SEC 규정상 반드시 본인 것으로 바꿀 것
# ─────────────────────────────────────────────────────────
# 우선순위: 환경변수 WATCH_EMAIL > 아래 기본값
# GitHub Actions에서는 Secrets에 WATCH_EMAIL을 등록하면 자동 적용됨
MY_EMAIL = os.environ.get("WATCH_EMAIL", "kmgod@users.noreply.github.com")
UA = {"User-Agent": f"AI-Cycle-Watch/1.0 ({MY_EMAIL})"}
OUT = os.environ.get("WATCH_OUT") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT, exist_ok=True)

# 감시 대상 기업 (SEC CIK) — 네오클라우드 및 관련
COMPANIES = {
    "CoreWeave":        "0001769628",
    "Nebius":           "0001739104",
    "IREN":             "0001878848",
    "Cipher Mining":    "0001819989",
    "TeraWulf":         "0001083301",
    "Applied Digital":  "0001144879",
    "Micron":           "0000723125",
    "SanDisk":          "0002012383",
}
# 관심 공시 유형
FORMS = {"8-K", "10-Q", "10-K", "424B5", "S-3", "FWP"}   # 424B5/FWP = 채권·증자 발행

# FRED 시계열 (금리·신용) — 무료, 키 불필요
FRED = {
    "DGS30":            "미국채 30년",
    "DGS10":            "미국채 10년",
    "BAMLH0A0HYM2":     "하이일드 스프레드(OAS)",
    "BAMLC0A4CBBB":     "BBB 회사채 스프레드",
    "DTWEXBGS":         "달러지수(광의)",
}

# 뉴스 키워드 — 대시보드 판정선에 대응
QUERIES = {
    "네오클라우드 자금조달": '(CoreWeave OR Nebius OR "neocloud") (debt OR financing OR "credit facility" OR notes)',
    "데이터센터 ABS":       '"data center" (ABS OR securitization OR "asset-backed")',
    "사모크레딧 부실":       '"private credit" (default OR distress OR writedown OR "mark down")',
    "GPU 임대료":           '(GPU OR H100 OR B200) (rental OR "rental rate" OR lease OR futures)',
    "AI 설비투자 가이던스":  '(hyperscaler OR "capital expenditure") AI (guidance OR cut OR raise)',
    "엔비디아 금융플랫폼":   'Nvidia ("$500 billion" OR financing platform OR backstop)',
    "전력·인허가 병목":      '"data center" (permit OR moratorium OR "grid interconnection" OR "power constraint")',
}

SEC_PAUSE = 0.2   # SEC 요청 간 최소 간격 (초당 10건 제한 준수)


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}")


def load_seen():
    p = os.path.join(OUT, "seen.json")
    if os.path.exists(p):
        try:
            return set(json.load(open(p, encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_seen(seen):
    # 최근 3000건만 유지
    json.dump(sorted(seen)[-3000:], open(os.path.join(OUT, "seen.json"), "w", encoding="utf-8"))


# ─────────────────────────────────────────────────────────
# 1) SEC 공시
# ─────────────────────────────────────────────────────────
def fetch_filings(days=1):
    rows, errors = [], []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    for name, cik in COMPANIES.items():
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        try:
            r = requests.get(url, headers=UA, timeout=20)
            time.sleep(SEC_PAUSE)
            if r.status_code != 200:
                errors.append(f"{name}: HTTP {r.status_code}")
                continue
            recent = r.json().get("filings", {}).get("recent", {})
            for form, date, acc, doc, desc in zip(
                recent.get("form", []), recent.get("filingDate", []),
                recent.get("accessionNumber", []), recent.get("primaryDocument", []),
                recent.get("primaryDocDescription", [])):
                try:
                    d = datetime.strptime(date, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if d < cutoff or form not in FORMS:
                    continue
                a = acc.replace("-", "")
                link = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{a}/{doc}"
                rows.append(dict(company=name, form=form, date=date,
                                 desc=(desc or "")[:80], url=link, key=f"F:{acc}"))
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}")
    return rows, errors


# ─────────────────────────────────────────────────────────
# 2) 뉴스 (구글 뉴스 RSS — 키 불필요)
# ─────────────────────────────────────────────────────────
def fetch_news(days=1, per_topic=6):
    rows, errors = [], []
    for topic, q in QUERIES.items():
        url = ("https://news.google.com/rss/search?q="
               + requests.utils.quote(f"{q} when:{max(days,1)}d")
               + "&hl=en-US&gl=US&ceid=US:en")
        try:
            feed = feedparser.parse(requests.get(url, headers=UA, timeout=20).content)
            if not feed.entries:
                continue
            for e in feed.entries[:per_topic]:
                src = e.get("source", {}).get("title", "") if isinstance(e.get("source"), dict) else ""
                rows.append(dict(topic=topic, title=e.title, url=e.link,
                                 source=src, published=e.get("published", "")[:16],
                                 key="N:" + re.sub(r"\W+", "", e.title)[:60]))
        except Exception as ex:
            errors.append(f"{topic}: {type(ex).__name__}")
    return rows, errors


# ─────────────────────────────────────────────────────────
# 3) 금리·스프레드 (FRED CSV — 키 불필요)
# ─────────────────────────────────────────────────────────
def fetch_rates():
    out, errors = {}, []
    for sid, label in FRED.items():
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
        try:
            r = requests.get(url, headers=UA, timeout=25)
            if r.status_code != 200:
                errors.append(f"{sid}: HTTP {r.status_code}"); continue
            lines = [l for l in r.text.strip().split("\n") if l]
            vals = []
            for line in lines[1:]:
                parts = line.split(",")
                if len(parts) >= 2 and parts[1] not in (".", ""):
                    try:
                        vals.append((parts[0], float(parts[1])))
                    except ValueError:
                        pass
            if not vals:
                continue
            last_d, last_v = vals[-1]
            prev_v = vals[-2][1] if len(vals) > 1 else last_v
            wk_v = vals[-6][1] if len(vals) > 6 else last_v
            out[sid] = dict(label=label, date=last_d, value=last_v,
                            chg_1d=last_v - prev_v, chg_1w=last_v - wk_v)
        except Exception as e:
            errors.append(f"{sid}: {type(e).__name__}")
    return out, errors


def append_rates_csv(rates):
    p = os.path.join(OUT, "rates.csv")
    new = not os.path.exists(p)
    with open(p, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["asof", "series", "label", "date", "value", "chg_1d", "chg_1w"])
        for sid, d in rates.items():
            w.writerow([datetime.now().strftime("%Y-%m-%d"), sid, d["label"],
                        d["date"], d["value"], round(d["chg_1d"], 4), round(d["chg_1w"], 4)])


# ─────────────────────────────────────────────────────────
# 경보 규칙 — 대시보드 판정선과 연동
# ─────────────────────────────────────────────────────────
def make_alerts(rates, filings, news):
    a = []
    r30 = rates.get("DGS30")
    if r30:
        if r30["value"] >= 5.25:
            a.append(f"[금리] 30년물 {r30['value']:.2f}% — 대시보드 경계선(5.25%) 도달/상회")
        if abs(r30["chg_1w"]) >= 0.15:
            a.append(f"[금리] 30년물 주간 {r30['chg_1w']*100:+.0f}bp — 급변")
    hy = rates.get("BAMLH0A0HYM2")
    if hy and hy["chg_1w"] >= 0.30:
        a.append(f"[신용] 하이일드 스프레드 주간 +{hy['chg_1w']*100:.0f}bp — 사모크레딧 경계")
    for f in filings:
        if f["form"] in ("424B5", "FWP", "S-3"):
            a.append(f"[조달] {f['company']} {f['form']} — 신규 발행 가능성 ({f['date']})")
        elif f["form"] == "8-K":
            a.append(f"[공시] {f['company']} 8-K ({f['date']})")
    kw = ("default", "distress", "bankruptcy", "writedown", "downgrade",
          "cancel", "moratorium", "halt")
    for n in news:
        t = n["title"].lower()
        if any(k in t for k in kw):
            a.append(f"[뉴스] {n['title'][:90]}")
    return a


# ─────────────────────────────────────────────────────────
def write_brief(rates, filings, news, alerts, errors):
    today = datetime.now().strftime("%Y-%m-%d")
    L = [f"# AI 사이클 일일 브리핑 — {today}", ""]

    L.append("## ⚑ 경보")
    L += [f"- {x}" for x in alerts] if alerts else ["- 없음 (판정선 도달 항목 없음)"]
    L.append("")

    L.append("## 금리·신용")
    if rates:
        L.append("| 지표 | 최신 | 전일 | 1주 | 기준일 |")
        L.append("|---|---|---|---|---|")
        for sid, d in rates.items():
            L.append(f"| {d['label']} | {d['value']:.3f} | {d['chg_1d']*100:+.0f}bp | "
                     f"{d['chg_1w']*100:+.0f}bp | {d['date']} |")
    else:
        L.append("(수집 실패)")
    L.append("")

    L.append(f"## SEC 공시 ({len(filings)}건)")
    if filings:
        for f in sorted(filings, key=lambda x: x["date"], reverse=True):
            L.append(f"- **{f['company']}** `{f['form']}` {f['date']} — {f['desc']}  \n  {f['url']}")
    else:
        L.append("- 신규 없음")
    L.append("")

    L.append(f"## 뉴스 ({len(news)}건)")
    bytopic = {}
    for n in news:
        bytopic.setdefault(n["topic"], []).append(n)
    for topic, items in bytopic.items():
        L.append(f"### {topic}")
        for n in items:
            L.append(f"- [{n['title']}]({n['url']}) — {n['source']} {n['published']}")
        L.append("")

    if errors:
        L.append("## 수집 오류")
        L += [f"- {e}" for e in errors]
        L.append("")

    L.append("---")
    L.append("*자동 수집 결과. 사실 확인 전 원문(SEC 공시·1차 보도) 대조 필요. "
             "판정선: 30Y 5.25% / 하이일드 스프레드 급등 / 네오클라우드 신규 조달 조건 / "
             "데이터센터 ABS 발행 / GPU 임대료(10/5 CME 선물 상장 후 최우선).*")

    body = "\n".join(L)
    p = os.path.join(OUT, f"brief_{today}.md")
    open(p, "w", encoding="utf-8").write(body)
    # 최신본 사본 (저장소에서 한 곳만 보면 되도록)
    open(os.path.join(OUT, "latest.md"), "w", encoding="utf-8").write(body)
    return p


def selftest():
    log("자가진단 시작")
    tests = [
        ("SEC EDGAR", f"https://data.sec.gov/submissions/CIK{COMPANIES['CoreWeave']}.json"),
        ("FRED",      "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS30"),
        ("Google News", "https://news.google.com/rss/search?q=test&hl=en-US&gl=US&ceid=US:en"),
    ]
    ok = True
    for name, url in tests:
        try:
            r = requests.get(url, headers=UA, timeout=20)
            status = "OK" if r.status_code == 200 else f"HTTP {r.status_code}"
            if r.status_code != 200:
                ok = False
            log(f"  {name}: {status} ({len(r.content)} bytes)")
        except Exception as e:
            ok = False
            log(f"  {name}: 실패 {type(e).__name__}")
    if MY_EMAIL.startswith("your_email"):
        log("  ⚠ WATCH_EMAIL 환경변수 또는 MY_EMAIL을 본인 이메일로 설정하세요 (SEC 규정)")
        ok = False
    else:
        log(f"  식별 이메일: {MY_EMAIL}")
    log("자가진단 " + ("통과" if ok else "실패 항목 있음"))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1, help="조회 기간(일)")
    ap.add_argument("--selftest", action="store_true", help="접근 점검만 수행")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(0 if selftest() else 1)

    errors = []
    log("금리 수집…");  rates, e = fetch_rates();            errors += e
    log("공시 수집…");  filings, e = fetch_filings(args.days); errors += e
    log("뉴스 수집…");  news, e = fetch_news(args.days);      errors += e

    # 중복 제거
    seen = load_seen()
    filings = [f for f in filings if f["key"] not in seen]
    news    = [n for n in news    if n["key"] not in seen]
    seen |= {x["key"] for x in filings} | {x["key"] for x in news}
    save_seen(seen)

    if rates:
        append_rates_csv(rates)
    alerts = make_alerts(rates, filings, news)
    path = write_brief(rates, filings, news, alerts, errors)

    log(f"완료 → {path}")
    log(f"  경보 {len(alerts)} · 공시 {len(filings)} · 뉴스 {len(news)} · 오류 {len(errors)}")
    if alerts:
        print("\n".join("  ⚑ " + a for a in alerts[:10]))


if __name__ == "__main__":
    main()
