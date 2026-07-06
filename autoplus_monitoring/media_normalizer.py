# -*- coding: utf-8 -*-
"""
media_normalizer.py
===============================================================================
[모듈 목적]
수집된 기사의 매체명·기자명을 정규화한다.
- URL 도메인을 기준으로 매체명·매체그룹을 추정 (data/media_domain_map.json)
- 기자명 오탈자·이명(異名)을 마스터 DB와 대조해 정규화

[왜 필요한가]
동일 매체라도 네이버 API/Google RSS에서 매체명이 다르게 표기되거나 아예
비어있는 경우가 많다 (collector_naver_api.py 주석 참조: 네이버 API는 매체명을
직접 제공하지 않음). 게재보고(report_formatter.py) 및 문서 정렬
(formatter_docx.py, MEDIA_PRIORITY_ORDER)을 위해서는 반드시 정규화된 매체
그룹 정보가 필요하다.
===============================================================================
"""
import os
import json
from urllib.parse import urlparse

from rule_filter import _now_kst_naive

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
MEDIA_MAP_PATH = os.path.join(DATA_DIR, "media_domain_map.json")
UNCONFIRMED_MEDIA_PATH = os.path.join(DATA_DIR, "unconfirmed_media.json")

# 기자 마스터 DB (실물 파일 없을 시 더미 dict로 대체)
# 확인 필요: 실제 운영 시에는 팀 내 기자 마스터 DB(csv/excel)를 연동해야 한다.
# 현재는 더미 예시로 오탈자/이명 패턴만 시연한다.
DEFAULT_JOURNALIST_MASTER_DB = {
    "김철수": ["김철수기자", "철수 김"],
    "이영희": ["이영희 기자", "영희이"],
}

_unconfirmed_media_domains = set()
_unconfirmed_journalists = set()


def _load_media_map():
    """data/media_domain_map.json 을 로드한다. 파일이 없으면 빈 dict 반환."""
    if not os.path.exists(MEDIA_MAP_PATH):
        return {}
    with open(MEDIA_MAP_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    # "_comment" 등 메타 키는 제외하고 실제 도메인 매핑만 반환
    return {k: v for k, v in raw.items() if not k.startswith("_")}


_MEDIA_MAP = _load_media_map()


def normalize_media_name(url: str):
    """
    URL의 도메인을 data/media_domain_map.json 과 대조해 매체명·그룹을 반환한다.
    매칭 실패 시 "미확인매체_[도메인]" 형태로 반환하고, 별도 확인 리스트
    (_unconfirmed_media_domains)에 누적해 추후 담당자가 media_domain_map.json에
    수동 추가할 수 있도록 한다.

    반환값: {"name": str, "group": str, "domain": str}
    """
    if not url:
        return {"name": "미확인매체_(URL없음)", "group": "온라인", "domain": ""}

    try:
        domain = urlparse(url).netloc.lower()
        # www. 접두사 제거
        if domain.startswith("www."):
            domain = domain[4:]
    except Exception:
        domain = ""

    # 정확히 일치하는 도메인 먼저 확인
    if domain in _MEDIA_MAP:
        info = _MEDIA_MAP[domain]
        return {"name": info.get("name", domain), "group": info.get("group", "온라인"), "domain": domain}

    # 서브도메인 등을 고려해 부분 일치도 확인 (예: biz.chosun.com vs chosun.com)
    for mapped_domain, info in _MEDIA_MAP.items():
        if domain.endswith(mapped_domain):
            return {"name": info.get("name", domain), "group": info.get("group", "온라인"), "domain": domain}

    # 매칭 실패 → 미확인 매체 리스트에 누적
    _unconfirmed_media_domains.add(domain)
    return {"name": f"미확인매체_{domain}", "group": "온라인", "domain": domain}


def normalize_journalist_name(name: str, master_db: dict = None):
    """
    기자명을 마스터 DB(master_db)와 대조해 오탈자·이명을 정규화한다.
    master_db가 주어지지 않으면 DEFAULT_JOURNALIST_MASTER_DB(더미)를 사용한다.
    매핑을 찾지 못하면 원본 이름을 그대로 반환하고, 미확인 리스트에 추가한다.

    master_db 형식: {"정규화된 이름": ["이명1", "이명2", ...]}
    """
    if master_db is None:
        master_db = DEFAULT_JOURNALIST_MASTER_DB

    if not name:
        return ""

    name_stripped = name.strip()

    # 이미 정규화된 이름인 경우
    if name_stripped in master_db:
        return name_stripped

    # 이명 리스트에서 검색
    for canonical, aliases in master_db.items():
        if name_stripped in aliases:
            return canonical

    # 매핑 없음 → 원본 유지 + 미확인 리스트 추가
    _unconfirmed_journalists.add(name_stripped)
    return name_stripped


def get_unconfirmed_media_domains():
    """이번 실행 중 매핑되지 않은 도메인 목록을 반환한다 (담당자 검토용)."""
    return sorted(_unconfirmed_media_domains)


def get_unconfirmed_journalists():
    """이번 실행 중 매핑되지 않은 기자명 목록을 반환한다 (담당자 검토용)."""
    return sorted(_unconfirmed_journalists)


# =============================================================================
# 미확인 매체 영속 저장 + 대시보드 등록 워크플로우 (2026-07-06 추가)
# -----------------------------------------------------------------------------
# 배경: 실제 담당자가 수기로 작성한 리포트와 대조해보니, 매번 새로운 매체가
# 계속 발견됐다(한 번에 13개, 그 다음 대조에서 또 70개 이상). 이걸 개발자가
# 매번 하나하나 도메인을 검색해 media_domain_map.json에 하드코딩하는 방식은
# 확장성이 없고, 잘못된 도메인을 추측해 등록하면 오히려 오매칭 위험도 있다.
#
# 대신, "미확인 매체"를 실행할 때마다 파일에 누적 기록해두고(예시 기사
# 제목/URL 포함), 담당자가 대시보드(/settings)에서 실제 화면에 뜬 정보를
# 보고 직접 매체명/그룹을 입력해 즉시 등록할 수 있도록 UI를 제공한다.
# 담당자가 원래 매일 보는 실제 매체이므로, 개발자가 추측하는 것보다 훨씬
# 정확하고 빠르게 계속 채워나갈 수 있다.
# =============================================================================
def _load_unconfirmed_media_store():
    """data/unconfirmed_media.json 을 로드한다 (없으면 빈 dict)."""
    if not os.path.exists(UNCONFIRMED_MEDIA_PATH):
        return {}
    with open(UNCONFIRMED_MEDIA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_unconfirmed_media_store(store: dict):
    with open(UNCONFIRMED_MEDIA_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


def _record_unconfirmed_media(updates: dict):
    """
    이번 실행에서 새로 발견된 미확인 도메인들을 기존 저장소에 병합해 저장한다.
    updates: {domain: {"example_title": str, "example_url": str}}
    """
    if not updates:
        return
    store = _load_unconfirmed_media_store()
    now_str = _now_kst_naive().strftime("%Y-%m-%d %H:%M:%S")
    for domain, info in updates.items():
        entry = store.get(domain, {"count": 0, "first_seen": now_str})
        entry["count"] = entry.get("count", 0) + info.get("count", 1)
        entry["example_title"] = info.get("example_title", entry.get("example_title", ""))
        entry["example_url"] = info.get("example_url", entry.get("example_url", ""))
        entry["last_seen"] = now_str
        store[domain] = entry
    _save_unconfirmed_media_store(store)


def get_unconfirmed_media_store():
    """대시보드 표시용: 아직 등록되지 않은 매체 도메인과 예시 기사 정보를 반환한다."""
    return _load_unconfirmed_media_store()


def resolve_unconfirmed_media(domain: str, name: str, group: str):
    """
    담당자가 미확인 도메인에 실제 매체명/그룹을 지정해 media_domain_map.json에
    즉시 등록하고, 미확인 목록에서 제거한다. 등록 즉시 현재 실행 중인 서버의
    메모리(_MEDIA_MAP)에도 반영되어 재시작 없이 다음 기사부터 바로 적용된다.
    """
    global _MEDIA_MAP

    domain = (domain or "").strip().lower()
    name = (name or "").strip()
    group = (group or "온라인").strip()
    if not domain or not name:
        return False, "도메인과 매체명은 필수입니다."

    full_map = {}
    if os.path.exists(MEDIA_MAP_PATH):
        with open(MEDIA_MAP_PATH, "r", encoding="utf-8") as f:
            full_map = json.load(f)
    full_map[domain] = {"name": name, "group": group}
    with open(MEDIA_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(full_map, f, ensure_ascii=False, indent=2)

    _MEDIA_MAP[domain] = {"name": name, "group": group}

    store = _load_unconfirmed_media_store()
    store.pop(domain, None)
    _save_unconfirmed_media_store(store)

    return True, f"'{domain}' → '{name}' ({group})로 등록되었습니다."


def normalize_articles(articles: list):
    """
    기사 리스트 전체에 대해 매체명/기자명 정규화를 일괄 적용한다.
    기존 필드(source, journalist)를 정규화된 값으로 덮어쓰고,
    media_group 필드를 새로 추가한다.
    """
    unconfirmed_updates = {}

    for article in articles:
        media_info = normalize_media_name(article.get("url", ""))
        # 이미 source가 채워져 있어도(예: Google RSS) media_domain_map 기준으로
        # 재확인하여 그룹 정보를 부여한다. source 텍스트 자체는 존중하되 비어있으면 채운다.
        if not article.get("source"):
            article["source"] = media_info["name"]
        article["media_group"] = media_info["group"]

        if media_info["name"].startswith("미확인매체_") and media_info["domain"]:
            domain = media_info["domain"]
            entry = unconfirmed_updates.setdefault(domain, {"count": 0})
            entry["count"] += 1
            entry.setdefault("example_title", article.get("title", ""))
            entry.setdefault("example_url", article.get("url", ""))

        if article.get("journalist"):
            article["journalist"] = normalize_journalist_name(article["journalist"])

    _record_unconfirmed_media(unconfirmed_updates)

    unconfirmed_domains = get_unconfirmed_media_domains()
    if unconfirmed_domains:
        print(f"[media_normalizer] 미확인 매체 도메인 {len(unconfirmed_domains)}건 발견: "
              f"{unconfirmed_domains} → /settings 화면에서 등록 가능")

    return articles


if __name__ == "__main__":
    test_articles = [
        {"url": "https://www.edaily.co.kr/news/12345", "source": "", "journalist": "김철수기자"},
        {"url": "https://unknown-site.example.com/a", "source": "", "journalist": ""},
    ]
    result = normalize_articles(test_articles)
    print(json.dumps(result, ensure_ascii=False, indent=2))
