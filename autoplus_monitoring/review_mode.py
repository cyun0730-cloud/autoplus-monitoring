# -*- coding: utf-8 -*-
"""
review_mode.py
===============================================================================
[모듈 목적]
주간 리뷰 모드. 실제 담당자가 발송한 모니터링 본문의 URL 목록(actual_sent_urls)
과 자동화 시스템의 결과(auto_result_articles)를 대조해:
  - "놓친 기사"  : 담당자는 포함했으나 자동화는 놓친 기사
  - "잘못 포함한 기사" : 자동화는 포함했으나 담당자는 제외한 기사
를 산출하고, label_examples.json 업데이트 후보(title, source, keyword,
제안 label, reason 초안)를 자동 제안한다.

[실행 방법]
주 1회 웹 UI(/review, /review/run) 또는 CLI(--review --sent-urls "url1,url2,...")
로 실행한다.

[핵심 메시지 - README/로그에 "로드맵 설명용 참고 메모(확정 성과 지표 아님)"로 명시]
"정확도는 데이터가 아니라 기준 이관의 문제이며, 대조 데이터가 이미 쌓이고 있어
2~3주 내 선별 로직 개선이 가능하고, 8주간 리뷰 루틴이 누적되면 하이브리드
운영에서 목표 정확도 도달을 기대할 수 있다."
===============================================================================
"""
import os
import json
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LABEL_EXAMPLES_PATH = os.path.join(DATA_DIR, "label_examples.json")

ROADMAP_NOTE = (
    "정확도는 데이터가 아니라 기준 이관의 문제이며, 대조 데이터가 이미 쌓이고 있어 "
    "2~3주 내 선별 로직 개선이 가능하고, 8주간 리뷰 루틴이 누적되면 하이브리드 운영에서 "
    "목표 정확도 도달을 기대할 수 있다."
    " (※ 로드맵 설명용 참고 메모이며, 확정 성과 지표가 아님)"
)


def _load_label_examples():
    if not os.path.exists(LABEL_EXAMPLES_PATH):
        return []
    with open(LABEL_EXAMPLES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_label_examples(examples: list):
    with open(LABEL_EXAMPLES_PATH, "w", encoding="utf-8") as f:
        json.dump(examples, f, ensure_ascii=False, indent=2)


def run_review_mode(actual_sent_urls: list, auto_result_articles: list):
    """
    실제 발송본 URL 목록과 자동화 결과(AI 채점까지 완료된 기사 리스트, 즉
    ai_decision 필드를 가진 articles)를 대조한다.

    Args:
        actual_sent_urls: 담당자가 실제로 발송한 모니터링에 포함된 기사 URL 목록
        auto_result_articles: ai_scorer 채점이 완료된 전체 기사 리스트
            (포함/제외/검토필요 모두 포함된 상태여야 비교가 의미 있음)

    Returns:
        {
          "missed_articles": [...],      # 담당자는 포함, 자동화는 누락
          "wrongly_included_articles": [...],  # 자동화는 포함, 담당자는 제외
          "label_update_candidates": [...],    # label_examples.json 추가 후보
          "roadmap_note": str,
        }
    """
    actual_url_set = set(actual_sent_urls)
    auto_url_map = {a.get("url"): a for a in auto_result_articles if a.get("url")}
    auto_included_urls = {
        a.get("url") for a in auto_result_articles
        if a.get("url") and a.get("ai_decision") == "포함"
    }

    # 놓친 기사: 담당자 발송 URL 중 자동화가 "포함"하지 않은 것
    missed_articles = []
    for url in actual_url_set:
        if url not in auto_included_urls:
            article = auto_url_map.get(url, {"url": url, "title": "(자동화 미수집 기사)", "source": ""})
            missed_articles.append(article)

    # 잘못 포함한 기사: 자동화가 "포함"했으나 담당자 발송 목록에 없는 것
    wrongly_included_articles = [
        a for a in auto_result_articles
        if a.get("url") in auto_included_urls and a.get("url") not in actual_url_set
    ]

    # label_examples.json 업데이트 후보 생성
    label_update_candidates = []
    for article in missed_articles:
        label_update_candidates.append({
            "title": article.get("title", ""),
            "source": article.get("source", ""),
            "keyword": article.get("search_keyword", ""),
            "suggested_label": "포함",
            "reason_draft": "담당자가 실제 발송했으나 자동화가 놓친 기사 - 포함 기준 재확인 필요",
        })
    for article in wrongly_included_articles:
        label_update_candidates.append({
            "title": article.get("title", ""),
            "source": article.get("source", ""),
            "keyword": article.get("search_keyword", ""),
            "suggested_label": "제외",
            "reason_draft": "자동화는 포함했으나 담당자는 발송하지 않은 기사 - 제외 기준 재확인 필요",
        })

    print(f"[review_mode] 리뷰 완료 - 놓친 기사 {len(missed_articles)}건 / "
          f"잘못 포함한 기사 {len(wrongly_included_articles)}건 / "
          f"라벨 업데이트 후보 {len(label_update_candidates)}건")
    print(f"[review_mode] {ROADMAP_NOTE}")

    return {
        "missed_articles": missed_articles,
        "wrongly_included_articles": wrongly_included_articles,
        "label_update_candidates": label_update_candidates,
        "roadmap_note": ROADMAP_NOTE,
        "reviewed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def apply_label_update_candidates(candidates: list, approved_indices: list = None):
    """
    담당자가 승인한 라벨 업데이트 후보를 실제 label_examples.json 에 반영한다.
    approved_indices가 None이면 전체 candidates를 반영, 리스트가 주어지면
    해당 인덱스만 반영한다 (웹 UI에서 담당자가 선택적으로 승인하는 시나리오 지원).
    """
    examples = _load_label_examples()

    targets = candidates if approved_indices is None else [
        candidates[i] for i in approved_indices if 0 <= i < len(candidates)
    ]

    added = 0
    for cand in targets:
        examples.append({
            "title": cand.get("title", ""),
            "source": cand.get("source", ""),
            "keyword": cand.get("keyword", ""),
            "label": cand.get("suggested_label", "검토필요"),
            "reason": cand.get("reason_draft", ""),
        })
        added += 1

    _save_label_examples(examples)
    print(f"[review_mode] label_examples.json 에 {added}건 반영 완료 (누적 {len(examples)}개)")
    return len(examples)


if __name__ == "__main__":
    # 간단한 자체 테스트
    sample_auto = [
        {"url": "https://example.com/1", "title": "기사A", "source": "이데일리",
         "search_keyword": "리본카", "ai_decision": "포함"},
        {"url": "https://example.com/2", "title": "기사B", "source": "오토IN",
         "search_keyword": "케이카", "ai_decision": "포함"},
    ]
    sample_actual_urls = ["https://example.com/1", "https://example.com/3"]
    result = run_review_mode(sample_actual_urls, sample_auto)
    print(json.dumps(result, ensure_ascii=False, indent=2))
