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
from datetime import datetime, timezone, timedelta

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
import release_calendar
import email_sender
import review_mode
import keywords
from keywords import MONITORING_WEEKDAYS

load_dotenv()

# 서버 OS 시간대(Render 등 해외 리전은 보통 UTC)와 무관하게, "오늘 날짜"/"지금"을
# 항상 한국 표준시(KST) 기준으로 계산한다. rule_filter.py의 날짜 필터도 동일한
# 기준(KST)을 쓰도록 맞춰뒀다 - 서로 다른 시간대 기준을 쓰면 대시보드가 표시하는
# "오늘"과 실제 기사 날짜 필터링 기준이 어긋날 수 있다.
KST = timezone(timedelta(hours=9))


def _now_kst():
    return datetime.now(timezone.utc).astimezone(KST).replace(tzinfo=None)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
DAILY_ARCHIVE_DIR = os.path.join(DATA_DIR, "daily_archive")


# =============================================================================
# 일자별 결과 누적 저장 (2026-07-06 추가)
# -----------------------------------------------------------------------------
# 기존에는 _latest_result가 메모리에만 있어 서버 재시작/재배포 시 사라지고,
# "오늘 실행한 최신 결과"만 볼 수 있었다(과거 날짜 조회 불가). 실행이 끝날 때마다
# 그날 날짜의 JSON 파일로 별도 저장해, 대시보드에서 날짜를 선택해 과거 결과를
# 다시 조회할 수 있도록 누적 데이터베이스(파일 기반)를 구성한다.
# 같은 날 여러 번 실행하면 그날 파일은 "가장 최근 실행 결과"로 덮어쓴다
# (실행 회차별 이력까지는 남기지 않음 - 필요 시 파일명에 타임스탬프를 추가해
# 확장 가능).
# =============================================================================
def _archive_path(date_str: str):
    return os.path.join(DAILY_ARCHIVE_DIR, f"{date_str}.json")


def _save_daily_archive(date_str: str):
    """현재 _latest_result를 그 날짜의 아카이브 파일로 저장한다."""
    os.makedirs(DAILY_ARCHIVE_DIR, exist_ok=True)
    payload = {
        "date": date_str,
        "saved_at": _now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "ai_scored": _latest_result["ai_scored"],
        "negative_flagged": _latest_result["negative_flagged"],
        "vig_sensitive": _latest_result["vig_sensitive"],
        "excluded": _latest_result["excluded"],
    }
    with open(_archive_path(date_str), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _load_daily_archive(date_str: str):
    """지정한 날짜의 아카이브 파일을 불러온다. 없으면 None."""
    path = _archive_path(date_str)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _list_archive_dates():
    """저장된 아카이브 날짜 목록을 최신순으로 반환한다 (오늘 날짜는 별도 포함 필요)."""
    if not os.path.isdir(DAILY_ARCHIVE_DIR):
        return []
    dates = [fn[:-5] for fn in os.listdir(DAILY_ARCHIVE_DIR) if fn.endswith(".json")]
    return sorted(dates, reverse=True)


def _list_available_dates():
    """오늘 날짜(아직 파일로 저장 전이어도 실시간 데이터가 있으므로) + 아카이브 날짜 목록."""
    today_str = _now_kst().strftime("%Y-%m-%d")
    dates = _list_archive_dates()
    if today_str not in dates:
        dates = [today_str] + dates
    return dates


# =============================================================================
# 달력 기반 날짜(범위) 조회 (2026-07-06 추가)
# -----------------------------------------------------------------------------
# 기존 "조회 날짜" 드롭다운은 "그 날짜에 파이프라인이 실행됐는가"를 기준으로
# 아카이브 파일 하나를 통째로 보여주는 방식이었다. 담당자 요청에 따라 달력에서
# 날짜(또는 여러 날짜/기간)를 고르면 "그 날짜에 실제 발행(published_at)된
# 기사"만 걸러 보여주는 방식으로 확장한다. 이를 위해 모든 날짜의 아카이브 +
# 오늘의 실시간 결과를 하나로 합친 전체 기사 풀이 필요하다.
# =============================================================================
def _gather_all_known_articles():
    """지금까지 쌓인 모든 아카이브 + 오늘의 실시간 결과를 URL 기준 중복 제거해 합친다."""
    all_articles = []
    seen_urls = set()

    for date_str in _list_archive_dates():
        archived = _load_daily_archive(date_str)
        if not archived:
            continue
        for a in _all_articles_flat(archived):
            url = a.get("url")
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            all_articles.append(a)

    for a in _all_articles_flat(_latest_result):
        url = a.get("url")
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        all_articles.append(a)

    # 병합된 풀 전체에 대해 고유 id를 다시 부여한다 (오늘 실시간 데이터의
    # _all_articles_flat() id 체계와는 별개 - 이 풀은 조회 전용이며 라벨
    # 수정에는 사용하지 않는다).
    for idx, a in enumerate(all_articles):
        a["_id"] = idx
    return all_articles


def _parse_date_range(start_str, end_str):
    """'YYYY-MM-DD' 문자열 두 개를 받아 [range_start, range_end) datetime 튜플로 변환한다.
    range_end는 end_str 날짜의 23:59:59까지 포함하도록 다음날 00:00으로 계산한다."""
    range_start = datetime.strptime(start_str, "%Y-%m-%d")
    range_end = datetime.strptime(end_str, "%Y-%m-%d") + timedelta(days=1)
    return range_start, range_end


def _filter_articles_by_date_range(articles, range_start, range_end):
    """기사 리스트에서 published_at이 [range_start, range_end) 안에 드는 것만 남긴다.
    발행일을 알 수 없는 기사는 "그 날짜에 실제 있었는지"를 답할 수 없으므로 제외한다
    (실시간 대시보드의 '놓침 방지 우선' 원칙과 달리, 여기서는 정확한 날짜 매칭이 목적)."""
    result = []
    for a in articles:
        parsed = rule_filter.parse_published_at(a.get("published_at", ""))
        if parsed is not None and range_start <= parsed < range_end:
            result.append(a)
    return result


def _compute_summary_counts(articles):
    """기사 리스트로부터 대시보드 상단 요약 카운트를 계산한다."""
    included = [a for a in articles if a.get("ai_decision") == "포함"]
    own_count = len([a for a in included if a.get("keyword_category") == "자사"])
    competitor_count = len([a for a in included if a.get("keyword_category") == "경쟁사"])
    industry_count = len([a for a in included if str(a.get("keyword_category", "")).startswith("업계")])
    warning_count = len([a for a in articles if a.get("negative_flag") or a.get("sensitive_flag")])
    return {
        "total_count": len(included),
        "own_count": own_count,
        "competitor_count": competitor_count,
        "industry_count": industry_count,
        "warning_count": warning_count,
    }

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
    "started_at": None,  # 이번 실행이 시작된 시각 (멈춘 실행 감지용)
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
        _pipeline_state["started_at"] = _now_kst().strftime("%Y-%m-%d %H:%M:%S")
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
        _pipeline_state["last_run_at"] = _now_kst().strftime("%Y-%m-%d %H:%M:%S")
        _save_daily_archive(_now_kst().strftime("%Y-%m-%d"))
        print("[web_app] 파이프라인 실행 완료 (일자별 아카이브 저장 완료)")

    except Exception as e:
        _pipeline_state["status"] = "error"
        _pipeline_state["error_message"] = str(e)
        print(f"[web_app] 파이프라인 실행 중 오류 발생: {e}")


# =============================================================================
# 메인 대시보드
# =============================================================================
@app.route("/")
def index():
    """
    메인 대시보드: 요약과 섹션별 필터 UI를 렌더링한다.
    ?start=YYYY-MM-DD&end=YYYY-MM-DD 로 달력에서 선택한 날짜(또는 기간)를
    조회할 수 있다 (둘 다 생략하면 오늘). end를 생략하면 start와 같은 값을
    써서 하루만 조회한다. 이 값들은 파이프라인 "실행일"이 아니라 기사의
    실제 발행일(published_at) 00:00~23:59 기준으로 매칭된다.
    """
    today_str = _now_kst().strftime("%Y-%m-%d")
    start_str = request.args.get("start", today_str)
    end_str = request.args.get("end", start_str)
    is_today = (start_str == today_str and end_str == today_str)

    if is_today:
        # 오늘 하루만 보는 기본 화면은 기존과 동일하게 실시간 결과를 그대로 사용한다
        # (라벨 수정과의 id 일치를 보장하기 위해 _gather_all_known_articles의
        # 병합 목록이 아니라 _latest_result를 직접 사용).
        counts = _compute_summary_counts(_all_articles_flat())
    else:
        try:
            range_start, range_end = _parse_date_range(start_str, end_str)
            all_articles = _gather_all_known_articles()
            counts = _compute_summary_counts(_filter_articles_by_date_range(all_articles, range_start, range_end))
        except ValueError:
            counts = {"total_count": 0, "own_count": 0, "competitor_count": 0, "industry_count": 0, "warning_count": 0}

    return render_template(
        "index.html",
        today=today_str,
        start_date=start_str,
        end_date=end_str,
        is_today=is_today,
        total_count=counts["total_count"],
        own_count=counts["own_count"],
        competitor_count=counts["competitor_count"],
        industry_count=counts["industry_count"],
        warning_count=counts["warning_count"],
        pipeline_status=_pipeline_state["status"],
        last_run_at=_pipeline_state["last_run_at"],
        test_mode=TEST_MODE,
    )


# =============================================================================
# 파이프라인 실행 & 상태
# =============================================================================
STALE_RUN_MINUTES = 40  # 이보다 오래 "running" 상태면 멈춘 실행으로 간주하고 새 실행 허용


@app.route("/run", methods=["POST"])
def run_pipeline():
    """
    모니터링 파이프라인을 백그라운드 스레드로 즉시 실행한다.

    [2026-07-06 추가] Render 무료 인스턴스는 15분간 요청이 없으면 슬립되는데,
    실행 중(특히 LLM 채점처럼 오래 걸리는 단계)에 슬립되면 백그라운드 스레드가
    죽어버려도 _pipeline_state["status"]는 영원히 "running"으로 남아, 이후
    모든 /run 요청이 409(already_running)로 막혀버리는 문제가 있었다.
    실행 시작 시각(started_at)이 STALE_RUN_MINUTES(기본 40분)을 넘겼는데도
    여전히 "running"이면 멈춘 실행으로 간주하고 새 실행을 허용한다.
    """
    if _pipeline_state["status"] == "running":
        started_at = _pipeline_state.get("started_at")
        is_stale = False
        if started_at:
            try:
                started_dt = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
                elapsed_minutes = (_now_kst() - started_dt).total_seconds() / 60
                is_stale = elapsed_minutes > STALE_RUN_MINUTES
            except ValueError:
                is_stale = True  # 시각 파싱 실패 시 안전하게 재실행 허용

        if not is_stale:
            return jsonify({"status": "already_running", "started_at": started_at}), 409

        print(f"[web_app] 이전 실행이 {STALE_RUN_MINUTES}분 넘게 멈춰있어(started_at={started_at}) "
              f"멈춘 실행으로 간주하고 새로 시작합니다.")

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
def _all_articles_flat(source=None):
    """
    포함/제외/검토필요 전체 기사를 하나의 리스트로 합치고 순번(id)을 부여한다.
    source를 지정하지 않으면 오늘의 실시간 결과(_latest_result)를 사용하고,
    과거 날짜 조회 시에는 아카이브에서 불러온 dict를 source로 넘겨 재사용한다.
    """
    source = source if source is not None else _latest_result
    all_articles = []
    for decision in ["포함", "제외", "검토필요"]:
        for article in source["ai_scored"].get(decision, []):
            all_articles.append(article)
    for article in source["negative_flagged"]:
        if article not in all_articles:
            all_articles.append(article)
    for article in source["vig_sensitive"]:
        if article not in all_articles:
            all_articles.append(article)
    for idx, article in enumerate(all_articles):
        article["_id"] = idx
        # 매체마다 제각각인 published_at 표기(RFC822, "YYYY-MM-DD HH:MM" 등)를
        # 화면 표시용으로 통일한다. 대시보드에서 검색어 뱃지 옆에 발행일을
        # 보여주기 위한 용도 (2026-07-06 추가).
        parsed = rule_filter.parse_published_at(article.get("published_at", ""))
        article["published_at_display"] = parsed.strftime("%m/%d %H:%M") if parsed else (article.get("published_at") or "-")
    return all_articles


def _resolve_source(date_param):
    """date_param이 오늘이거나 없으면 실시간 데이터, 과거 날짜면 아카이브를 반환한다."""
    today_str = _now_kst().strftime("%Y-%m-%d")
    if not date_param or date_param == today_str:
        return _latest_result, True
    archived = _load_daily_archive(date_param)
    return archived, False


def _get_current_grouped_result():
    """
    오늘의 전체 기사를 "현재" ai_decision 기준으로 다시 그룹핑해 반환한다.

    update_article_label()은 기사의 ai_decision 필드만 바꾸고 _latest_result
    안의 원래 리스트(포함/제외/검토필요) 소속은 그대로 두기 때문에, Word 문서나
    이메일을 만들 때 _latest_result["ai_scored"]를 그대로 쓰면 담당자가 대시보드에서
    수정한 내용이 반영되지 않는다. 이 함수가 그 재그룹핑을 담당한다.

    [2026-07-06 수정] 기존에는 negative_flagged/vig_sensitive([경고] 섹션 대상)를
    negative_flag/sensitive_flag 원본 플래그로만 걸러서, 담당자가 대시보드에서
    "제외"로 명시적으로 바꾼 기사도 Word/이메일의 [경고] 섹션에 계속 무조건
    떠버리는 문제가 있었다. "제외"로 바꾼 기사는 경고 섹션을 포함해 문서
    어디에도 나오지 않아야 하므로, ai_decision == "제외"인 기사는 여기서도
    제외한다.

    반환값: (ai_scored_regrouped, negative_flagged, vig_sensitive)
    """
    all_articles = _all_articles_flat()
    regrouped = {"포함": [], "제외": [], "검토필요": []}
    for a in all_articles:
        bucket = a.get("ai_decision") if a.get("ai_decision") in regrouped else "검토필요"
        regrouped[bucket].append(a)

    negative_flagged = [
        a for a in all_articles if a.get("negative_flag") and a.get("ai_decision") != "제외"
    ]
    vig_sensitive = [
        a for a in all_articles if a.get("sensitive_flag") and a.get("ai_decision") != "제외"
    ]
    return regrouped, negative_flagged, vig_sensitive


@app.route("/articles")
def articles():
    """
    기사 목록 API. decision/section(keyword_category)/flag/date 파라미터로 필터링 가능.
    예: /articles?decision=포함&section=경쟁사&flag=negative&date=2026-07-05
    date를 생략하거나 오늘 날짜를 주면 실시간 결과, 과거 날짜를 주면 그날의
    아카이브(누적 저장된 결과)를 조회한다.
    """
    decision_filter = request.args.get("decision")
    section_filter = request.args.get("section")
    flag_filter = request.args.get("flag")
    date_param = request.args.get("date")

    source, _is_today = _resolve_source(date_param)
    if source is None:
        return jsonify([])  # 해당 날짜에 저장된 결과 없음

    result = _all_articles_flat(source)

    if decision_filter:
        result = [a for a in result if a.get("ai_decision") == decision_filter]
    if section_filter:
        result = [a for a in result if a.get("keyword_category") == section_filter]
    if flag_filter == "negative":
        result = [a for a in result if a.get("negative_flag")]
    elif flag_filter == "vig":
        result = [a for a in result if a.get("sensitive_flag")]

    return jsonify(result)


@app.route("/articles/by-date")
def articles_by_date():
    """
    달력에서 선택한 날짜(하루) 또는 기간의 기사를 조회한다. 파이프라인
    "실행일"이 아니라 기사의 실제 발행일(published_at, 00:00~23:59 KST)을
    기준으로 매칭한다는 점이 /articles?date=...와 다르다.

    쿼리 파라미터:
      start=YYYY-MM-DD (필수)
      end=YYYY-MM-DD (생략 시 start와 동일 - 하루만 조회)
      decision/section/flag: 기존 /articles와 동일한 필터

    예: /articles/by-date?start=2026-07-05&end=2026-07-05
        /articles/by-date?start=2026-07-01&end=2026-07-05 (기간 조회, 여러 날짜)

    주의: 읽기 전용 조회 전용이다 (라벨 수정은 오늘 실시간 데이터에 대해서만
    /articles + /articles/<id>/label 조합으로 가능).
    """
    start_str = request.args.get("start")
    end_str = request.args.get("end", start_str)
    if not start_str:
        return jsonify({"error": "start 파라미터가 필요합니다 (예: ?start=2026-07-05)"}), 400

    try:
        range_start, range_end = _parse_date_range(start_str, end_str)
    except ValueError:
        return jsonify({"error": "날짜 형식은 YYYY-MM-DD 이어야 합니다."}), 400

    all_articles = _gather_all_known_articles()
    result = _filter_articles_by_date_range(all_articles, range_start, range_end)

    decision_filter = request.args.get("decision")
    section_filter = request.args.get("section")
    flag_filter = request.args.get("flag")
    if decision_filter:
        result = [a for a in result if a.get("ai_decision") == decision_filter]
    if section_filter:
        result = [a for a in result if a.get("keyword_category") == section_filter]
    if flag_filter == "negative":
        result = [a for a in result if a.get("negative_flag")]
    elif flag_filter == "vig":
        result = [a for a in result if a.get("sensitive_flag")]

    return jsonify(result)


@app.route("/summary")
def summary():
    """
    대시보드 상단 요약 카운트(자사/경쟁사/업계/경고/전체)를 JSON으로 반환한다.
    포함/제외 라벨을 수정한 직후 전체 페이지 새로고침 없이 숫자만 실시간으로
    갱신하기 위한 용도. date 파라미터로 과거 날짜 조회도 지원한다.
    """
    date_param = request.args.get("date")
    source, _is_today = _resolve_source(date_param)
    if source is None:
        return jsonify({"error": "해당 날짜의 저장된 결과가 없습니다."}), 404

    included = source["ai_scored"].get("포함", [])
    own_count = len([a for a in included if a.get("keyword_category") == "자사"])
    competitor_count = len([a for a in included if a.get("keyword_category") == "경쟁사"])
    industry_count = len([a for a in included if str(a.get("keyword_category", "")).startswith("업계")])
    warning_count = len(source["negative_flagged"]) + len(source["vig_sensitive"])

    return jsonify({
        "total_count": len(included),
        "own_count": own_count,
        "competitor_count": competitor_count,
        "industry_count": industry_count,
        "warning_count": warning_count,
    })


@app.route("/history/dates")
def history_dates():
    """일자별 누적 아카이브 중 조회 가능한 날짜 목록을 최신순으로 반환한다."""
    return jsonify(_list_available_dates())


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
    """
    현재 대시보드에 반영된 최신 포함/제외/검토필요 상태를 기준으로 이메일을
    발송한다. (2026-07-06 수정: Word 다운로드와 동일한 이유로, 담당자가
    라벨을 수정한 뒤에도 예전 상태로 발송되던 문제를 고쳤다.)
    """
    regrouped, negative_flagged, vig_sensitive = _get_current_grouped_result()
    result = email_sender.send_monitoring_email(regrouped, negative_flagged, vig_sensitive)
    return jsonify(result)


# =============================================================================
# 설정 (구독자 / 발송주기)
# =============================================================================
@app.route("/settings")
def settings():
    """구독자 목록·발송주기(SEND_FREQUENCY)·키워드 현황·미확인 매체 설정 화면을 렌더링한다."""
    subscribers = email_sender.load_subscribers()
    return render_template(
        "settings.html",
        subscribers=subscribers,
        send_frequency=SEND_FREQUENCY,
        monitoring_weekdays=MONITORING_WEEKDAYS,
        keywords_snapshot=keywords.get_keywords_snapshot(),
        unconfirmed_media=media_normalizer.get_unconfirmed_media_store(),
    )


@app.route("/media/unconfirmed")
def media_unconfirmed():
    """미확인 매체 도메인 목록(예시 기사 포함)을 JSON으로 반환한다."""
    return jsonify(media_normalizer.get_unconfirmed_media_store())


@app.route("/media/resolve", methods=["POST"])
def media_resolve():
    """
    담당자가 미확인 매체 도메인에 실제 매체명/그룹을 지정해 등록한다.
    요청 형식: {"domain": "...", "name": "...", "group": "..."}
    """
    payload = request.get_json(force=True) or {}
    success, message = media_normalizer.resolve_unconfirmed_media(
        payload.get("domain", ""), payload.get("name", ""), payload.get("group", "온라인")
    )
    return jsonify({"success": success, "message": message})


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
    (주의: 이 시점에는 label_examples.json에 아직 반영되지 않는다. 화면에서
    담당자가 후보를 확인하고 "반영하기"를 눌러야 실제로 저장된다 -
    /review/apply 참조.)
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


@app.route("/review/apply", methods=["POST"])
def review_apply():
    """
    /review/run이 계산한 label_update_candidates 중 담당자가 승인한 항목만
    (또는 전체를) 실제 label_examples.json에 반영한다.
    (2026-07-06 추가: 기존에는 후보를 화면에 보여주기만 하고 실제 반영하는
    경로가 없어, 리뷰를 아무리 실행해도 다음 채점에 학습되지 않는 문제가
    있었다. 이 라우트가 그 반영 단계를 담당한다.)

    요청 형식: {"candidates": [...], "approved_indices": [0, 2, ...]}
    approved_indices를 생략하면 candidates 전체를 반영한다.
    """
    payload = request.get_json(force=True) or {}
    candidates = payload.get("candidates", [])
    approved_indices = payload.get("approved_indices")  # None이면 전체 반영

    if not candidates:
        return jsonify({"error": "반영할 후보가 없습니다. 먼저 /review/run으로 리뷰를 실행하세요."}), 400

    total_count = review_mode.apply_label_update_candidates(candidates, approved_indices)
    applied_count = len(candidates) if approved_indices is None else len(
        [i for i in approved_indices if 0 <= i < len(candidates)]
    )

    return jsonify({
        "status": "applied",
        "applied_count": applied_count,
        "total_label_examples": total_count,
    })


# =============================================================================
# 다운로드
# =============================================================================
@app.route("/download/docx")
def download_docx():
    """
    현재 대시보드에 반영된 최신 포함/제외/검토필요 상태를 기준으로 Word
    문서를 요청 시점에 즉시 다시 생성해 다운로드한다.

    [2026-07-06 수정] 기존에는 파이프라인 실행 직후( /run 완료 시점)에
    한 번 생성된 정적 파일을 그대로 내려주기만 해서, 담당자가 대시보드에서
    포함/제외를 나중에 수정해도 Word 파일에는 전혀 반영되지 않는 문제가
    있었다. update_article_label()은 기사의 ai_decision 필드만 바꾸고
    _latest_result 내 원래 리스트(포함/제외/검토필요) 소속은 그대로 두므로,
    다운로드 시점에 _all_articles_flat()으로 현재 ai_decision 기준으로
    다시 그룹핑해 문서를 새로 만든다.
    """
    if not _latest_result.get("ai_scored"):
        return jsonify({"error": "생성된 결과가 없습니다. 먼저 /run 으로 파이프라인을 실행하세요."}), 404

    regrouped, negative_flagged, vig_sensitive = _get_current_grouped_result()
    docx_path = formatter_docx.generate_monitoring_docx(regrouped, negative_flagged, vig_sensitive)
    _latest_result["docx_path"] = docx_path  # 캐시 갱신 - 이후 재다운로드도 최신 유지

    return send_file(docx_path, as_attachment=True)


@app.route("/download/coverage-report")
def download_coverage_report():
    """
    게재보고 Word 문서를 다운로드한다 (경제지/전문지·무가지/오토지/온라인
    구분 집계). README에 명시된 3대 산출물 중 하나였으나 기존에는 어떤
    라우트에도 연결되지 않은 채 report_formatter.py만 존재하던 기능이라
    2026-07-06에 새로 연결했다.

    [2026-07-06 수정 - 담당자 요청 반영]
    - 게재보고는 원래 "자사가 배포한 보도자료가 어느 매체에 게재됐는지" 집계하는
      용도이므로, 경쟁사/업계 기사까지 섞여 나오던 것을 자사(keyword_category
      == "자사") 기사만으로 제한했다.
    - 자사 언급 기사가 없는 날(보도자료를 배포하지 않았거나 아직 게재되지 않은
      날)에는 빈 문서를 만드는 대신 명확한 오류 메시지를 반환한다.
    - ?start=YYYY-MM-DD&end=YYYY-MM-DD 로 달력에서 선택한 날짜(기간)를 대상으로
      생성할 수 있다 (생략하면 오늘 하루).
    """
    start_str = request.args.get("start", _now_kst().strftime("%Y-%m-%d"))
    end_str = request.args.get("end", start_str)

    try:
        range_start, range_end = _parse_date_range(start_str, end_str)
    except ValueError:
        return jsonify({"error": "날짜 형식은 YYYY-MM-DD 이어야 합니다."}), 400

    all_articles = _gather_all_known_articles()
    own_articles = [
        a for a in _filter_articles_by_date_range(all_articles, range_start, range_end)
        if a.get("keyword_category") == "자사" and a.get("ai_decision") == "포함"
    ]

    if not own_articles:
        period_label = start_str if start_str == end_str else f"{start_str} ~ {end_str}"
        return jsonify({
            "error": f"{period_label} 기간에는 자사 언급 기사가 없어 게재보고를 생성할 수 없습니다. "
                     "보도자료가 게재된 날짜(또는 기간)를 다시 선택해주세요."
        }), 404

    release_title = request.args.get("release_title", "").strip()
    if not release_title:
        today_releases = release_calendar.get_today_releases()
        release_title = today_releases[0]["title"] if today_releases else "자사 보도자료 게재보고"

    filepath = report_formatter.generate_coverage_report_docx(own_articles, release_title)
    return send_file(filepath, as_attachment=True)


@app.route("/download/email-preview")
def download_email_preview():
    """이메일 HTML 미리보기 파일을 반환한다 (브라우저에서 바로 렌더링됨)."""
    preview_path = os.path.join(OUTPUT_DIR, "email_preview.html")
    if not os.path.exists(preview_path):
        return jsonify({"error": "이메일 미리보기 파일이 없습니다. 먼저 이메일을 발송(TEST_MODE)하세요."}), 404
    return send_file(preview_path)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False)
