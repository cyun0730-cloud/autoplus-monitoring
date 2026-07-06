# -*- coding: utf-8 -*-
"""
web_app.py
===============================================================================
[모듈 목적]
Flask 기반 웹 대시보드. 오토플러스 뉴스 모니터링 자동화 시스템의 실행 결과를
확인하고, 수동 실행/이메일 발송/구독 관리/주간 리뷰/문서 다운로드를 수행할 수
있는 관리 화면을 제공한다.

[프레임워크]
Flask로 확정 (다른 프레임워크로 임의 변경 금지 - 사용자 지시사항).

[라우트 목록]
  GET  /                        메인 대시보드
  POST /run                     모니터링 즉시 실행 (백그라운드 스레드)
  GET  /status                  실행 상태 JSON
  GET  /articles                기사 목록 API (decision/section/flag 필터)
  POST /articles/<id>/label     담당자가 AI 판단을 직접 수정 → label_examples 후보 반영
  POST /send-email               이메일 즉시 발송
  GET  /settings                 구독자·발송주기·키워드 설정 화면
  POST /settings/subscribers     구독 이메일 추가/삭제
  GET  /keywords                 키워드 현황 조회 API (카테고리별)
  POST /keywords/add             키워드 추가 API
  POST /keywords/remove          키워드 삭제 API
  GET  /review                   주간 리뷰 화면
  POST /review/run               리뷰 실행
  GET  /download/docx            Word 문서 다운로드
  GET  /download/email-preview   이메일 HTML 미리보기

[2026-07-06 업데이트]
  1) 키워드 현황 조회/추가/삭제 기능 추가 (/keywords*, keywords.py의
     add_keyword()/remove_keyword()/get_keywords_snapshot() 사용).
  2) 대시보드에서 보고된 "URL 연결 오류" 진단 결과를 반영: 실제 원인은
     collector_naver_api.py의 네이버 API 엔드포인트 URL이 아니라(이미 정확한
     https://openapi.naver.com/v1/search/news.json 사용 중), TEST_MODE=True
     상태에서 생성되는 더미 기사 URL(https://dummy.example.com/...)이 대시보드에
     실제 클릭 가능한 링크로 노출되어 브라우저에서 연결 오류가 발생한 것으로
     추정됨(확실하지 않으나 코드 흐름상 가장 개연성 높은 원인). 이에 따라
     index() 라우트에서 test_mode 플래그를 템플릿에 전달해 배너로 명시하고,
     static/js/app.js에서 더미 URL 클릭 시 실제 이동 대신 안내 메시지를
     표시하도록 조치함. 실 운영 전환(TEST_MODE=False + NAVER_CLIENT_ID/SECRET
     실값 입력) 시에는 실제 네이버 뉴스 URL이 연결되므로 이 문제는 재현되지
     않을 것으로 판단됨(운영 전환 후 재확인 필요).
===============================================================================
"""
import os
import json
import threading
from datetime import datetime

from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for
from dotenv import load_dotenv

import collector_naver_api
import collector_google_rss
import collector_media_specific
import media_normalizer
import rule_filter
import ai_scorer
import formatter_docx
import report_formatter
import email_sender
import review_mode
import keywords
from keywords import MONITORING_WEEKDAYS

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
SEND_FREQUENCY = os.getenv("SEND_FREQUENCY", "MWF")
TEST_MODE = os.getenv("TEST_MODE", "True").lower() == "true"

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

# -----------------------------------------------------------------------------
# 전역 상태 (백그라운드 실행 상태 및 최신 결과 캐시)
# 확인 필요: 운영 규모가 커지면 이 인메모리 상태 대신 파일/DB 기반 상태 관리로
# 전환 권장. 현재는 단일 사용자(PR 담당자) 사용을 전제로 한 경량 구현이다.
# -----------------------------------------------------------------------------
_pipeline_state = {
    "status": "idle",  # idle | running | done | error
    "last_run_at": None,
    "error_message": None,
}
_latest_result = {
    "ai_scored": {"포함": [], "제외": [], "검토필요": []},
    "negative_flagged": [],
    "vig_sensitive": [],
    "excluded": [],
    "docx_path": None,
}


def _run_pipeline_core():
    """
    수집 → 정규화 → 1차 규칙필터 → 2차 AI채점 → 문서생성 까지의 전체 파이프라인을
    실행한다. web_app.py의 /run 라우트에서 별도 스레드로 호출된다.
    """
    global _pipeline_state, _latest_result
    try:
        _pipeline_state["status"] = "running"
        _pipeline_state["error_message"] = None

        # 1) 수집
        naver_articles = collector_naver_api.collect_from_naver()
        rss_articles = collector_google_rss.collect_own_keywords_only()
        merged = collector_google_rss.merge_with_naver_results(naver_articles, rss_articles)
        media_specific_articles = collector_media_specific.collect_all_media_specific()
        merged.extend(media_specific_articles)
        collector_naver_api.save_raw_articles(merged)

        # 2) 매체/기자명 정규화
        normalized = media_normalizer.normalize_articles(merged)

        # 3) 1차 규칙 필터
        filtered = rule_filter.apply_all_filters(normalized)

        # 4) 2차 AI 채점 (1차 통과분에 한해서만)
        ai_scored = ai_scorer.run_ai_scoring(filtered["pass"])

        # 5) 문서 생성
        docx_path = formatter_docx.generate_monitoring_docx(
            ai_scored, filtered["negative_flagged"], filtered["vig_sensitive"]
        )

        _latest_result = {
            "ai_scored": ai_scored,
            "negative_flagged": filtered["negative_flagged"],
            "vig_sensitive": filtered["vig_sensitive"],
            "excluded": filtered["excluded"],
            "docx_path": docx_path,
        }

        _pipeline_state["status"] = "done"
        _pipeline_state["last_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print("[web_app] 파이프라인 실행 완료")

    except Exception as e:
        _pipeline_state["status"] = "error"
        _pipeline_state["error_message"] = str(e)
        print(f"[web_app] 파이프라인 실행 중 오류 발생: {e}")


# =============================================================================
# 메인 대시보드
# =============================================================================
@app.route("/")
def index():
    """메인 대시보드: 오늘 결과 요약과 섹션별 필터 UI를 렌더링한다."""
    included = _latest_result["ai_scored"].get("포함", [])
    own_count = len([a for a in included if a.get("keyword_category") == "자사"])
    competitor_count = len([a for a in included if a.get("keyword_category") == "경쟁사"])
    industry_count = len([a for a in included if str(a.get("keyword_category", "")).startswith("업계")])
    warning_count = len(_latest_result["negative_flagged"]) + len(_latest_result["vig_sensitive"])

    return render_template(
        "index.html",
        today=datetime.now().strftime("%Y-%m-%d"),
        total_count=len(included),
        own_count=own_count,
        competitor_count=competitor_count,
        industry_count=industry_count,
        warning_count=warning_count,
        pipeline_status=_pipeline_state["status"],
        last_run_at=_pipeline_state["last_run_at"],
        test_mode=TEST_MODE,
    )


# =============================================================================
# 파이프라인 실행 & 상태
# =============================================================================
@app.route("/run", methods=["POST"])
def run_pipeline():
    """모니터링 파이프라인을 백그라운드 스레드로 즉시 실행한다."""
    if _pipeline_state["status"] == "running":
        return jsonify({"status": "already_running"}), 409

    thread = threading.Thread(target=_run_pipeline_core, daemon=True)
    thread.start()
    return jsonify({"status": "started"})


@app.route("/status")
def status():
    """현재 파이프라인 실행 상태를 JSON으로 반환한다."""
    return jsonify(_pipeline_state)


# =============================================================================
# 기사 목록 API (필터 지원) + 라벨 수정 (실시간 라벨링 루프)
# =============================================================================
def _all_articles_flat():
    """포함/제외/검토필요 전체 기사를 하나의 리스트로 합치고 순번(id)을 부여한다."""
    all_articles = []
    for decision in ["포함", "제외", "검토필요"]:
        for article in _latest_result["ai_scored"].get(decision, []):
            all_articles.append(article)
    for article in _latest_result["negative_flagged"]:
        if article not in all_articles:
            all_articles.append(article)
    for article in _latest_result["vig_sensitive"]:
        if article not in all_articles:
            all_articles.append(article)
    for idx, article in enumerate(all_articles):
        article["_id"] = idx
    return all_articles


@app.route("/articles")
def articles():
    """
    기사 목록 API. decision/section(keyword_category)/flag 파라미터로 필터링 가능.
    예: /articles?decision=포함&section=경쟁사&flag=negative
    """
    decision_filter = request.args.get("decision")
    section_filter = request.args.get("section")
    flag_filter = request.args.get("flag")

    result = _all_articles_flat()

    if decision_filter:
        result = [a for a in result if a.get("ai_decision") == decision_filter]
    if section_filter:
        result = [a for a in result if a.get("keyword_category") == section_filter]
    if flag_filter == "negative":
        result = [a for a in result if a.get("negative_flag")]
    elif flag_filter == "vig":
        result = [a for a in result if a.get("sensitive_flag")]

    return jsonify(result)


@app.route("/articles/<int:article_id>/label", methods=["POST"])
def update_article_label(article_id):
    """
    담당자가 AI 판단(포함/제외/검토필요)을 직접 수정하면, 즉시
    label_examples.json 후보에 반영한다 (실시간 라벨링 루프).
    """
    payload = request.get_json(force=True) or {}
    new_label = payload.get("label")
    reason = payload.get("reason", "담당자 웹 대시보드 직접 수정")

    if new_label not in ("포함", "제외", "검토필요"):
        return jsonify({"error": "label 값은 포함/제외/검토필요 중 하나여야 합니다."}), 400

    all_articles = _all_articles_flat()
    target = next((a for a in all_articles if a.get("_id") == article_id), None)
    if target is None:
        return jsonify({"error": "해당 기사를 찾을 수 없습니다."}), 404

    target["ai_decision"] = new_label
    target["ai_reason"] = reason

    # label_examples.json 후보에 즉시 반영 (review_mode.apply_label_update_candidates 재사용)
    review_mode.apply_label_update_candidates([{
        "title": target.get("title", ""),
        "source": target.get("source", ""),
        "keyword": target.get("search_keyword", ""),
        "suggested_label": new_label,
        "reason_draft": reason,
    }])

    return jsonify({"status": "updated", "article_id": article_id, "new_label": new_label})


# =============================================================================
# 이메일 발송
# =============================================================================
@app.route("/send-email", methods=["POST"])
def send_email_route():
    """현재 캐시된 최신 결과를 기준으로 이메일을 즉시 발송한다."""
    result = email_sender.send_monitoring_email(
        _latest_result["ai_scored"],
        _latest_result["negative_flagged"],
        _latest_result["vig_sensitive"],
    )
    return jsonify(result)


# =============================================================================
# 설정 (구독자 / 발송주기)
# =============================================================================
@app.route("/settings")
def settings():
    """구독자 목록·발송주기(SEND_FREQUENCY)·키워드 현황 설정 화면을 렌더링한다."""
    subscribers = email_sender.load_subscribers()
    return render_template(
        "settings.html",
        subscribers=subscribers,
        send_frequency=SEND_FREQUENCY,
        monitoring_weekdays=MONITORING_WEEKDAYS,
        keywords_snapshot=keywords.get_keywords_snapshot(),
    )


# =============================================================================
# 키워드 현황 조회 / 추가 / 삭제
# (핵심 전제 실천: 담당자가 현업에서 파악한 새로운 경쟁사/업계 키워드를 코드
#  수정·재배포 없이 즉시 시스템에 반영할 수 있도록 함으로써 "판단 기준의
#  미이관"을 줄이는 것이 목적이다.)
# =============================================================================
@app.route("/keywords")
def keywords_snapshot_route():
    """
    현재 전체 키워드 현황(자사/경쟁사/업계1~5/민감/부정)을 JSON으로 반환한다.
    """
    return jsonify(keywords.get_keywords_snapshot())


@app.route("/keywords/add", methods=["POST"])
def keywords_add():
    """
    지정 카테고리에 키워드를 추가한다.
    요청 바디: {"category": "own|competitor|industry1~5|sensitive|negative", "keyword": "..."}
    """
    payload = request.get_json(force=True) or {}
    category = payload.get("category", "")
    keyword = payload.get("keyword", "")

    success, message = keywords.add_keyword(category, keyword)
    status_code = 200 if success else 400
    return jsonify({
        "success": success,
        "message": message,
        "snapshot": keywords.get_keywords_snapshot(),
    }), status_code


@app.route("/keywords/remove", methods=["POST"])
def keywords_remove():
    """
    지정 카테고리에서 키워드를 삭제한다.
    요청 바디: {"category": "own|competitor|industry1~5|sensitive|negative", "keyword": "..."}
    안전장치: 자사(own) 키워드는 최소 1개 이상 유지되어야 하므로 마지막 1개
    삭제 요청은 거부된다 (keywords.remove_keyword 참조).
    """
    payload = request.get_json(force=True) or {}
    category = payload.get("category", "")
    keyword = payload.get("keyword", "")

    success, message = keywords.remove_keyword(category, keyword)
    status_code = 200 if success else 400
    return jsonify({
        "success": success,
        "message": message,
        "snapshot": keywords.get_keywords_snapshot(),
    }), status_code


@app.route("/settings/subscribers", methods=["POST"])
def settings_subscribers():
    """구독 이메일을 추가하거나 삭제한다."""
    payload = request.get_json(force=True) or {}
    action = payload.get("action")
    email = payload.get("email", "").strip()

    if action == "add":
        success, message = email_sender.add_subscriber(email)
    elif action == "remove":
        success, message = email_sender.remove_subscriber(email)
    else:
        return jsonify({"error": "action 값은 add/remove 중 하나여야 합니다."}), 400

    status_code = 200 if success else 400
    return jsonify({"success": success, "message": message}), status_code


# =============================================================================
# 주간 리뷰
# =============================================================================
@app.route("/review")
def review():
    """주간 리뷰 화면을 렌더링한다."""
    return render_template("review.html", roadmap_note=review_mode.ROADMAP_NOTE)


@app.route("/review/run", methods=["POST"])
def review_run():
    """
    담당자가 입력한 실제 발송 URL 목록(콤마 구분 문자열 또는 JSON 배열)과
    현재 캐시된 자동화 결과를 대조해 리뷰를 수행한다.
    """
    payload = request.get_json(force=True) or {}
    sent_urls_raw = payload.get("sent_urls", "")

    if isinstance(sent_urls_raw, str):
        actual_sent_urls = [u.strip() for u in sent_urls_raw.split(",") if u.strip()]
    else:
        actual_sent_urls = sent_urls_raw

    all_auto_articles = []
    for decision in ["포함", "제외", "검토필요"]:
        all_auto_articles.extend(_latest_result["ai_scored"].get(decision, []))

    result = review_mode.run_review_mode(actual_sent_urls, all_auto_articles)
    return jsonify(result)


# =============================================================================
# 다운로드
# =============================================================================
@app.route("/download/docx")
def download_docx():
    """최근 생성된 모니터링 Word 문서를 다운로드한다."""
    docx_path = _latest_result.get("docx_path")
    if not docx_path or not os.path.exists(docx_path):
        return jsonify({"error": "생성된 문서가 없습니다. 먼저 /run 으로 파이프라인을 실행하세요."}), 404
    return send_file(docx_path, as_attachment=True)


@app.route("/download/email-preview")
def download_email_preview():
    """이메일 HTML 미리보기 파일을 반환한다 (브라우저에서 바로 렌더링됨)."""
    preview_path = os.path.join(OUTPUT_DIR, "email_preview.html")
    if not os.path.exists(preview_path):
        return jsonify({"error": "이메일 미리보기 파일이 없습니다. 먼저 이메일을 발송(TEST_MODE)하세요."}), 404
    return send_file(preview_path)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False)
