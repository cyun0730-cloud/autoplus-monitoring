# -*- coding: utf-8 -*-
"""
email_sender.py
===============================================================================
[모듈 목적]
Outlook에서 바로 확인 가능한 HTML 메일을 SMTP 또는 Microsoft Graph API 방식으로
발송한다. EMAIL_BACKEND(.env) 값에 따라 분기한다.

[SMTP 방식]
smtplib + STARTTLS, smtp.office365.com:587.
확인 필요(사내 IT 정책 확인 필요): Microsoft는 Exchange Online 기본 인증(SMTP
계정/비밀번호)을 단계적으로 폐지하는 추세다. 사내 Outlook 계정에서 SMTP 방식이
차단되어 있을 가능성이 있으므로, 운영 전 사내 IT 담당자에게 반드시 확인할 것.

[Graph API 방식]
POST https://graph.microsoft.com/v1.0/users/{from}/sendMail
(client_credentials로 토큰 발급). 발송 대신 isDraft=true로 임시보관함에 초안만
저장하는 안전장치 옵션도 함께 제공해, 담당자가 Outlook에서 직접 확인 후 최종
발송 여부를 결정할 수 있게 한다.

[이메일 본문(HTML) 작성 원칙]
Outlook 데스크톱 앱은 Word 렌더링 엔진을 사용하므로 float/flexbox를 지원하지
않는다. 반드시 <table> 레이아웃과 인라인 style만 사용한다.
구조는 Word 문서와 동일 순서(경고→배포자료→자사→경쟁사→업계)를 따른다.

[TEST_MODE]
.env의 TEST_MODE=True 인 경우 실제 발송 없이 output/email_preview.html 로
저장한다.
===============================================================================
"""
import os
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

import requests
from dotenv import load_dotenv

from keywords import COMPETITOR_KEYWORDS

load_dotenv()

EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "smtp")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.office365.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

GRAPH_TENANT_ID = os.getenv("GRAPH_TENANT_ID", "")
GRAPH_CLIENT_ID = os.getenv("GRAPH_CLIENT_ID", "")
GRAPH_CLIENT_SECRET = os.getenv("GRAPH_CLIENT_SECRET", "")
GRAPH_FROM_EMAIL = os.getenv("GRAPH_FROM_EMAIL", "")

TEST_MODE = os.getenv("TEST_MODE", "True").lower() == "true"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
SUBSCRIBERS_PATH = os.path.join(DATA_DIR, "email_subscribers.json")


# =============================================================================
# 구독자 관리
# =============================================================================
def load_subscribers():
    """data/email_subscribers.json 을 로드해 구독자 이메일 리스트를 반환한다."""
    if not os.path.exists(SUBSCRIBERS_PATH):
        return []
    with open(SUBSCRIBERS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("subscribers", [])


def _is_valid_email(email: str):
    """간단한 이메일 형식 검증 (정규식 기반)."""
    import re
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email or ""))


def add_subscriber(email: str):
    """구독자 추가. 형식이 올바르지 않거나 이미 존재하면 False 반환."""
    if not _is_valid_email(email):
        return False, "이메일 형식이 올바르지 않습니다."

    subscribers = load_subscribers()
    if email in subscribers:
        return False, "이미 등록된 구독자입니다."

    subscribers.append(email)
    _save_subscribers(subscribers)
    return True, "구독자가 추가되었습니다."


def remove_subscriber(email: str):
    """구독자 삭제."""
    subscribers = load_subscribers()
    if email not in subscribers:
        return False, "등록되지 않은 구독자입니다."
    subscribers.remove(email)
    _save_subscribers(subscribers)
    return True, "구독자가 삭제되었습니다."


def _save_subscribers(subscribers: list):
    payload = {
        "_comment": "이메일 구독자 목록. web_app.py의 /settings/subscribers 라우트에서 추가/삭제된다.",
        "subscribers": subscribers,
    }
    with open(SUBSCRIBERS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


# =============================================================================
# HTML 본문 생성 (Outlook 호환: table + inline style)
# =============================================================================
def _article_row_html(article: dict):
    """기사 한 건을 <table> row(HTML) 로 변환한다. AI 판단 근거는 회색 텍스트로 표시."""
    source = article.get("source", "미확인매체")
    journalist = article.get("journalist", "")
    title = article.get("title", "")
    url = article.get("url", "")
    reason = article.get("ai_reason", "")

    reason_html = ""
    if reason:
        reason_html = (
            f'<tr><td style="padding:2px 8px 10px 20px; font-size:12px; color:#888888; '
            f'font-style:italic; border-bottom:1px solid #eeeeee;">- AI 판단 근거: {reason}</td></tr>'
        )

    return f"""
    <tr>
      <td style="padding:8px; font-size:14px; color:#222222; border-bottom:1px solid #eeeeee;">
        ▷ [{source}] {journalist} |
        <a href="{url}" style="color:#1a56db; text-decoration:none;">{title}</a>
      </td>
    </tr>
    {reason_html}
    """


def _section_html(title: str, articles: list, bg_color: str = "#f0f4f8"):
    """섹션 제목 + 기사 목록을 HTML table로 렌더링한다."""
    if not articles:
        return ""
    rows = "".join(_article_row_html(a) for a in articles)
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:16px;">
      <tr>
        <td style="background-color:{bg_color}; padding:10px 12px; font-size:16px; font-weight:bold; color:#111111;">
          {title}
        </td>
      </tr>
      {rows}
    </table>
    """


def build_email_html(ai_scored_results: dict, negative_flagged: list, vig_sensitive: list):
    """
    Outlook 호환 HTML 메일 본문을 생성한다.
    구조: 상단 요약 → 경고 → 배포자료 → 자사 → 경쟁사 → 업계 → 하단 안내문구
    """
    included = ai_scored_results.get("포함", [])
    own_articles = [a for a in included if a.get("keyword_category") == "자사"]
    competitor_articles = [a for a in included if a.get("keyword_category") == "경쟁사"]
    industry_articles = [a for a in included if str(a.get("keyword_category", "")).startswith("업계")]

    today_str = datetime.now().strftime("%Y년 %m월 %d일")
    total_count = len(included)

    summary_html = f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:20px;">
      <tr>
        <td style="background-color:#1a56db; padding:16px; color:#ffffff; font-size:18px; font-weight:bold;">
          오토플러스 뉴스 모니터링 - {today_str}
        </td>
      </tr>
      <tr>
        <td style="padding:12px; font-size:13px; color:#333333; background-color:#f8f9fa;">
          총 {total_count}건 (자사 {len(own_articles)} / 경쟁사 {len(competitor_articles)} / 업계 {len(industry_articles)})
          &nbsp;|&nbsp; 경고 {len(negative_flagged) + len(vig_sensitive)}건
        </td>
      </tr>
    </table>
    """

    warning_articles = negative_flagged + [
        {**a, "ai_reason": a.get("sensitive_note", "")} for a in vig_sensitive
    ]
    warning_html = _section_html("[경고] 부정 이슈 후보 / 민감 이슈", warning_articles, bg_color="#fde8e8")
    own_html = _section_html("[오토플러스 뉴스]", own_articles, bg_color="#e6f4ea")
    competitor_html = _section_html("[경쟁사 뉴스]", competitor_articles, bg_color="#fff4e5")
    industry_html = _section_html("[업계 뉴스]", industry_articles, bg_color="#eef2ff")

    footer_html = """
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:20px;">
      <tr>
        <td style="padding:12px; font-size:11px; color:#999999; border-top:1px solid #eeeeee;">
          본 메일은 오토플러스 PR팀 언론 모니터링 자동화 시스템에서 발송되었습니다.
        </td>
      </tr>
    </table>
    """

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0; padding:0; background-color:#ffffff; font-family:'Malgun Gothic', Arial, sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <td align="center">
        <table role="presentation" width="680" cellpadding="0" cellspacing="0" border="0">
          <tr><td>
            {summary_html}
            {warning_html}
            {own_html}
            {competitor_html}
            {industry_html}
            {footer_html}
          </td></tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
    return html


# =============================================================================
# SMTP 발송
# =============================================================================
def send_via_smtp(subject: str, html_body: str, to_list: list):
    """
    SMTP(smtp.office365.com:587, STARTTLS)로 이메일을 발송한다.
    확인 필요(사내 IT 정책): 기본 인증이 차단된 환경에서는 동작하지 않을 수 있음.
    """
    if not SMTP_USER or not SMTP_PASSWORD:
        raise RuntimeError("[email_sender] SMTP_USER/SMTP_PASSWORD 가 .env 에 설정되지 않았습니다.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = ", ".join(to_list)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, to_list, msg.as_string())

    print(f"[email_sender] SMTP 발송 완료 → 수신자 {len(to_list)}명")


# =============================================================================
# Microsoft Graph API 발송
# =============================================================================
def _get_graph_access_token():
    """client_credentials 플로우로 Graph API 액세스 토큰을 발급받는다."""
    url = f"https://login.microsoftonline.com/{GRAPH_TENANT_ID}/oauth2/v2.0/token"
    data = {
        "client_id": GRAPH_CLIENT_ID,
        "client_secret": GRAPH_CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    resp = requests.post(url, data=data, timeout=10)
    resp.raise_for_status()
    return resp.json()["access_token"]


def send_via_graph(subject: str, html_body: str, to_list: list, save_as_draft: bool = False):
    """
    Microsoft Graph API로 이메일을 발송(또는 초안 저장)한다.
    save_as_draft=True 인 경우 isDraft=true 로 임시보관함에만 저장해, 담당자가
    Outlook에서 직접 확인 후 최종 발송 여부를 결정할 수 있는 안전장치를 제공한다.
    """
    if not all([GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET, GRAPH_FROM_EMAIL]):
        raise RuntimeError("[email_sender] GRAPH_* 환경변수가 .env 에 모두 설정되지 않았습니다.")

    token = _get_graph_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    message_payload = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html_body},
        "toRecipients": [{"emailAddress": {"address": addr}} for addr in to_list],
    }

    if save_as_draft:
        # 초안 저장: POST /users/{id}/messages (isDraft는 기본 true)
        url = f"https://graph.microsoft.com/v1.0/users/{GRAPH_FROM_EMAIL}/messages"
        resp = requests.post(url, headers=headers, json=message_payload, timeout=15)
        resp.raise_for_status()
        print("[email_sender] Graph API 초안 저장 완료 → Outlook 임시보관함에서 확인 후 발송 필요")
    else:
        url = f"https://graph.microsoft.com/v1.0/users/{GRAPH_FROM_EMAIL}/sendMail"
        resp = requests.post(url, headers=headers, json={"message": message_payload}, timeout=15)
        resp.raise_for_status()
        print(f"[email_sender] Graph API 발송 완료 → 수신자 {len(to_list)}명")


# =============================================================================
# 통합 발송 진입점
# =============================================================================
def send_monitoring_email(ai_scored_results: dict, negative_flagged: list, vig_sensitive: list,
                           save_as_draft: bool = False):
    """
    EMAIL_BACKEND(.env) 값에 따라 SMTP 또는 Graph API로 발송한다.
    TEST_MODE=True 인 경우 실제 발송 없이 output/email_preview.html 로 저장한다.
    """
    html_body = build_email_html(ai_scored_results, negative_flagged, vig_sensitive)
    subject = f"[오토플러스] 뉴스 모니터링 {datetime.now().strftime('%Y-%m-%d')}"
    to_list = load_subscribers()

    if TEST_MODE:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        preview_path = os.path.join(OUTPUT_DIR, "email_preview.html")
        with open(preview_path, "w", encoding="utf-8") as f:
            f.write(html_body)
        print(f"[email_sender] TEST_MODE=True → 실제 발송 없이 미리보기 저장: {preview_path}")
        return {"status": "preview_saved", "path": preview_path}

    if not to_list:
        print("[email_sender] 구독자가 없어 발송을 생략합니다.")
        return {"status": "no_subscribers"}

    if EMAIL_BACKEND == "graph":
        send_via_graph(subject, html_body, to_list, save_as_draft=save_as_draft)
        return {"status": "sent_via_graph", "draft": save_as_draft}
    else:
        send_via_smtp(subject, html_body, to_list)
        return {"status": "sent_via_smtp"}


if __name__ == "__main__":
    dummy_results = {
        "포함": [
            {"title": "리본카, 신규 서비스 출시", "source": "이데일리", "journalist": "김기자",
             "url": "https://example.com/1", "keyword_category": "자사", "ai_reason": "자사 직접 언급"},
        ],
        "제외": [], "검토필요": [],
    }
    result = send_monitoring_email(dummy_results, [], [])
    print(result)
