# AI Cycle Watch

AI 인프라 사이클의 자금조달·신용 지표를 매일 자동 수집하는 감시망.
GitHub Actions가 매일 한국시간 오전 7시에 실행하며, PC가 꺼져 있어도 동작한다.

**최신 브리핑 → [`out/latest.md`](out/latest.md)**

---

## 무엇을 보는가

AI 사이클의 위험은 수요가 아니라 자금조달 쪽에서 먼저 드러난다는 전제로,
채권시장·공시·뉴스 세 경로를 감시한다.

| 층 | 지표 | 출처 |
|---|---|---|
| 금리 | 미국채 30년·10년 | FRED |
| 신용 | 하이일드·BBB 스프레드 | FRED |
| 조달 | 네오클라우드 채권 발행(424B5·FWP·S-3) | SEC EDGAR |
| 실적 | 8-K / 10-Q / 10-K | SEC EDGAR |
| 뉴스 | 7개 주제 헤드라인 | Google News RSS |

감시 기업: CoreWeave, Nebius, IREN, Cipher Mining, TeraWulf, Applied Digital, Micron, SanDisk

뉴스 주제: 네오클라우드 자금조달 · 데이터센터 ABS · 사모크레딧 부실 · GPU 임대료 ·
AI 설비투자 가이던스 · 엔비디아 금융플랫폼 · 전력/인허가 병목

---

## 경보 규칙

브리핑 최상단 `⚑ 경보`에만 뜨는 항목. 나머지는 참고용이다.

- 미국채 30년물 **5.25% 도달** 또는 주간 ±15bp 이상 변동
- 하이일드 스프레드 주간 **+30bp 이상**
- 감시 기업의 **발행 관련 공시**(424B5·FWP·S-3) 등장
- 헤드라인에 default / distress / bankruptcy / writedown / downgrade / cancel / moratorium / halt 포함

---

## 설정 (최초 1회)

1. **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `WATCH_EMAIL`
   - Value: 본인 이메일 (SEC EDGAR가 접근자 식별을 요구함)

2. **Actions 탭 → "I understand my workflows, go ahead and enable them"** 클릭

3. **Actions → AI Cycle Daily Watch → Run workflow** 로 즉시 테스트

이후 매일 자동 실행되며 결과가 `out/`에 커밋된다.

---

## 로컬에서도 실행하려면

```bash
pip install requests feedparser
python ai_watch.py --selftest    # 접근 점검
python ai_watch.py               # 오늘자 수집
python ai_watch.py --days 3      # 최근 3일
```

---

## 출력물

| 파일 | 내용 |
|---|---|
| `out/latest.md` | 최신 브리핑 (이것만 보면 됨) |
| `out/brief_YYYY-MM-DD.md` | 일자별 보관본 |
| `out/rates.csv` | 금리·스프레드 시계열 누적 |
| `out/seen.json` | 중복 방지 기록 |

---

## 손보기

- **기업 추가·삭제** → `ai_watch.py`의 `COMPANIES` (CIK는 sec.gov 검색)
- **뉴스 주제 변경** → `QUERIES`
- **경보 기준 조정** → `make_alerts()` 함수의 숫자
- **실행 시각 변경** → `.github/workflows/daily.yml`의 cron (UTC 기준)

**예정된 추가**: 2026년 10월 5일 CME·실리콘데이터 컴퓨트 선물(H100·B200 임대지수) 상장 후,
GPU 임대료 시세를 최우선 지표로 추가할 것.

---

## 한계

- 공개 데이터만 수집한다. 사모크레딧 부실은 상당수가 비공개라 뉴스로만 잡힌다.
- RSS는 헤드라인만 제공하므로 본문 확인은 링크를 직접 열어야 한다.
- **이것은 알림망이지 분석 도구가 아니다.** 수치의 성격과 인용의 정확성은 원문 대조로 판별해야 한다.
