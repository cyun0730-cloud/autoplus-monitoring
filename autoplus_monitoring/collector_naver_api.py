# -*- coding: utf-8 -*-
"""
collector_naver_api.py
===============================================================================
[모듈 목적]
네이버 뉴스 검색 API(https://openapi.naver.com/v1/search/news.json)를 호출해
keywords.py에 정의된 자사/경쟁사/업계 키워드를 순차 검색하고, 결과를
data/raw_articles.json 에 저장한다.

[중요 - 시스템 철학]
이 모듈은 "수집"만 담당한다. 어떤 기사를 최종적으로 포함/제외할지는 이 모듈의
책임이 아니다 (그 판단은 rule_filter.py의 1차 규칙과 ai_scorer.py의 2차 AI
채점이 담당). 수집 단계에서 과도하게 필터링하면 PR 담당자의 암묵적 판단 기준을
반영할 기회 자체가 사라지므로, 이 모듈은 최대한 폭넓게 수집하는 것을 원칙으로
한다.

[TEST_MODE]
.env의 TEST_MODE=True 인 경우, 실제 네이버 API를 호출하지 않고 더미 기사 20건을
반환한다. 이를 통해 API 키가 없는 개발 환경에서도 전체 파이프라인을 검증할 수
있다.
===============================================================================
"""
import os
import json
import time
import random
from datetime import datetime, timedelta
from urllib.parse import quote

import requests
from dotenv import load_dotenv

from keywords import OWN_KEYWORDS, COMPETITOR_KEYWORDS, INDUSTRY_KEYWORDS
from rule_filter import _now_kst_naive

load_dotenv()

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")
TEST_MODE = os.getenv("TEST_MODE", "True").lower() == "true"

NAVER_NEWS_API_URL = "https://openapi.naver.com/v1/search/news.json"

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
RAW_ARTICLES_PATH = os.path.join(DATA_DIR, "raw_articles.json")


def _strip_html(text: str) -> str:
    """네이버 API 응답에 포함된 <b> 태그 등 HTML 태그와 엔티티를 제거한다."""
    import re
    text = re.sub(r"<[^>]+>", "", text or "")
    text = text.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return text.strip()


def _build_keyword_list():
    """
    keywords.py 로부터 (검색어, 카테고리) 튜플 리스트를 생성한다.
    카테고리 예: "자사", "경쟁사", "업계1"~"업계5"
    """
    kw_list = []
    for kw in OWN_KEYWORDS:
        kw_list.append((kw, "자사"))
    for kw in COMPETITOR_KEYWORDS:
        kw_list.append((kw, "경쟁사"))
    for priority, kws in INDUSTRY_KEYWORDS.items():
        for kw in kws:
            kw_list.append((kw, f"업계{priority}"))
    return kw_list


def _generate_dummy_articles():
    """
    TEST_MODE=True 일 때 사용하는 더미 기사 20건 생성기.
    실제 API 호출 없이도 rule_filter -> ai_scorer -> formatter_docx 로 이어지는
    전체 파이프라인이 정상 동작하는지 검증할 수 있도록, 실제 발생 가능한 케이스
    (자사 언급/경쟁사 신규서비스/경쟁사 단순프로모션/VIG민감/부정이슈/중복 등)를
    골고루 포함한다.
    """
    now = _now_kst_naive()
    dummy_titles = [
        ("리본카, 여름 성수기 맞아 인증중고차 물량 확대", "이데일리", "리본카", "자사"),
        ("오토플러스, 3분기 거래액 전년 대비 30% 증가", "머니투데이", "오토플러스", "자사"),
        ("케이카, AI 기반 차량 진단 서비스 신규 도입", "한국경제", "케이카", "경쟁사"),
        ("헤이딜러, 여름맞이 세차 쿠폰 증정 이벤트", "오토IN", "헤이딜러", "경쟁사"),
        ("KB차차차, 10주년 기념 경품 이벤트 진행", "디지털타임스", "KB차차차", "경쟁사"),
        ("엔카닷컴, 신규 투자 유치 및 사업 확장 발표", "전자신문", "엔카닷컴", "경쟁사"),
        ("국내 중고차 시장 규모 40조 돌파 전망", "연합뉴스", "중고차", "업계1"),
        ("렌터카 구독서비스 이용자 급증", "뉴스1", "구독서비스", "업계1"),
        ("캐피탈사, 중고차 금융 상품 다양화", "서울경제", "캐피탈+중고차", "업계2"),
        ("BMW, 신형 전기 세단 국내 출시", "조선일보", "BMW", "업계3"),
        ("벤츠 코리아, 여름 드라이빙 페스티벌 개최", "오토모닝", "벤츠", "업계3"),
        ("테슬라, 국내 판매량 3개월 연속 1위", "매일경제", "테슬라", "업계3"),
        ("아우디코리아 판촉 사은품 증정 행사", "메트로", "아우디", "업계3"),
        ("수입차 업계 판매 동향 분석 기획기사", "한겨레", "업계 트렌드", "업계4"),
        ("국산차 5월 판매 순위 발표", "동아일보", "업계 기획", "업계4"),
        ("전기차 충전 인프라 확충 계획 발표", "국민일보", "인프라", "업계5"),
        ("자율주행 관련 법규 개정 추진", "세계일보", "법규", "업계5"),
        ("리본카 차량 결함 논란, 소비자 불만 확산", "소비자매체", "리본카", "자사"),
        ("오토플러스, VIG파트너스 지분 매각설 재점화", "머니S", "오토플러스", "자사"),
        ("케이카, 여름맞이 특별 할인 이벤트 진행", "오토IN", "케이카", "경쟁사"),
    ]
    articles = []
    for i, (title, source, kw, cat) in enumerate(dummy_titles):
        articles.append({
            "title": title,
            "url": f"https://dummy.example.com/news/{now.strftime('%Y%m%d')}/{i+1}",
            "source": source,
            "journalist": random.choice(["김기자", "이기자", "박기자", ""]),
            "summary": f"{title} 관련 상세 내용 요약입니다. (TEST_MODE 더미 데이터)",
            "published_at": (now - timedelta(hours=random.randint(0, 20))).strftime("%Y-%m-%d %H:%M"),
            "search_keyword": kw,
            "keyword_category": cat,
        })
    return articles


def collect_from_naver(days: int = 1):
    """
    네이버 뉴스 검색 API를 호출해 키워드별 기사를 수집한다.
    - TEST_MODE=True: 더미 기사 20건 반환 (외부 호출 없음)
    - TEST_MODE=False: 실제 API 호출, NAVER_CLIENT_ID/SECRET 필요

    반환값: 기사 딕셔너리 리스트. 각 기사 필드는 다음과 같다.
      title, url, source, journalist, summary, published_at,
      search_keyword, keyword_category
    """
    if TEST_MODE:
        print("[collector_naver_api] TEST_MODE=True → 네이버 API 호출 없이 더미 기사 20건 반환")
        articles = _generate_dummy_articles()
    else:
        if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
            raise RuntimeError(
                "[collector_naver_api] NAVER_CLIENT_ID/SECRET 이 .env 에 설정되지 않았습니다. "
                "실 운영 시 확인/교체 필요."
            )
        keyword_list = _build_keyword_list()
        articles = []
        seen_urls = set()
        headers = {
            "X-Naver-Client-Id": NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
        }
        for kw, category in keyword_list:
            params = {"query": kw, "display": 20, "sort": "date"}
            try:
                resp = requests.get(NAVER_NEWS_API_URL, headers=headers, params=params, timeout=10)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                # 네트워크 오류 등은 해당 키워드만 건너뛰고 계속 진행 (전체 파이프라인 중단 방지)
                print(f"[collector_naver_api] 키워드 '{kw}' 수집 실패: {e}")
                continue

            for item in data.get("items", []):
                url = item.get("originallink") or item.get("link")
                if not url or url in seen_urls:
                    continue  # URL 기준 중복 제거
                seen_urls.add(url)
                articles.append({
                    "title": _strip_html(item.get("title", "")),
                    "url": url,
                    "source": "",  # 네이버 API는 매체명을 직접 주지 않음 → media_normalizer.py 에서 도메인 기반 추정
                    "journalist": "",  # 네이버 API는 기자명을 제공하지 않음 → 본문 크롤링 필요(추후 고도화)
                    "summary": _strip_html(item.get("description", "")),
                    "published_at": item.get("pubDate", ""),
                    "search_keyword": kw,
                    "keyword_category": category,
                })
            time.sleep(0.15)  # API 호출 과다 방지용 짧은 대기

    return articles


def save_raw_articles(articles, path: str = RAW_ARTICLES_PATH):
    """수집된 기사 리스트를 data/raw_articles.json 에 저장한다."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "_comment": "수집기(collector_*.py)가 수집한 원본 기사 캐시. main.py 실행 시마다 덮어쓰기 된다.",
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "articles": articles,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[collector_naver_api] {len(articles)}건 수집 완료 → {path} 저장")


if __name__ == "__main__":
    arts = collect_from_naver()
    save_raw_articles(arts)
