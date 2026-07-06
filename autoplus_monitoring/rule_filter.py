# -*- coding: utf-8 -*-
"""
rule_filter.py
===============================================================================
[모듈 목적]
1차 규칙 기반 필터. AI(LLM) 호출 이전에 기계적 규칙으로 명확하게 판단 가능한
항목을 먼저 처리한다. 이렇게 함으로써:
  1) LLM 호출 비용을 절감한다 (ai_scorer.py는 1차 통과 기사에만 호출됨)
  2) 기보고 중복 제거처럼 "정확도가 100%여야 하는" 판단을 AI의 확률적 판단에
     맡기지 않고 결정적(deterministic) 로직으로 처리한다.

[핵심 전제]
"정확도 저하 원인은 기술적 한계가 아니라 담당자 판단 기준의 미이관"이다.
이 필터들은 오토플러스 인수인계서에 명시된 담당자의 명시적 규칙
(VIG파트너스 별도 처리, 단순 프로모션 제외 등)을 최대한 코드로 이관한 것이며,
완벽하지 않은 부분(예: 프로모션 여부의 애매한 경계)은 AI 채점 단계나 담당자의
review_mode.py 피드백을 통해 지속적으로 보완되어야 한다.

[처리 순서 - apply_all_filters()]
  1. filter_by_date        : 지정 기간(기본 1일) 이내 기사만 남김
  2. filter_duplicate_sent : 기보고 중복 제거 (URL/제목 유사도 90% 이상)
  3. filter_sensitive      : VIG파트너스 언급 기사 분리
  4. flag_negative         : 부정 키워드 기사에 태그만 부여 (제거하지 않음)
  5. filter_oem_promotion  : 3순위 완성차 단순 프로모션 기사 제외
===============================================================================
"""
import os
import json
import difflib
from datetime import datetime, timedelta

from keywords import (
    SENSITIVE_KEYWORDS,
    NEGATIVE_KEYWORDS,
    INDUSTRY_KEYWORDS,
    OEM_ALLOWED_EXPRESSIONS,
    OEM_PROMOTION_EXPRESSIONS,
)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SENT_ARTICLES_PATH = os.path.join(DATA_DIR, "sent_articles.json")

DUPLICATE_TITLE_SIMILARITY_THRESHOLD = 0.90  # 제목 유사도 90% 이상이면 중복으로 간주


def _load_sent_articles():
    """data/sent_articles.json 을 로드해 (url set, title list) 를 반환한다."""
    if not os.path.exists(SENT_ARTICLES_PATH):
        return set(), []
    with open(SENT_ARTICLES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    sent_list = data.get("sent_articles", [])
    urls = {item.get("url", "") for item in sent_list if item.get("url")}
    titles = [item.get("title", "") for item in sent_list if item.get("title")]
    return urls, titles


def filter_by_date(articles: list, days: int = 1):
    """
    발행일 기준 최근 `days`일 이내 기사만 남긴다.
    published_at 파싱에 실패하는 기사는(형식이 매체마다 상이함) 보수적으로
    "최신 기사일 가능성"을 존중해 통과시킨다 (놓친 기사 방지가 오탐 방지보다
    우선이라는 PR 실무 판단 반영).
    """
    cutoff = datetime.now() - timedelta(days=days)
    result = []
    for article in articles:
        published_at = article.get("published_at", "")
        parsed = None
        # 다양한 날짜 포맷 시도 (네이버 API: RFC822, 더미 데이터: "%Y-%m-%d %H:%M")
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(published_at, fmt)
                if parsed.tzinfo is not None:
                    parsed = parsed.replace(tzinfo=None)
                break
            except (ValueError, TypeError):
                continue
        if parsed is None:
            # 파싱 실패 시 통과 (놓침 방지 우선)
            result.append(article)
            continue
        if parsed >= cutoff:
            result.append(article)
    return result


def filter_duplicate_sent(articles: list):
    """
    data/sent_articles.json 과 URL 정확 일치 또는 제목 유사도 90% 이상인 기사를
    기보고 중복으로 간주해 제거한다.
    이 판단은 AI에 위임하지 않고 반드시 이 단계에서 기계적으로 처리한다
    (SequenceMatcher 기반 결정적 로직).

    반환값: (통과 기사 리스트, 중복 제거된 기사 리스트)
    """
    sent_urls, sent_titles = _load_sent_articles()
    passed, removed = [], []

    for article in articles:
        url = article.get("url", "")
        title = article.get("title", "")

        is_duplicate = False
        if url and url in sent_urls:
            is_duplicate = True
        else:
            for sent_title in sent_titles:
                similarity = difflib.SequenceMatcher(None, title, sent_title).ratio()
                if similarity >= DUPLICATE_TITLE_SIMILARITY_THRESHOLD:
                    is_duplicate = True
                    break

        if is_duplicate:
            removed.append(article)
        else:
            passed.append(article)

    print(f"[rule_filter] 기보고 중복 제거: {len(removed)}건 제외 (기계적 판단, AI 위임 없음)")
    return passed, removed


def filter_sensitive(articles: list):
    """
    VIG파트너스 등 민감 키워드가 제목 또는 요약에 포함된 기사를 정규 리스트에서
    분리해 별도로 반환한다. 인수인계서 명시 사항: "VIG파트너스가 언급된 이슈
    등은 모니터링에 포함하지 않고, 별도로 실무 카톡방에 알림."

    반환값: (일반 기사 리스트, 민감 이슈 기사 리스트)
    """
    normal, sensitive = [], []
    for article in articles:
        text = f"{article.get('title', '')} {article.get('summary', '')}"
        if any(kw in text for kw in SENSITIVE_KEYWORDS):
            article["sensitive_flag"] = True
            article["sensitive_note"] = "VIG파트너스 언급 - 실무 확인 필요 (전사 공유 전 실무 카톡방 문의 必)"
            sensitive.append(article)
        else:
            normal.append(article)
    if sensitive:
        print(f"[rule_filter] 민감 이슈(VIG파트너스 등) {len(sensitive)}건 분리 → 실무 확인 필요")
    return normal, sensitive


def flag_negative(articles: list):
    """
    부정 키워드(사고/소송/리콜/불만/논란/과징금/수사/적발) 포함 기사에
    NEGATIVE_FLAG 태그만 부여한다. 제거하지 않고 문서 상단 경고 섹션용으로
    남긴다 (formatter_docx.py 참조).
    """
    flagged_count = 0
    for article in articles:
        text = f"{article.get('title', '')} {article.get('summary', '')}"
        matched = [kw for kw in NEGATIVE_KEYWORDS if kw in text]
        if matched:
            article["negative_flag"] = True
            article["negative_keywords_matched"] = matched
            flagged_count += 1
        else:
            article["negative_flag"] = False
    if flagged_count:
        print(f"[rule_filter] 부정 키워드 매칭 {flagged_count}건 NEGATIVE_FLAG 부여 (제거하지 않음, 경고 섹션용)")
    return articles


def _is_oem_simple_promotion(article: dict):
    """
    3순위(완성차) 기사가 "단순 프로모션"에 해당하는지 판단한다.
    인수인계서/keywords.py 기준: 신차출시/판매동향/국내전략 관련 표현이 없고
    단순 프로모션 표현만 있으면 제외 대상.

    판단 로직(휴리스틱, 100% 정확 불가 - AI 채점 단계에서 2차 보완):
      - 허용 표현(OEM_ALLOWED_EXPRESSIONS)이 하나도 없고
      - 프로모션 표현(OEM_PROMOTION_EXPRESSIONS)이 하나라도 있으면 → 제외 대상
    """
    text = f"{article.get('title', '')} {article.get('summary', '')}"
    has_allowed = any(expr in text for expr in OEM_ALLOWED_EXPRESSIONS)
    has_promotion = any(expr in text for expr in OEM_PROMOTION_EXPRESSIONS)
    return has_promotion and not has_allowed


def filter_oem_promotion(articles: list):
    """
    3순위 완성차 키워드(keyword_category == '업계3') 기사 중 신차출시/판매동향/
    국내전략 관련 표현이 없고 단순 프로모션 표현만 있는 기사를 제외한다.
    3순위가 아닌 기사는 이 필터의 영향을 받지 않는다.

    반환값: (통과 기사 리스트, 제외된 기사 리스트)
    """
    passed, excluded = [], []
    for article in articles:
        if article.get("keyword_category") == "업계3" and _is_oem_simple_promotion(article):
            article["exclude_reason"] = "3순위 완성차 단순 프로모션 표현만 존재 (신차출시/판매동향/국내전략 표현 없음)"
            excluded.append(article)
        else:
            passed.append(article)
    if excluded:
        print(f"[rule_filter] 3순위 완성차 단순 프로모션 {len(excluded)}건 제외")
    return passed, excluded


def apply_all_filters(articles: list, days: int = 1):
    """
    1차 규칙 필터를 순서대로 모두 적용한다.
      1) filter_by_date
      2) filter_duplicate_sent
      3) filter_sensitive
      4) flag_negative
      5) filter_oem_promotion

    반환 구조:
      {
        "pass": [...],             # 최종 통과 (AI 채점 대상)
        "vig_sensitive": [...],    # 민감 이슈 (별도 실무 확인)
        "negative_flagged": [...], # pass에 포함되되 NEGATIVE_FLAG=True 인 기사 (참고용 서브셋)
        "excluded": [...]          # 완전 제외된 기사 (기보고 중복 + 단순 프로모션)
      }
    """
    print(f"[rule_filter] 1차 규칙 필터 시작 - 입력 {len(articles)}건")

    step1 = filter_by_date(articles, days=days)
    print(f"[rule_filter] Step1 날짜 필터 통과: {len(step1)}건")

    step2_pass, step2_excluded = filter_duplicate_sent(step1)

    step3_normal, step3_sensitive = filter_sensitive(step2_pass)

    step4_flagged = flag_negative(step3_normal)

    step5_pass, step5_excluded = filter_oem_promotion(step4_flagged)

    negative_flagged_subset = [a for a in step5_pass if a.get("negative_flag")]

    result = {
        "pass": step5_pass,
        "vig_sensitive": step3_sensitive,
        "negative_flagged": negative_flagged_subset,
        "excluded": step2_excluded + step5_excluded,
    }

    print(f"[rule_filter] 1차 필터 완료 → 통과 {len(result['pass'])}건 / "
          f"민감 {len(result['vig_sensitive'])}건 / "
          f"부정플래그 {len(result['negative_flagged'])}건(통과분 내 서브셋) / "
          f"완전제외 {len(result['excluded'])}건")

    return result


if __name__ == "__main__":
    # 간단한 자체 테스트 (외부 의존성 없음)
    sample = [
        {"title": "리본카, 신규 서비스 출시", "summary": "테스트", "url": "https://example.com/1",
         "published_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "keyword_category": "자사"},
        {"title": "VIG파트너스 매각 이슈", "summary": "민감 테스트", "url": "https://example.com/2",
         "published_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "keyword_category": "자사"},
        {"title": "벤츠 사은품 이벤트 진행", "summary": "단순 프로모션", "url": "https://example.com/3",
         "published_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "keyword_category": "업계3"},
    ]
    out = apply_all_filters(sample)
    print(json.dumps(out, ensure_ascii=False, indent=2))
