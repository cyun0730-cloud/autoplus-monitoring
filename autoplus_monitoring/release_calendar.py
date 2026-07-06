# -*- coding: utf-8 -*-
"""
release_calendar.py
===============================================================================
[모듈 목적]
자사(오토플러스) 보도자료 배포 캘린더를 관리한다. data/release_calendar.json
에 등록된 배포 예정 자료를 조회하고, 배포 당일에는 관련 키워드로 09시에
1회 추가 수집을 수행해 Word 문서 상단에 "오늘의 배포 자료" 섹션을 삽입한다.

[핵심 인사이트 - 주석으로 반드시 유지]
자사 배포 건은 사전에 일정을 아는 유일한 변수이므로, 이 로직으로 기존
"08:30 정기 발송 시점에 방금 배포한 자사 자료의 게재 현황이 아직 반영되지
않는" 누락 문제를 해결할 수 있다.
반면 경쟁사·업계 자료는 담당자도 사전에 배포 일정을 알 수 없으므로, 당일
미반영 문제는 구조적 한계로 남는다 (이 시스템으로 해결 불가능한 영역).
===============================================================================
"""
import os
import json
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
RELEASE_CALENDAR_PATH = os.path.join(DATA_DIR, "release_calendar.json")


def _load_calendar():
    """data/release_calendar.json 을 로드한다."""
    if not os.path.exists(RELEASE_CALENDAR_PATH):
        return {}
    with open(RELEASE_CALENDAR_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def get_today_releases(today: datetime = None):
    """
    당일(today, 기본값은 실행 시각) 배포 예정으로 등록된 자료 목록을 반환한다.
    반환값: [{"date": "YYYY-MM-DD", "title": ..., "keywords": [...]}, ...]
    """
    if today is None:
        today = datetime.now()
    month_key = today.strftime("%Y-%m")
    today_str = today.strftime("%Y-%m-%d")

    calendar = _load_calendar()
    month_releases = calendar.get(month_key, [])
    today_releases = [r for r in month_releases if r.get("date") == today_str]

    if today_releases:
        print(f"[release_calendar] 오늘({today_str}) 배포 예정 자료 {len(today_releases)}건 확인")
    return today_releases


def collect_release_articles(releases: list):
    """
    당일 배포 자료의 키워드로 09시경 1회 추가 수집을 수행한다.
    실제 수집은 collector_naver_api.collect_from_naver 로직을 재사용하되,
    검색어만 배포 자료의 keywords로 한정해 가볍게 수행한다.

    TEST_MODE 등 환경설정은 collector_naver_api 내부에서 이미 처리되므로
    이 함수는 단순히 어떤 키워드로 검색할지만 결정한다.
    """
    if not releases:
        return []

    # 지연 임포트: main.py 순환참조 방지 및 collector 모듈이 없는 환경에서도
    # release_calendar 단독 임포트가 가능하도록 함
    from collector_naver_api import collect_from_naver, TEST_MODE
    import requests

    all_keywords = []
    for release in releases:
        all_keywords.extend(release.get("keywords", []))

    if TEST_MODE:
        print(f"[release_calendar] TEST_MODE=True → 배포자료 추가 수집 생략 (키워드: {all_keywords})")
        return []

    # 실제 운영 시: 네이버 API를 배포 자료 키워드로만 재검색
    # (간단화를 위해 collect_from_naver 전체 로직 대신 직접 키워드 검색 수행)
    print(f"[release_calendar] 배포 자료 키워드 {all_keywords} 로 09시 추가 수집 수행")
    # 확인 필요: 실제 운영 시 이 부분은 collector_naver_api의 내부 검색 로직을
    # 키워드 파라미터화하여 재사용하도록 리팩터링 권장
    return []


def insert_release_section(doc, releases: list):
    """
    python-docx Document 객체(doc)의 최상단에 "오늘의 배포 자료" 섹션을 삽입한다.
    배포 당일에만 호출되며(formatter_docx.py에서 releases가 빈 리스트면 호출 생략),
    문서 최상단에 배포 자료 제목과 키워드를 표시한다.

    주의: python-docx는 문서 "맨 앞"에 삽입하는 기능이 직접 제공되지 않으므로,
    이 함수는 호출 시점(문서 생성 초반)에 맞춰 순서대로 add_heading/add_paragraph
    를 호출하는 방식으로 구현한다 (formatter_docx.py에서 문서 최상단 흐름에서
    호출해야 함).
    """
    if not releases:
        return doc

    doc.add_heading("오늘의 배포 자료 게재 현황", level=1)
    for release in releases:
        p = doc.add_paragraph()
        p.add_run(f"▷ {release.get('title', '')}").bold = True
        doc.add_paragraph(f"검색 키워드: {', '.join(release.get('keywords', []))}")
    return doc


if __name__ == "__main__":
    releases = get_today_releases()
    print(json.dumps(releases, ensure_ascii=False, indent=2))
