# -*- coding: utf-8 -*-
"""
keywords.py
===============================================================================
[모듈 목적]
오토플러스(리본카) 언론 모니터링 자동화 시스템의 "단일 진실 공급원(Single Source
of Truth)"이다. 자사/경쟁사/업계 키워드, 민감/부정 키워드, 모니터링 요일,
매체 우선순위 등 시스템 전체에서 참조하는 모든 상수를 이 파일 하나에서만
관리한다. 다른 모듈(rule_filter.py, ai_scorer.py, collector_*.py 등)은 절대
키워드를 직접 하드코딩하지 않고 반드시 이 파일을 import 하여 사용한다.

[핵심 전제 - 반드시 숙지]
이 시스템의 최우선 목표는 단순 기사 수집이 아니라, PR 담당자(오토플러스 인수인계서
기준)의 암묵적 선별 기준을 시스템에 이관하는 것이다.
"정확도 저하 원인은 기술적 한계가 아니라 담당자 판단 기준의 미이관"이라는 전제를
이 파일을 포함한 모든 코드 주석·실행 로그·README에 일관되게 명시한다.

[2026-07-06 업데이트 - 키워드 현황 조회/추가/삭제 기능 반영]
기존에는 이 파일의 상수들이 완전히 하드코딩되어 있어, 검색 키워드를 바꾸려면
코드를 직접 수정하고 재배포해야 했다. 이는 PR 담당자가 현업에서 겪는 실제
니즈(경쟁사 신규 진입, 업계 트렌드 키워드 변화 등에 따라 검색어를 수시로
조정해야 함)를 반영하지 못하는 구조였다.

이번 업데이트로 자사/경쟁사/업계1~5/민감/부정 키워드는 data/keywords.json 에
영속화되며, 웹 대시보드(설정 화면)에서 실시간으로 조회·추가·삭제할 수 있다.

[하위 호환성 - 매우 중요]
collector_naver_api.py, collector_google_rss.py, rule_filter.py,
formatter_docx.py, report_formatter.py, email_sender.py 등 다수의 모듈이
`from keywords import OWN_KEYWORDS` 형태로 이 파일의 리스트/딕셔너리 객체를
직접 참조(바인딩)한다. 파이썬에서 `from x import Y`는 "이름"이 아니라 그
시점의 "객체"에 대한 참조를 가져오는 것이므로, 만약 add_keyword()/
remove_keyword() 가 `OWN_KEYWORDS = new_list` 처럼 객체 자체를 재할당하면
이미 import를 마친 다른 모듈들은 갱신 내용을 전혀 보지 못하는 문제가 생긴다.

따라서 이 파일의 모든 변경 함수(add_keyword/remove_keyword)는 반드시
리스트의 `.append()`/`.remove()` 등 **제자리(in-place) 변경**만 수행하고,
절대 최상위 이름을 재할당하지 않는다. INDUSTRY_KEYWORDS는 딕셔너리 자체는
고정하고 그 값(list)만 in-place로 변경한다.

[출처]
- 오토플러스 인수인계서_김채윤(250711).doc 의 "모니터링 카테고리" 표
- 사용자가 제공한 keywords.py 원문 (임의 축약 금지, 그대로 반영 - 아래 값은
  data/keywords.json 최초 생성 시의 시드 값으로 사용됨)
===============================================================================
"""
import os
import json
import copy

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
KEYWORDS_JSON_PATH = os.path.join(DATA_DIR, "keywords.json")

# -----------------------------------------------------------------------------
# 최초 1회 시드값 (data/keywords.json 이 존재하지 않을 때만 사용됨)
# 사용자가 제시한 keywords.py 원문을 그대로 반영한다 (임의 축약 금지).
# -----------------------------------------------------------------------------
_SEED_OWN_KEYWORDS = ["오토플러스", "리본카"]

_SEED_COMPETITOR_KEYWORDS = [
    "케이카", "엔카닷컴", "KB차차차", "헤이딜러", "오토허브셀카", "카머스",
    "첫차중고차", "차란차", "직카", "현대인증중고차", "기아인증중고차",
    "KGM인증중고차", "롯데렌탈중고차", "SK렌터카중고차",
    "오토인사이드",  # 2026-07-06 추가: 실제 담당자 수기 모니터링 결과 대조 중 누락 발견
    # 확인 필요: 인수인계서 원문에는 "첫차"로 단독 표기됨. 실제 검색 시
    # "첫차중고차"로 검색하면 결과가 좁아질 수 있으므로, 운영 중 정경화 대리/
    # 나한글 팀장에게 정확한 검색어를 재확인할 것을 권장함. (웹 대시보드에서
    # 담당자가 직접 "첫차"로 수정/추가할 수 있도록 이번 업데이트로 지원함)
]

# 2026-07-06 업데이트: 실제 담당자가 3일간(7/3~7/6) 수기로 수행한 모니터링
# 결과물과 시스템 자동 수집 결과를 대조한 결과, 아래 카테고리에 누락된
# 키워드들을 발견해 보강함. 상세 근거는 각 카테고리 옆 주석 참조.
_SEED_INDUSTRY_KEYWORDS = {
    "1": [
        "중고차", "렌터카", "구독서비스",
        # 담당자 리포트에 "중고차(수입, 직영, 매매, 리스, 할부)"로 세부 검색
        # 범주가 명시돼 있어, 포괄 키워드 "중고차" 외에 세부 키워드도 추가
        # (기존 "중고차" 검색만으로는 노출 우선순위에서 밀리는 세부 기사를
        # 보강 포착하기 위함)
        "중고차 매매", "중고차 리스", "중고차 할부", "중고차 직영",
    ],
    "2": [
        "캐피탈+중고차", "카드사",
        # 담당자 리포트의 "현대캐피탈" 언급 기사(예: 침수차 보상 등)를
        # 시스템이 놓치고 있어 추가
        "현대캐피탈", "현대캐피탈 인증중고차",
    ],
    "3": [
        # 기존에는 수입 브랜드 중 "벤츠·BMW·아우디·테슬라"만 실제 검색 키워드로
        # 등록돼 있었음. 아래 항목들은 이 파일 하단의 DOMESTIC_OEM_BRANDS /
        # IMPORT_OEM_BRANDS 참고용 리스트에는 있었지만 실제 검색에는 반영되지
        # 않았던 것을 담당자 리포트 대조로 발견해 동기화함(볼보 포함).
        "벤츠", "BMW", "아우디", "테슬라", "볼보",
        "현대차", "기아", "제네시스", "르노코리아", "KGM", "한국지엠",
        # 담당자 리포트에 등장했으나 기존 어떤 목록에도 없던 해외 브랜드
        "BYD", "토요타", "렉서스", "닛산",
    ],  # 신차출시·판매동향·국내전략만 포함, 단순 프로모션 제외
    "4": [
        "업계 기획", "업계 트렌드",
        # 담당자 리포트의 "자동차(시장, 업계, 트렌드)" 포괄 검색을 반영
        "국산차", "수입차",
    ],
    "5": ["인프라", "법규", "자율주행", "모빌리티"],
}

_SEED_SENSITIVE_KEYWORDS = ["VIG파트너스"]

_SEED_NEGATIVE_KEYWORDS = ["사고", "소송", "리콜", "불만", "논란", "과징금", "수사", "적발"]

# 업계 카테고리별 안내 라벨 (웹 대시보드 표시용, 인수인계서 표 기준)
INDUSTRY_LABELS = {
    "1": "1순위: 중고차/렌터카/구독서비스",
    "2": "2순위: 캐피탈+중고차/카드사(자동차 금융)",
    "3": "3순위: 브랜드뉴스(신차출시·판매동향·국내전략, 단순 프로모션 제외)",
    "4": "4순위: 업계 기획/트렌드",
    "5": "5순위: 기타(인프라/법규/자율주행/모빌리티)",
}


def _default_keywords_payload():
    """data/keywords.json 최초 생성 시 사용할 기본 페이로드를 반환한다."""
    return {
        "_comment": (
            "검색 키워드 단일 진실 공급원(keywords.py)의 영속화 파일. "
            "웹 대시보드(/settings)에서 추가·삭제하면 이 파일이 갱신된다. "
            "직접 수정해도 되지만, 가급적 웹 대시보드를 통해 변경할 것을 권장한다."
        ),
        "own": list(_SEED_OWN_KEYWORDS),
        "competitor": list(_SEED_COMPETITOR_KEYWORDS),
        "industry": copy.deepcopy(_SEED_INDUSTRY_KEYWORDS),
        "sensitive": list(_SEED_SENSITIVE_KEYWORDS),
        "negative": list(_SEED_NEGATIVE_KEYWORDS),
    }


def _load_keywords_payload():
    """
    data/keywords.json 을 로드한다. 파일이 없으면 시드값으로 새로 생성한다.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(KEYWORDS_JSON_PATH):
        payload = _default_keywords_payload()
        with open(KEYWORDS_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"[keywords] data/keywords.json 이 없어 시드값으로 새로 생성했습니다: {KEYWORDS_JSON_PATH}")
        return payload

    with open(KEYWORDS_JSON_PATH, "r", encoding="utf-8") as f:
        payload = json.load(f)

    # 방어적 기본값 보정 (파일이 일부만 존재하는 등 예외 상황 대비)
    defaults = _default_keywords_payload()
    for key in ("own", "competitor", "sensitive", "negative"):
        payload.setdefault(key, defaults[key])
    payload.setdefault("industry", defaults["industry"])
    for priority in ("1", "2", "3", "4", "5"):
        payload["industry"].setdefault(priority, defaults["industry"][priority])

    return payload


def save_keywords():
    """
    현재 메모리 상의 키워드 상태(OWN_KEYWORDS 등)를 data/keywords.json 에 저장한다.
    add_keyword()/remove_keyword() 호출 직후 자동으로 실행된다.
    """
    payload = {
        "_comment": (
            "검색 키워드 단일 진실 공급원(keywords.py)의 영속화 파일. "
            "웹 대시보드(/settings)에서 추가·삭제하면 이 파일이 갱신된다. "
            "직접 수정해도 되지만, 가급적 웹 대시보드를 통해 변경할 것을 권장한다."
        ),
        "own": OWN_KEYWORDS,
        "competitor": COMPETITOR_KEYWORDS,
        "industry": {str(k): v for k, v in INDUSTRY_KEYWORDS.items()},
        "sensitive": SENSITIVE_KEYWORDS,
        "negative": NEGATIVE_KEYWORDS,
    }
    with open(KEYWORDS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


# -----------------------------------------------------------------------------
# 최초 로드 - 아래 이름들(OWN_KEYWORDS 등)은 이후 절대 재할당(rebind)하지 않는다.
# add_keyword/remove_keyword 는 이 객체들을 in-place 로만 변경한다.
# -----------------------------------------------------------------------------
_payload = _load_keywords_payload()

OWN_KEYWORDS = list(_payload["own"])
COMPETITOR_KEYWORDS = list(_payload["competitor"])
INDUSTRY_KEYWORDS = {int(k): list(v) for k, v in _payload["industry"].items()}
SENSITIVE_KEYWORDS = list(_payload["sensitive"])
NEGATIVE_KEYWORDS = list(_payload["negative"])

# -----------------------------------------------------------------------------
# 6) 모니터링 공식 운영 요일 - 월/수/금 (0=월요일 기준, datetime.weekday())
#    인수인계서: "월, 수, 금 11시까지 전달. 공휴일 등으로 해당 요일 모니터링
#    미발송 시, 직후 업무일에 전달"
# -----------------------------------------------------------------------------
MONITORING_WEEKDAYS = [0, 2, 4]  # 월/수/금 (0=월)

# -----------------------------------------------------------------------------
# 7) 매체 우선순위 - 게재보고/문서 정렬 시 사용
#    인수인계서 미디어리스트 분류 기준(통신사>일간지>경제지>스포츠지>전문지/무가지>
#    주간지>오토지>온라인) 및 사용자 제공 keywords.py 원문 반영
# -----------------------------------------------------------------------------
MEDIA_PRIORITY_ORDER = [
    "통신사", "일간지", "경제지", "스포츠지", "전문지/무가지",
    "주간지", "오토지", "온라인"
]
# 전문지/무가지 예: 전자신문, 디지털타임스, 전기신문, 메트로
# 오토지 예: 오토IN, 오토모닝, 오토다이어리

# -----------------------------------------------------------------------------
# 8) 3순위(완성차) 세부 - 신차출시/판매동향/국내전략 판별용 허용 표현
#    인수인계서 표: "신차 출시: 벤츠·BMW·아우디·테슬라 정도 / 브랜드별 판매 동향 /
#    국내 사업 전략 등 ※단순 프로모션 자료 반영 X"
#    rule_filter.py의 filter_oem_promotion()에서 사용
#    (확인 필요: 이 표현 리스트들은 이번 업데이트의 "검색 키워드 관리" 범위에는
#    포함하지 않았다. 문의 시 별도 관리 기능으로 확장 검토 가능.)
# -----------------------------------------------------------------------------
OEM_ALLOWED_EXPRESSIONS = [
    "신차", "출시", "판매", "점유율", "전략", "생산", "양산", "수출", "실적",
    "등록", "판매량", "판매 동향", "국내 전략", "투자", "공장"
]
OEM_PROMOTION_EXPRESSIONS = [
    "이벤트", "프로모션", "할인", "경품", "쿠폰", "사은품", "체험단", "시승 이벤트",
    "페스티벌", "특가", "감사제"
]

# -----------------------------------------------------------------------------
# 9) 국내 완성차 5개사 + 수입 브랜드 목록 (3순위 세부 카테고리, 인수인계서 표 반영)
#    [2026-07-06 수정] 기존에는 "실제 검색 키워드는 INDUSTRY_KEYWORDS[3]을
#    따른다"고 되어 있었으나, 실제로는 이 리스트가 INDUSTRY_KEYWORDS[3]에
#    반영되지 않아 "볼보" 등 여기 적힌 브랜드 기사가 수집 자체가 안 되는
#    문제가 있었다(담당자 수기 모니터링 결과와 대조해 발견). 지금은
#    INDUSTRY_KEYWORDS[3](위 _SEED_INDUSTRY_KEYWORDS["3"] 참조)에 아래
#    브랜드들을 실제로 동기화해 반영했다. 이 리스트는 어떤 브랜드가
#    "3순위 브랜드뉴스" 대상인지 보여주는 참고용으로 계속 유지한다.
# -----------------------------------------------------------------------------
DOMESTIC_OEM_BRANDS = ["현대차", "기아", "제네시스", "르노코리아", "KGM", "한국지엠"]
IMPORT_OEM_BRANDS = ["벤츠", "BMW", "아우디", "볼보", "테슬라", "BYD", "토요타", "렉서스", "닛산"]

# -----------------------------------------------------------------------------
# 10) 오토플러스가 지양하는 표현 (보도자료 작성 가이드, 참고용 - 향후 문체 검수
#     기능 확장 시 사용 가능. 인수인계서 "보도자료 작성 시 유의사항" 반영)
#     확인 필요: 이 리스트는 기사 필터링용이 아니라 "자사 배포 보도자료 작성"
#     가이드이므로 review_mode.py / formatter_docx.py의 참고 주석으로만 활용.
# -----------------------------------------------------------------------------
DISCOURAGED_EXPRESSIONS_FOR_PR = ["감가율", "잔존율", "고물가에 지친", "이중고에 시달리는"]
PREFERRED_EXPRESSIONS_FOR_PR = ["가격 하락률", "가격 유지율"]


# =============================================================================
# 키워드 현황 조회 / 추가 / 삭제 API (web_app.py 의 /keywords 라우트에서 사용)
# =============================================================================
# 카테고리 코드 정의:
#   "own"          -> OWN_KEYWORDS (자사)
#   "competitor"   -> COMPETITOR_KEYWORDS (경쟁사)
#   "industry1"~"industry5" -> INDUSTRY_KEYWORDS[1]~[5] (업계 1~5순위)
#   "sensitive"    -> SENSITIVE_KEYWORDS (VIG파트너스 등 민감 키워드)
#   "negative"     -> NEGATIVE_KEYWORDS (부정 키워드 태그용)
VALID_CATEGORIES = (
    ["own", "competitor", "sensitive", "negative"]
    + [f"industry{i}" for i in range(1, 6)]
)


def _get_list_for_category(category: str):
    """카테고리 코드에 해당하는 실제 리스트 객체(참조)를 반환한다."""
    if category == "own":
        return OWN_KEYWORDS
    if category == "competitor":
        return COMPETITOR_KEYWORDS
    if category == "sensitive":
        return SENSITIVE_KEYWORDS
    if category == "negative":
        return NEGATIVE_KEYWORDS
    if category.startswith("industry"):
        try:
            priority = int(category.replace("industry", ""))
        except ValueError:
            return None
        return INDUSTRY_KEYWORDS.get(priority)
    return None


def get_keywords_snapshot():
    """
    현재 전체 키워드 현황을 화면 표시용 딕셔너리로 반환한다.
    웹 대시보드 /settings, /keywords API 에서 사용.
    """
    return {
        "own": list(OWN_KEYWORDS),
        "competitor": list(COMPETITOR_KEYWORDS),
        "industry": {str(k): list(v) for k, v in sorted(INDUSTRY_KEYWORDS.items())},
        "sensitive": list(SENSITIVE_KEYWORDS),
        "negative": list(NEGATIVE_KEYWORDS),
        "industry_labels": INDUSTRY_LABELS,
    }


def add_keyword(category: str, keyword: str):
    """
    지정 카테고리에 키워드를 추가한다 (in-place, 재할당 없음).

    반환값: (성공 여부(bool), 메시지(str))
    """
    keyword = (keyword or "").strip()
    if not keyword:
        return False, "키워드를 입력해 주세요."
    if category not in VALID_CATEGORIES:
        return False, f"알 수 없는 카테고리입니다: {category}"

    target_list = _get_list_for_category(category)
    if target_list is None:
        return False, f"카테고리 '{category}'에 해당하는 키워드 목록을 찾을 수 없습니다."

    if keyword in target_list:
        return False, f"'{keyword}'는(은) 이미 등록된 키워드입니다."

    target_list.append(keyword)  # in-place 변경 - 다른 모듈의 참조도 즉시 반영됨
    save_keywords()
    print(f"[keywords] 키워드 추가: category={category}, keyword='{keyword}' "
          f"(추가 후 담당자 판단 기준이 시스템에 반영됨 - 핵심 전제 실천)")
    return True, f"'{keyword}' 키워드가 [{category}]에 추가되었습니다."


def remove_keyword(category: str, keyword: str):
    """
    지정 카테고리에서 키워드를 삭제한다 (in-place, 재할당 없음).

    안전장치: 자사(own) 키워드는 최소 1개 이상 유지되어야 하므로, 마지막
    1개를 삭제하려는 요청은 거부한다 (자사 언급 누락은 PR팀 입장에서 가장
    치명적인 실패이므로 - collector_google_rss.py 모듈 철학 주석 참조).

    반환값: (성공 여부(bool), 메시지(str))
    """
    keyword = (keyword or "").strip()
    if not keyword:
        return False, "삭제할 키워드를 입력해 주세요."
    if category not in VALID_CATEGORIES:
        return False, f"알 수 없는 카테고리입니다: {category}"

    target_list = _get_list_for_category(category)
    if target_list is None:
        return False, f"카테고리 '{category}'에 해당하는 키워드 목록을 찾을 수 없습니다."

    if keyword not in target_list:
        return False, f"'{keyword}'는(은) 해당 카테고리에 존재하지 않습니다."

    if category == "own" and len(target_list) <= 1:
        return False, (
            "자사 키워드는 최소 1개 이상 유지되어야 합니다 "
            "(자사 언급 누락은 PR팀 입장에서 가장 치명적인 실패이기 때문입니다)."
        )

    target_list.remove(keyword)  # in-place 변경
    save_keywords()
    print(f"[keywords] 키워드 삭제: category={category}, keyword='{keyword}'")
    return True, f"'{keyword}' 키워드가 [{category}]에서 삭제되었습니다."


if __name__ == "__main__":
    print(json.dumps(get_keywords_snapshot(), ensure_ascii=False, indent=2))
