# -*- coding: utf-8 -*-
"""
collector_google_rss.py
===============================================================================
[모듈 목적]
Google News RSS(https://news.google.com/rss/search?q={키워드}&hl=ko&gl=KR&ceid=KR:ko)
를 feedparser로 수집해, collector_naver_api.py의 결과를 보완한다.
네이버 뉴스 검색 API가 다루지 않는 매체나 최신 기사를 보완 수집하는 목적이다.

[수집 우선순위]
자사 키워드를 최우선 적용하고, 경쟁사/업계 키워드로 확장 가능한 함수 구조로
설계한다 (extend_to_competitor_and_industry 참조). 이는 "자사 언급 누락은
PR팀 입장에서 가장 치명적인 실패"라는 암묵적 판단 기준을 반영한 것이다.

[중복 제거]
네이버 결과와 URL 기준 중복 제거 후 병합한다 (merge_with_naver_results 참조).

[TEST_MODE]
.env의 TEST_MODE=True 인 경우 실제 RSS 호출 없이 빈 리스트를 반환한다
(네이버 수집기의 더미 데이터로 이미 전체 플로우 테스트가 가능하므로, 중복
더미 데이터 생성을 피하기 위함).
===============================================================================
"""
import os
import time
from urllib.parse import quote

import feedparser
from dotenv import load_dotenv

from keywords import OWN_KEYWORDS, COMPETITOR_KEYWORDS, INDUSTRY_KEYWORDS

load_dotenv()
TEST_MODE = os.getenv("TEST_MODE", "True").lower() == "true"

GOOGLE_NEWS_RSS_TEMPLATE = "https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"


def _fetch_rss_for_keyword(keyword: str, category: str):
    """단일 키워드에 대해 Google News RSS를 조회하고 기사 리스트로 변환한다."""
    url = GOOGLE_NEWS_RSS_TEMPLATE.format(query=quote(keyword))
    articles = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            # Google News RSS의 title은 보통 "기사제목 - 매체명" 형식
            raw_title = entry.get("title", "")
            source = ""
            title = raw_title
            if " - " in raw_title:
                title, source = raw_title.rsplit(" - ", 1)
            articles.append({
                "title": title.strip(),
                "url": entry.get("link", ""),
                "source": source.strip(),
                "journalist": "",
                "summary": entry.get("summary", ""),
                "published_at": entry.get("published", ""),
                "search_keyword": keyword,
                "keyword_category": category,
            })
    except Exception as e:
        print(f"[collector_google_rss] 키워드 '{keyword}' RSS 수집 실패: {e}")
    return articles


def collect_own_keywords_only():
    """자사 키워드만 우선 수집 (기본 호출 함수)."""
    if TEST_MODE:
        print("[collector_google_rss] TEST_MODE=True → Google RSS 호출 생략 (빈 리스트 반환)")
        return []
    all_articles = []
    for kw in OWN_KEYWORDS:
        all_articles.extend(_fetch_rss_for_keyword(kw, "자사"))
        time.sleep(0.2)
    return all_articles


def extend_to_competitor_and_industry():
    """
    자사 키워드 수집으로 부족할 경우, 경쟁사/업계 키워드로 확장 수집한다.
    운영 부하(요청 과다)를 고려해 기본 파이프라인에서는 자동 호출하지 않고,
    필요 시 main.py 또는 web_app.py 수동실행 옵션에서 선택적으로 호출하도록
    별도 함수로 분리해 둔다.
    """
    if TEST_MODE:
        print("[collector_google_rss] TEST_MODE=True → 확장 수집 생략")
        return []
    all_articles = []
    for kw in COMPETITOR_KEYWORDS:
        all_articles.extend(_fetch_rss_for_keyword(kw, "경쟁사"))
        time.sleep(0.2)
    for priority, kws in INDUSTRY_KEYWORDS.items():
        for kw in kws:
            all_articles.extend(_fetch_rss_for_keyword(kw, f"업계{priority}"))
            time.sleep(0.2)
    return all_articles


def merge_with_naver_results(naver_articles: list, rss_articles: list):
    """
    네이버 API 수집 결과와 Google RSS 수집 결과를 URL 기준으로 중복 제거하며 병합한다.
    네이버 결과를 우선으로 하고(먼저 수집된 매체 정보 등을 신뢰), RSS 결과 중
    URL이 겹치지 않는 항목만 추가한다.
    """
    seen_urls = {a["url"] for a in naver_articles if a.get("url")}
    merged = list(naver_articles)
    added = 0
    for a in rss_articles:
        url = a.get("url")
        if url and url not in seen_urls:
            merged.append(a)
            seen_urls.add(url)
            added += 1
    print(f"[collector_google_rss] RSS 보완 수집으로 {added}건 신규 추가 (네이버 결과와 병합)")
    return merged


if __name__ == "__main__":
    own = collect_own_keywords_only()
    print(f"[collector_google_rss] 자사 키워드 수집 {len(own)}건")
