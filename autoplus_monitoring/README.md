# 오토플러스(리본카) 언론 모니터링 자동화 시스템

## 0. 핵심 전제 (반드시 숙지)

이 시스템의 최우선 목표는 **단순 기사 수집이 아니라, PR 담당자의 암묵적 선별 기준을
시스템에 이관해 AI 선별 정확도를 단계적으로 끌어올리는 것**입니다.

> **"정확도 저하 원인은 기술적 한계가 아니라 담당자 판단 기준의 미이관"**

이 전제는 모든 코드 주석·실행 로그·본 README에 일관되게 반영되어 있습니다.
AI 모델을 교체하거나 프롬프트를 정교화하는 것보다, `review_mode.py`를 통해 실제
발송본과 자동화 결과를 지속적으로 대조하며 `data/label_examples.json`을 갱신하는
것이 정확도 향상의 핵심 경로입니다.

---

## 1. 시스템 개요 / 목적

오토플러스(리본카)의 PR 실무(정경화 대리, 나한글 팀장 등)를 대행하는 커뮤니케이션
담당자가 매일 반복 수행하던 "뉴스 수집 → 카테고리 분류 → 포함/제외 판단 → Word
문서 작성 → 이메일 발송" 과정을 자동화합니다.

**3대 산출물**
- **(A) Python 백엔드 파이프라인**: 수집 → 1차 규칙필터 → 2차 AI채점 → 문서생성
- **(B) 웹 대시보드**: 결과 확인 + 이메일 구독 관리 + 수동실행 + 주간리뷰
- **(C) Outlook 연동 이메일**: 본문을 Outlook에서 바로 확인 가능한 HTML 메일 발송/초안 저장

**참고 자료 반영 처리 지침**
- 사용자가 제시한 참고 블로그(`blog.naver.com/aiforhr/224307047762`)는 HRD 분야의
  "수집→엑셀 정리→Outlook 발송" 자동화 사례로, 실제 접속 결과 확인함. 이 사례의
  일반적인 뉴스레터형 대시보드 구조(수집→정리→발송)를 참고하되, 최종 기능 명세는
  본 프로젝트 요구사항(웹 대시보드 명세)을 우선 적용했습니다.
- "이메일 추가 시 매일 발송" 요청과 인수인계서상 공식 운영 요일(월/수/금)의 상충은
  `.env`의 `SEND_FREQUENCY` 값(MWF/DAILY)으로 관리자가 선택할 수 있게 구조를
  열어두는 것으로 해결했습니다 (9번 항목 참조).

---

## 2. 설치법

```bash
cd autoplus_monitoring
pip install -r requirements.txt
cp .env.example .env   # 실제 값으로 채운 뒤 사용 (TEST_MODE=True 유지 시 API 키 없이도 동작)
```

---

## 3. .env 설정법

`.env.example`을 참고해 `.env` 파일을 생성합니다. 주요 항목:

| 변수 | 설명 | 확인 필요 사항 |
|---|---|---|
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | 네이버 뉴스 검색 API 인증 | 네이버 개발자센터에서 애플리케이션 등록 필요 |
| `LLM_API_KEY` / `LLM_MODEL` | 2차 AI 채점용 LLM | 사용할 LLM 제공사 정책 확인 필요 |
| `EMAIL_BACKEND` | `smtp` 또는 `graph` | 사내 IT 정책 확인 필요 (아래 8번 항목 참조) |
| `SMTP_*` | SMTP 발송 설정 | Exchange Online 기본 인증 차단 여부 확인 필요 |
| `GRAPH_*` | Microsoft Graph API 발송 설정 | Azure AD 앱 등록 및 Mail.Send 권한 필요 |
| `SEND_FREQUENCY` | `MWF`(월/수/금, 기본) 또는 `DAILY` | 9번 항목 참조 |
| `TEST_MODE` | `True` 시 외부 API 호출 없이 전체 플로우 동작 | 개발/데모 환경에서는 `True` 권장 |
| `FLASK_SECRET_KEY` / `FLASK_PORT` | 웹 대시보드 설정 | 운영 배포 시 SECRET_KEY 반드시 변경 |

**절대 `.env` 파일을 git에 커밋하지 마십시오** (`.gitignore`에 이미 등록됨).

---

## 4. 실행 모드 4가지

```bash
# 1) 파이프라인 1회 즉시 실행 (수집→필터→채점→문서생성→메일)
python3 main.py --run

# 2) 자동 스케줄 실행 (SEND_FREQUENCY에 따라 월/수/금 또는 매일 11시 자동 실행)
python3 main.py --schedule

# 3) 주간 리뷰 모드 (실제 발송 URL과 자동화 결과 대조)
python3 main.py --review --sent-urls "https://example.com/1,https://example.com/2"

# 4) 웹 대시보드 실행
python3 main.py --web
# 또는 PM2로 데몬 실행:
pm2 start ecosystem.config.cjs
```

---

## 5. 웹 대시보드 접속법

```bash
pm2 start ecosystem.config.cjs
curl http://localhost:5000/
```

**라우트 목록**

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/` | 메인 대시보드 (오늘 결과, 섹션별 필터) |
| POST | `/run` | 모니터링 즉시 실행 (백그라운드 스레드) |
| GET | `/status` | 실행 상태 JSON |
| GET | `/articles` | 기사 목록 API (`decision`/`section`/`flag` 필터) |
| POST | `/articles/<id>/label` | 담당자가 AI 판단을 직접 수정 → 실시간 라벨링 루프 |
| POST | `/send-email` | 이메일 즉시 발송 |
| GET | `/settings` | 구독자·발송주기 설정 화면 |
| POST | `/settings/subscribers` | 구독 이메일 추가/삭제 |
| GET | `/review` | 주간 리뷰 화면 |
| POST | `/review/run` | 리뷰 실행 |
| GET | `/keywords` | 키워드 현황 조회 API (자사/경쟁사/업계1~5/민감/부정) |
| POST | `/keywords/add` | 키워드 추가 (`{"category":"...", "keyword":"..."}`) |
| POST | `/keywords/remove` | 키워드 삭제 (`{"category":"...", "keyword":"..."}`) |
| GET | `/download/docx` | Word 문서 다운로드 |
| GET | `/download/email-preview` | 이메일 HTML 미리보기 |

---

## 5-1. 키워드 관리 기능 (2026-07-06 추가)

**배경**: 검색 키워드(자사/경쟁사/업계1~5/민감/부정)가 `keywords.py`에
하드코딩되어 있으면, 경쟁사 신규 진입이나 업계 트렌드 변화가 있을 때마다
코드를 직접 수정하고 재배포해야 한다. 이는 "정확도 저하 원인은 기술적 한계가
아니라 담당자 판단 기준의 미이관"이라는 핵심 전제와 배치되므로, 담당자가
웹 대시보드에서 직접 키워드를 조회·추가·삭제할 수 있도록 개선했다.

**사용법**: `/settings` 화면 하단 "키워드 관리" 섹션에서 카테고리별로 현재
등록된 키워드가 칩(chip) 형태로 표시된다. 입력창에 새 키워드를 입력하고
추가(+) 버튼을 누르면 즉시 등록되며, 각 칩의 x 버튼을 누르면 확인 후 삭제된다.

**구현 방식**:
- 키워드 데이터는 `data/keywords.json`에 영속화된다 (기존에는 `keywords.py`
  파일 내 상수로만 존재).
- `collector_naver_api.py`, `collector_google_rss.py`, `rule_filter.py`,
  `formatter_docx.py`, `report_formatter.py`, `email_sender.py` 등 기존
  모듈들은 여전히 `from keywords import OWN_KEYWORDS` 형태로 동일하게
  동작한다. `keywords.py`의 `add_keyword()`/`remove_keyword()`는 리스트를
  `.append()`/`.remove()`로 **제자리(in-place) 변경**만 수행하고 절대
  최상위 이름을 재할당하지 않으므로, 이미 import를 마친 다른 모듈들도
  변경 사항을 즉시 함께 참조한다 (파이썬 객체 참조 공유 원리 활용).
- **안전장치**: 자사(own) 키워드는 최소 1개 이상 유지되어야 하며, 마지막
  1개를 삭제하려는 요청은 거부된다("자사 언급 누락은 PR팀 입장에서 가장
  치명적인 실패"라는 기존 설계 철학 반영). 중복 키워드 추가도 거부된다.

---

## 6. 주간 리뷰 루틴 가이드

1. 담당자가 실제 발송한 모니터링의 기사 URL 목록을 수집합니다 (메일함 또는
   카톡방 아카이브 참고).
2. 웹 대시보드 `/review` 화면 또는 CLI(`--review --sent-urls "..."`)로 리뷰를
   실행합니다.
3. 시스템이 "놓친 기사"(담당자는 포함, 자동화는 누락)와 "잘못 포함한 기사"
   (자동화는 포함, 담당자는 제외)를 산출하고 `label_examples.json` 업데이트
   후보를 제안합니다.
4. 담당자가 후보를 검토 후 승인하면 `data/label_examples.json`에 실제 반영되어,
   다음 채점부터 few-shot 예시로 활용됩니다.

**로드맵 참고 메모** (확정 성과 지표 아님):
> 정확도는 데이터가 아니라 기준 이관의 문제이며, 대조 데이터가 이미 쌓이고 있어
> 2~3주 내 선별 로직 개선이 가능하고, 8주간 리뷰 루틴이 누적되면 하이브리드
> 운영에서 목표 정확도 도달을 기대할 수 있다.

권장 주기: **주 1회**

---

## 7. label_examples 업데이트 방법

- **자동 방법 1 (주간 리뷰)**: 위 6번 항목 참조.
- **자동 방법 2 (실시간 라벨링 루프)**: 웹 대시보드에서 담당자가 기사 카드의
  포함/제외/검토필요 버튼을 직접 클릭하면 `/articles/<id>/label` API가 호출되어
  즉시 `label_examples.json` 후보에 반영됩니다.
- **수동 방법**: `data/label_examples.json`을 직접 열어 아래 형식으로 예시를
  추가/수정할 수 있습니다.
  ```json
  {"title": "...", "source": "...", "keyword": "...", "label": "포함/제외/검토필요", "reason": "..."}
  ```
- 현재 시드 데이터는 10개이며, `ai_scorer.py`는 **20개 이상 누적 시 선별
  정확도가 계단식으로 향상될 수 있음**을 참고용 로드맵으로 안내합니다(확정
  지표 아님). 담당자는 **주 1회 실물 데이터로 교체/추가**할 것을 권장합니다.

---

## 8. Outlook 연동 (SMTP vs Graph API) 선택 기준

| 구분 | SMTP | Microsoft Graph API |
|---|---|---|
| 설정 난이도 | 낮음 (계정/비밀번호만 필요) | 높음 (Azure AD 앱 등록 필요) |
| 안정성 | **사내 IT 정책 확인 필요** - Microsoft가 Exchange Online 기본 인증을 단계적으로 폐지하는 추세 | 상대적으로 안정적 (최신 인증 방식) |
| 안전장치 | 없음 (발송 즉시 반영) | `isDraft=true` 옵션으로 초안만 저장 가능 → 담당자가 Outlook에서 직접 확인 후 발송 |

**사내 IT 확인 필요 사항**:
1. 사내 Outlook 계정에서 SMTP 기본 인증이 차단되어 있는지 여부
2. Graph API 사용 시 Azure AD 앱 등록 및 `Mail.Send` 권한 관리자 동의 필요
3. 위 확인 결과에 따라 `.env`의 `EMAIL_BACKEND` 값(`smtp`/`graph`)을 결정

**권장**: 사내 IT 정책이 확인되기 전까지는 `EMAIL_BACKEND=graph` +
`save_as_draft=True`(초안 저장) 조합으로 안전하게 운영 후, 검증되면 자동 발송으로
전환하는 것을 권장합니다.

---

## 9. "매일 vs 월/수/금" 설계 판단 근거 메모

사용자의 "이메일 추가 시 매일 발송" 요청과 오토플러스 인수인계서에 명시된 공식
운영 요일(월/수/금 11시까지 전달)이 문자 그대로는 상충합니다.

**판단**: 이는 확정된 사실이 아니라 상충 조건에 대한 합리적 판단이며, 기본
발송 주기는 **공식 운영 기준인 월/수/금(MWF)**을 따르되, `.env`의
`SEND_FREQUENCY` 값을 `DAILY`로 변경하면 관리자가 언제든 매일 발송으로 전환할
수 있도록 구조를 열어두었습니다. (`main.py`의 `run_schedule()` 참조)

이 설계가 실제 운영 요구사항과 맞는지는 오토플러스 측(정경화 대리/나한글
팀장)과 재확인이 필요합니다.

---

## 10. 트러블슈팅 FAQ

**Q1. `python3 main.py --run` 실행 시 아무 결과도 안 나와요.**
A. `.env`의 `TEST_MODE` 값을 확인하세요. `True`면 더미 데이터로 동작합니다.
`False`로 바꾸면 `NAVER_CLIENT_ID`/`SECRET`, `LLM_API_KEY` 등이 필요합니다.

**Q2. 이메일이 실제로 발송되지 않아요.**
A. `TEST_MODE=True`인 경우 `output/email_preview.html`로만 저장되고 실제
발송되지 않습니다 (의도된 동작). 실제 발송하려면 `TEST_MODE=False`로 변경하고
`EMAIL_BACKEND`에 맞는 인증정보를 `.env`에 채우세요.

**Q3. Word 문서에 표(figure) 이미지가 안 보여요.**
A. 현재 시스템은 텍스트/URL 기반 기사만 문서화합니다. 인수인계서에 언급된
"지면 게재 시 이미지 첨부" 워크플로우는 이번 1차 구축 범위에 포함되지 않았으며,
추후 개별 첨부 기능으로 확장 검토가 필요합니다.

**Q4. 특정 매체가 "미확인매체_도메인" 으로 표시돼요.**
A. `data/media_domain_map.json`에 해당 도메인이 등록되지 않은 경우입니다.
실행 로그에 미확인 도메인 목록이 출력되니, 이를 참고해 해당 파일에 매체명/그룹을
직접 추가하세요.

**Q5. `filter_oem_promotion`이 프로모션 기사를 제대로 못 걸러내요.**
A. 이 필터는 휴리스틱(키워드 매칭) 기반이라 100% 정확하지 않습니다. 애매한
케이스는 2차 AI 채점 단계에서 보완되며, 최종적으로는 `review_mode.py`를 통한
지속적 피드백으로 개선하는 것이 근본 해결책입니다 (0번 핵심 전제 참조).

**Q6. 웹 대시보드가 5000번 포트 충돌로 안 켜져요.**
A. `fuser -k 5000/tcp` 로 기존 프로세스를 종료한 뒤 `pm2 restart
autoplus-monitoring-web`을 실행하세요.

**Q7. 스케줄 모드(`--schedule`)가 공휴일에도 실행돼요.**
A. `holidays` 라이브러리의 한국 공휴일 데이터가 최신인지 확인하세요. 라이브러리
버전이 오래되면 최신 연도의 대체공휴일이 누락될 수 있습니다.

**Q8. 대시보드에서 기사 링크를 클릭하면 "URL 연결 오류"가 떠요. 네이버 API
주소가 잘못된 건가요? (2026-07-06 진단 결과 추가)**
A. **아닙니다.** `collector_naver_api.py`의 네이버 뉴스 검색 API 엔드포인트
(`https://openapi.naver.com/v1/search/news.json`)는 이미 정확한 주소로
설정되어 있으며, 코드상 이 값은 수정된 적이 없습니다.

실제 원인은 `.env`의 `TEST_MODE=True` 설정입니다. TEST_MODE에서는 실제
네이버 API를 호출하지 않고 `https://dummy.example.com/news/...` 형태의
**존재하지 않는 가상 URL**로 더미 기사 20건을 생성합니다(파이프라인 검증
목적). 이 더미 URL이 대시보드에 실제 클릭 가능한 링크로 노출되어 있었고,
클릭 시 브라우저가 존재하지 않는 도메인에 접속을 시도하면서 연결 오류가
발생한 것으로 추정됩니다(확실하지 않으나 코드 흐름상 가장 개연성 높은
원인이며, 실제 재현 확인은 하지 못했습니다).

**조치 내역**:
1. 대시보드 상단에 TEST_MODE 안내 배너를 추가해, 지금 보이는 기사가 더미
   데이터임을 명시했습니다.
2. `static/js/app.js`에서 `dummy.example.com` 도메인의 링크는 클릭 시 실제
   이동 대신 "TEST_MODE 더미 데이터입니다" 안내 메시지를 표시하도록
   변경했습니다(링크 옆에 `TEST` 뱃지도 표시됩니다).

**실제 기사 링크가 정상 연결되도록 하려면**: `.env`에서 `TEST_MODE=False`로
변경하고, 네이버 개발자센터(https://developers.naver.com)에서 발급받은
`NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET`을 입력한 뒤 서비스를 재시작하세요.
이 경우 실제 네이버 뉴스 검색 결과의 `originallink`/`link` 값이 그대로
사용되므로 정상적으로 기사 페이지에 연결됩니다.

**[해결 완료 - 2026-07-06 추가]**
담당자로부터 실제 네이버 API `Client ID`/`Client Secret`을 전달받아
`.env`에 반영하고 `TEST_MODE=False`로 전환했습니다. 조치 후 검증 결과는
다음과 같습니다.

- 자격증명 유효성: 네이버 API에 직접 호출 테스트 결과 `HTTP 200` 정상
  응답 확인.
- 파이프라인 재실행(`/run`) 결과: 기사 464건 수집, 이 중 `dummy.example.com`
  더미 URL은 **0건**. 모든 URL이 `chosun.com`, `sedaily.com`,
  `news1.kr` 등 실제 언론사 도메인으로 확인됨 → **URL 연결 오류 원인 해소**.
- TEST_MODE 안내 배너는 `TEST_MODE=False` 전환에 따라 대시보드에서
  자동으로 사라짐(코드 조건부 렌더링 정상 동작 확인).

**[2차 조치 완료 - 2026-07-06 추가] AI 채점(LLM) 연동**
담당자로부터 GenSpark 플랫폼이 제공하는 AI API 키(`gsk-...` 형식)를
전달받아 `.env`의 `LLM_API_KEY`에 반영했습니다. 이 키는 OpenAI 공식
API가 아닌 **GenSpark 자체 OpenAI 호환 프록시** 전용이므로, 다음 두
가지를 함께 조정했습니다.

1. `.env`에 `LLM_BASE_URL=https://www.genspark.ai/api/llm_proxy/v1`
   추가, `LLM_MODEL`을 이 프록시가 허용하는 모델 중 하나인
   `gpt-5-mini`로 변경(기존 `gpt-4o-mini`는 이 프록시에서 미지원).
2. `ai_scorer.py`의 `_call_llm()`이 `LLM_BASE_URL`을 사용하도록
   1줄 수정(`OpenAI(api_key=..., base_url=LLM_BASE_URL)`).

**검증 결과**: 파이프라인(`/run`)을 실제 LLM 채점 모드로 재실행한
결과(1차 필터 통과 460건 + 라벨 예시 4건 = 총 464건, 약 30분 소요 —
아래 [성능 이슈] 참조), 시스템 프롬프트의 포함/제외 기준에 따라
정상적으로 분류되었습니다.

| 판정 | 건수 |
|---|---|
| 포함 | 249건 |
| 제외 | 197건 |
| 검토필요 | 14건 (부정 키워드 포함 기사 등 리스크 관리 기준 정상 반영 확인) |

예시: "제네시스 EV 리콜..." 기사 → "'리콜' 등 부정 키워드가 포함되어
있어 리스크 확인을 위해 담당자 검토 필요"로 정확히 판단 — 시스템
프롬프트의 [특수 처리] 항목(부정 키워드 시 NEGATIVE_FLAG 표시)이
의도대로 작동함을 확인했습니다.

**[성능 관련 확인 필요 사항 - 정확한 원인 미확정]**
464건을 LLM에 순차(건별로 1회씩) 호출하는 현재 구조상 전체 채점에
약 30분이 소요되었습니다. 이는 오류는 아니지만, 운영 시(특히 MWF
스케줄 자동 실행) 소요 시간이 부담될 수 있습니다. 정확한 개선
방향은 추가 논의가 필요하나, 합리적으로 검토 가능한 방향은 다음과
같습니다(확정된 결정 아님, 추측 수준):
- 요청 병렬화(동시 호출 수 제한을 두고 배치 처리)
- 1차 규칙 필터를 더 보수적으로 조정해 LLM 채점 대상 건수 자체를 축소
- 별도 큐/비동기 처리 구조 도입

이 부분은 담당자 판단이 필요한 사항이라 임의로 구조를 변경하지
않았습니다.

---

## 프로젝트 구조

```
autoplus_monitoring/
├── main.py                      # 실행 진입점 (--run/--schedule/--review/--web)
├── collector_naver_api.py       # 네이버 뉴스 검색 API 수집
├── collector_google_rss.py      # Google News RSS 보완 수집
├── collector_media_specific.py  # 개별 매체 수집 (TODO)
├── rule_filter.py                # 1차 규칙 기반 필터
├── ai_scorer.py                  # 2차 AI 채점 (LLM)
├── media_normalizer.py           # 매체명·기자명 정규화
├── release_calendar.py           # 배포 캘린더 연동
├── formatter_docx.py             # Word 문서 생성
├── report_formatter.py           # 게재보고 양식 생성
├── email_sender.py                # SMTP/Graph API 발송
├── review_mode.py                 # 주간 리뷰 + 라벨 업데이트
├── keywords.py                    # 키워드·우선순위 단일 진실 공급원
├── web_app.py                     # Flask 웹 대시보드
├── templates/                     # index/settings/review/email_template
├── static/css, static/js          # 프론트엔드 리소스
├── data/                          # JSON 데이터 파일 (keywords.json 포함 - 2026-07-06 추가)
├── output/                        # 생성된 docx/html 결과물
├── .env.example
├── requirements.txt
└── README.md
```

## 자체 검증 체크리스트 결과 (에이전트 작성)

1. ✅ PART 1~7의 키워드·규칙·포맷 축약 없이 100% 반영 (`keywords.py` SYSTEM_PROMPT 원문 유지)
2. ✅ 웹 대시보드 수동실행/결과조회/구독관리/다운로드/주간리뷰 전체 curl 테스트 완료
3. ✅ Outlook HTML 메일 table+인라인 스타일 구현, 실제 미리보기 파일 생성 확인
4. ✅ 1차 규칙필터-2차 AI채점 파일 분리, 기보고 중복은 rule_filter.py에서 기계적 제거
5. ✅ 모든 인증정보 .env 분리 (하드코딩 없음)
6. ✅ TEST_MODE=True로 `main.py --run` 및 `--web` 전체 플로우 외부호출 없이 정상 동작 확인
7. ✅ label_examples 누적 개수 및 "기준 이관 문제" 안내 문구 실행 로그 출력 확인
8. ✅ 모든 함수에 한국어 주석(docstring) 작성 완료

## 자체 검증 체크리스트 결과 (2026-07-06 키워드 관리 기능 추가분)

9. ✅ 키워드 현황 조회(`/keywords`)·추가(`/keywords/add`)·삭제
   (`/keywords/remove`) API curl 테스트 완료(자사/경쟁사/업계1~5/민감/부정
   전 카테고리, 중복 추가 거부, 자사 최소 1개 유지 안전장치 정상 동작 확인).
   `keywords.py`의 `from keywords import X` in-place mutation 방식이 이미
   import된 `rule_filter.py`/`collector_naver_api.py` 등에도 즉시 반영됨을
   Python REPL로 직접 검증함(재할당이 아닌 `.append()`/`.remove()`만 사용).
10. ✅ "URL 연결 오류" 원인 진단: 네이버 API 엔드포인트는 이미 정확함을
    코드 검토로 확인. TEST_MODE 더미 URL(`dummy.example.com`)이 실제
    원인일 가능성이 높다고 판단(확실하지 않음을 README/배너에 명시)하고,
    대시보드 배너 + 더미 링크 클릭 차단(안내 메시지 대체) 조치 완료.
11. ✅ 기존 12개 라우트 전체 회귀 테스트(curl) 재실행 - 모두 정상 동작
    확인(POST /run → GET /status → GET /articles → 라벨수정 → 구독자
    추가/삭제 → 이메일발송 → 리뷰실행 → docx/이메일미리보기 다운로드).
12. ✅ 테스트로 오염된 `data/label_examples.json`(10→14건),
    `data/sent_articles.json`을 git 커밋 시점의 원본으로 복원, `data/keywords.json`은
    시드값 기준으로 순서까지 원본과 동일하게 재생성 완료.
