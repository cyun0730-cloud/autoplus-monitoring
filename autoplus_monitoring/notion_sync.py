"""
notion_sync.py
===============================================================================
Render 무료 플랜은 영구 디스크가 없어, 코드를 재배포할 때마다 data/daily_archive
에 쌓아둔 일자별 모니터링 기록이 초기화될 위험이 있다. 이 모듈은 매일 실행이
끝날 때마다 "포함" 판정된 기사들을 Notion 데이터베이스에도 함께 기록해,
Render가 초기화되더라도 과거 기록이 Notion 쪽에 영구히 남도록 한다.

[설계 결정]
- 전체(포함/제외/검토필요) 기사를 다 옮기면 기사당 API 호출이 여러 번 필요해
  하루 수백 건 기준 몇 분씩 걸릴 수 있다. 실제로 "영구 보관할 가치가 있는" 것은
  최종 리포트에 실리는 "포함" 기사이므로, 우선 포함 기사만 동기화한다.
  (제외/검토필요까지 필요해지면 push_articles_to_notion 호출부만 확장하면 됨)
- 같은 기사가 여러 번 동기화되어 중복 생성되는 것을 막기 위해, 생성 전에
  URL 속성으로 기존 페이지를 조회해 이미 있으면 건너뛴다.
- Notion 연동이 실패해도(토큰 미설정, 네트워크 오류 등) 전체 모니터링
  파이프라인이 죽지 않도록 모든 호출부에서 예외를 잡아 로그만 남긴다.
===============================================================================
"""
import os
import time
import requests

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")
NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}


def is_configured():
    """Notion 연동에 필요한 환경변수가 설정되어 있는지 확인한다."""
    return bool(NOTION_API_KEY and NOTION_DATABASE_ID)


def _find_existing_page(url: str):
    """URL 속성 기준으로 이미 등록된 페이지가 있는지 조회한다 (중복 방지)."""
    if not url:
        return None
    payload = {"filter": {"property": "URL", "url": {"equals": url}}}
    resp = requests.post(
        f"{NOTION_API_BASE}/databases/{NOTION_DATABASE_ID}/query",
        headers=_HEADERS, json=payload, timeout=10,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    return results[0] if results else None


def _build_properties(date_str: str, article: dict):
    """기사 dict를 Notion 페이지 속성(properties) 형식으로 변환한다."""
    title = article.get("title", "(제목 없음)")[:200]  # Notion 제목 길이 제한 여유
    url = article.get("url", "")
    source = article.get("source", "미확인매체")
    category = article.get("keyword_category", "")
    decision = article.get("ai_decision", "")
    keyword = article.get("search_keyword", "")
    published_display = article.get("published_at_display", "")

    properties = {
        "제목": {"title": [{"text": {"content": title}}]},
        "매체": {"rich_text": [{"text": {"content": source}}]},
        "검색키워드": {"rich_text": [{"text": {"content": keyword}}]},
        "실행일": {"date": {"start": date_str}},
    }
    if url:
        properties["URL"] = {"url": url}
    if category:
        properties["구분"] = {"select": {"name": category}}
    if decision:
        properties["판단"] = {"select": {"name": decision}}
    # 발행일은 "MM/DD HH:MM" 표시용 문자열이라 Notion date 타입에 그대로 못 쓴다.
    # 실행일(date_str, YYYY-MM-DD)만 date로 넣고, 발행 표시값은 텍스트로 보존한다.
    if published_display:
        properties["검색키워드"]["rich_text"][0]["text"]["content"] += f"  (발행: {published_display})"

    return properties


def push_articles_to_notion(date_str: str, articles: list, max_retries: int = 2):
    """
    지정한 실행일(date_str, YYYY-MM-DD)의 기사 리스트를 Notion 데이터베이스에
    동기화한다. 이미 등록된 URL은 건너뛴다.

    반환값: {"synced": 성공 건수, "skipped": 이미 있어서 건너뛴 건수, "failed": 실패 건수}
    """
    result = {"synced": 0, "skipped": 0, "failed": 0}

    if not is_configured():
        print("[notion_sync] NOTION_API_KEY / NOTION_DATABASE_ID 미설정 - 동기화 건너뜀")
        return result

    for article in articles:
        url = article.get("url", "")
        try:
            if _find_existing_page(url):
                result["skipped"] += 1
                continue

            properties = _build_properties(date_str, article)
            resp = None
            for attempt in range(max_retries + 1):
                resp = requests.post(
                    f"{NOTION_API_BASE}/pages",
                    headers=_HEADERS,
                    json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": properties},
                    timeout=10,
                )
                if resp.status_code == 200:
                    break
                if resp.status_code == 429:  # rate limit - 잠깐 쉬고 재시도
                    time.sleep(1.0)
                    continue
                break

            if resp is not None and resp.status_code == 200:
                result["synced"] += 1
            else:
                result["failed"] += 1
                print(f"[notion_sync] 등록 실패 (status={resp.status_code if resp else 'N/A'}): "
                      f"{article.get('title', '')[:40]} - {resp.text[:200] if resp else ''}")

            time.sleep(0.35)  # Notion API 요청 제한(초당 약 3건) 여유 있게 준수
        except Exception as e:
            result["failed"] += 1
            print(f"[notion_sync] 예외 발생: {article.get('title', '')[:40]} - {e}")

    print(f"[notion_sync] {date_str} 동기화 완료 - 신규 {result['synced']}건 / "
          f"중복스킵 {result['skipped']}건 / 실패 {result['failed']}건")
    return result


def test_connection():
    """
    Notion 연동 설정이 올바른지 확인한다 (데이터베이스 조회 1회 시도).
    반환값: (성공 여부, 메시지)
    """
    if not is_configured():
        return False, "NOTION_API_KEY 또는 NOTION_DATABASE_ID가 설정되지 않았습니다."
    try:
        resp = requests.get(
            f"{NOTION_API_BASE}/databases/{NOTION_DATABASE_ID}",
            headers=_HEADERS, timeout=10,
        )
        if resp.status_code == 200:
            title_obj = resp.json().get("title", [])
            db_title = title_obj[0]["plain_text"] if title_obj else "(제목 없음)"
            return True, f"연결 성공! 데이터베이스: {db_title}"
        return False, f"연결 실패 (status={resp.status_code}): {resp.text[:300]}"
    except Exception as e:
        return False, f"연결 실패: {e}"
