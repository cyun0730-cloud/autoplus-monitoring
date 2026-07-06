# -*- coding: utf-8 -*-
"""
collector_media_specific.py
===============================================================================
[모듈 목적]
이데일리·뉴스1 등 오토플러스 인수인계서에 명시된 "주요 관리 매체"(협찬·관계
매체)를 개별 크롤링하기 위한 자리(TODO)를 마련한다.

[왜 TODO 인가]
매체별로 HTML 구조와 robots.txt/이용약관이 상이해, 일괄적인 크롤러 로직을
적용할 수 없다. 매체별로 개별 검토(구조 분석, 약관 검토, 요청 빈도 제한 등)가
필요하므로, 이번 1차 구축 단계에서는 함수 시그니처와 통합 지점만 마련하고
실제 구현은 추후 진행한다.

인수인계서 "주요 관리 미디어" 참고: 이데일리(협찬 진행), 뉴스1(도서 구매),
이뉴스투데이(포럼 티켓 구매), 헤럴드경제(포럼 티켓 구매) 등
===============================================================================
"""
# 매체별 HTML 구조·이용약관이 상이해 추후 개별 검토 필요


def collect_from_edaily():
    """
    이데일리 개별 수집 함수 (TODO).
    매체별 HTML 구조·이용약관이 상이해 추후 개별 검토 필요.
    현재는 네이버 API / Google RSS 수집 결과에 이데일리 기사가 포함되므로
    긴급하지 않으나, 협찬 매체 특성상 우선 노출/속보 확인 목적으로 추후
    개별 구현을 고려한다.
    """
    # TODO: 이데일리 자체 검색 페이지 또는 RSS 피드 구조 분석 후 구현
    return []


def collect_from_news1():
    """
    뉴스1 개별 수집 함수 (TODO).
    매체별 HTML 구조·이용약관이 상이해 추후 개별 검토 필요.
    """
    # TODO: 뉴스1 자체 검색 페이지 또는 RSS 피드 구조 분석 후 구현
    return []


def collect_from_enewstoday():
    """
    이뉴스투데이 개별 수집 함수 (TODO).
    매체별 HTML 구조·이용약관이 상이해 추후 개별 검토 필요.
    """
    # TODO: 이뉴스투데이 자체 검색 페이지 구조 분석 후 구현
    return []


def collect_from_heraldcorp():
    """
    헤럴드경제 개별 수집 함수 (TODO).
    매체별 HTML 구조·이용약관이 상이해 추후 개별 검토 필요.
    """
    # TODO: 헤럴드경제 자체 검색 페이지 구조 분석 후 구현
    return []


def collect_all_media_specific():
    """
    개별 매체 수집 함수를 모두 호출해 통합하는 진입점.
    현재는 모든 개별 함수가 TODO(빈 리스트 반환) 상태이므로 항상 빈 리스트를
    반환하지만, main.py 파이프라인에서 이 함수를 이미 호출하도록 연결해 두어
    추후 개별 함수 구현 시 파이프라인 수정 없이 바로 통합되도록 한다.
    """
    articles = []
    articles.extend(collect_from_edaily())
    articles.extend(collect_from_news1())
    articles.extend(collect_from_enewstoday())
    articles.extend(collect_from_heraldcorp())
    if not articles:
        print("[collector_media_specific] 개별 매체 수집 함수는 현재 TODO 상태입니다. "
              "매체별 HTML 구조·이용약관이 상이해 추후 개별 검토 필요.")
    return articles


if __name__ == "__main__":
    result = collect_all_media_specific()
    print(f"[collector_media_specific] 수집 결과: {len(result)}건")
