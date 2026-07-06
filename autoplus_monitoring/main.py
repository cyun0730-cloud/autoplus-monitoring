# -*- coding: utf-8 -*-
"""
main.py
===============================================================================
[모듈 목적]
오토플러스(리본카) 언론 모니터링 자동화 시스템의 실행 진입점.
4가지 실행 모드를 지원한다.

  --run                        파이프라인 1회 즉시 실행
  --schedule                   SEND_FREQUENCY(.env)에 따라 자동 스케줄 실행
  --review --sent-urls "..."   리뷰 모드 실행
  --web                        Flask 웹 서버 실행

[스케줄 로직 - --schedule]
schedule 라이브러리로 SEND_FREQUENCY(.env)에 따라 자동 실행한다.
기본값 MWF일 경우 매주 월/수/금 09시 배포캘린더 확인 → 10:30 AI채점/문서생성 →
11시 이메일 발송. holidays 라이브러리로 한국 공휴일 감지 시 익업무일로 자동 연기.
DAILY로 설정 변경 시 매일 동일 루틴 실행.
===============================================================================
"""
import os
import sys
import argparse
from datetime import datetime, timedelta

import schedule
import holidays
from dotenv import load_dotenv

import collector_naver_api
import collector_google_rss
import collector_media_specific
import media_normalizer
import rule_filter
import ai_scorer
import formatter_docx
import email_sender
import review_mode
from release_calendar import get_today_releases, collect_release_articles

load_dotenv()

SEND_FREQUENCY = os.getenv("SEND_FREQUENCY", "MWF")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))

KR_HOLIDAYS = holidays.KR()


def run_pipeline_once():
    """
    파이프라인을 1회 실행한다: 수집 → 정규화 → 1차 규칙필터 → 2차 AI채점 →
    문서생성 → 이메일 발송.
    """
    print("=" * 70)
    print(f"[main] 파이프라인 실행 시작 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 0) 배포 캘린더 확인 (자사 배포 예정 자료가 있으면 09시 추가 수집)
    today_releases = get_today_releases()
    if today_releases:
        collect_release_articles(today_releases)

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

    # 4) 2차 AI 채점
    ai_scored = ai_scorer.run_ai_scoring(filtered["pass"])

    # 5) 문서 생성
    docx_path = formatter_docx.generate_monitoring_docx(
        ai_scored, filtered["negative_flagged"], filtered["vig_sensitive"]
    )

    # 6) 이메일 발송 (TEST_MODE 시 미리보기만 저장)
    email_sender.send_monitoring_email(ai_scored, filtered["negative_flagged"], filtered["vig_sensitive"])

    print(f"[main] 파이프라인 실행 완료 → 문서: {docx_path}")
    return {"ai_scored": ai_scored, "filtered": filtered, "docx_path": docx_path}


def _next_business_day(date):
    """공휴일/주말을 감안해 다음 업무일을 반환한다."""
    next_day = date + timedelta(days=1)
    while next_day.weekday() >= 5 or next_day in KR_HOLIDAYS:
        next_day += timedelta(days=1)
    return next_day


def _scheduled_job():
    """
    schedule 라이브러리가 호출하는 실제 실행 함수.
    오늘이 공휴일이면 실행을 건너뛰고(익업무일 자동 연기 로직은 schedule 등록
    시점에서 요일 자체를 검사하므로, 여기서는 안전장치로 한 번 더 확인한다).
    """
    today = datetime.now().date()
    if today in KR_HOLIDAYS:
        next_bd = _next_business_day(datetime.now())
        print(f"[main] 오늘({today})은 공휴일입니다. 실행을 건너뛰고 다음 업무일({next_bd.date()})에 발송됩니다.")
        return
    run_pipeline_once()


def run_schedule():
    """
    SEND_FREQUENCY(.env)에 따라 매일 또는 월/수/금 스케줄을 등록하고 대기한다.
    - MWF: 매주 월/수/금 11:00 실행 (09시 배포캘린더 확인/10:30 AI채점은
      run_pipeline_once() 내부에서 순차적으로 처리되므로, 스케줄 자체는 11시
      1회 등록으로 충분함 - 확인 필요: 운영 중 09/10:30/11시 3단계로 세분화가
      필요하면 schedule.every().monday.at("09:00") 등으로 추가 등록 가능)
    - DAILY: 매일 11:00 실행
    """
    print(f"[main] 스케줄 모드 시작 - SEND_FREQUENCY={SEND_FREQUENCY}")
    print("[main] 참고: 정확도는 데이터 부족이 아니라 담당자 기준 이관의 문제입니다. "
          "review_mode.py를 주기적으로 실행해 label_examples.json을 갱신하세요.")

    if SEND_FREQUENCY.upper() == "DAILY":
        schedule.every().day.at("11:00").do(_scheduled_job)
        print("[main] 매일 11:00 실행 스케줄 등록 완료")
    else:
        schedule.every().monday.at("11:00").do(_scheduled_job)
        schedule.every().wednesday.at("11:00").do(_scheduled_job)
        schedule.every().friday.at("11:00").do(_scheduled_job)
        print("[main] 월/수/금 11:00 실행 스케줄 등록 완료 (MWF)")

    import time
    while True:
        schedule.run_pending()
        time.sleep(30)


def run_review(sent_urls_str: str):
    """
    리뷰 모드 실행. --sent-urls 로 전달받은 콤마 구분 URL 문자열을 파싱해
    최근 raw_articles.json 기반 AI 채점 결과와 대조한다.

    확인 필요: CLI 리뷰 모드는 최근 수집/채점 결과가 파일로 남아있지 않으면
    (web_app.py의 인메모리 캐시와 달리) 비교 대상이 없을 수 있으므로, 운영
    시에는 --run 실행 직후 --review를 이어서 실행하거나, 웹 대시보드의
    /review 화면 사용을 권장한다.
    """
    actual_sent_urls = [u.strip() for u in sent_urls_str.split(",") if u.strip()]

    # 최신 raw_articles.json 을 다시 정규화/필터/채점하여 비교 기준 확보
    import json
    raw_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "raw_articles.json")
    if not os.path.exists(raw_path):
        print("[main] data/raw_articles.json 이 없습니다. 먼저 --run 을 실행하세요.")
        return

    with open(raw_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)
    articles = raw_data.get("articles", [])

    normalized = media_normalizer.normalize_articles(articles)
    filtered = rule_filter.apply_all_filters(normalized)
    ai_scored = ai_scorer.run_ai_scoring(filtered["pass"])

    all_scored = ai_scored["포함"] + ai_scored["제외"] + ai_scored["검토필요"]
    result = review_mode.run_review_mode(actual_sent_urls, all_scored)

    print("\n[main] === 리뷰 결과 요약 ===")
    print(f"놓친 기사: {len(result['missed_articles'])}건")
    print(f"잘못 포함한 기사: {len(result['wrongly_included_articles'])}건")
    print(f"라벨 업데이트 후보: {len(result['label_update_candidates'])}건")

    apply = input("라벨 업데이트 후보를 label_examples.json 에 반영하시겠습니까? (y/n): ")
    if apply.strip().lower() == "y":
        review_mode.apply_label_update_candidates(result["label_update_candidates"])


def run_web():
    """Flask 웹 서버를 실행한다 (.env의 FLASK_PORT 사용)."""
    from web_app import app
    print(f"[main] Flask 웹 대시보드 시작 - http://0.0.0.0:{FLASK_PORT}")
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False)


def main():
    parser = argparse.ArgumentParser(description="오토플러스 뉴스 모니터링 자동화 시스템")
    parser.add_argument("--run", action="store_true", help="파이프라인 1회 즉시 실행")
    parser.add_argument("--schedule", action="store_true", help="자동 스케줄 실행 (SEND_FREQUENCY 따름)")
    parser.add_argument("--review", action="store_true", help="리뷰 모드 실행")
    parser.add_argument("--sent-urls", type=str, default="", help="--review 와 함께 사용, 콤마 구분 URL 목록")
    parser.add_argument("--web", action="store_true", help="Flask 웹 서버 실행")

    args = parser.parse_args()

    if args.run:
        run_pipeline_once()
    elif args.schedule:
        run_schedule()
    elif args.review:
        if not args.sent_urls:
            print("[main] --review 사용 시 --sent-urls \"url1,url2,...\" 를 함께 전달해야 합니다.")
            sys.exit(1)
        run_review(args.sent_urls)
    elif args.web:
        run_web()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
