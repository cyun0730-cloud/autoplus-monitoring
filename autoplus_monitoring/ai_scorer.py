# -*- coding: utf-8 -*-
"""
ai_scorer.py
===============================================================================
[모듈 목적]
2차 AI 채점. rule_filter.py의 1차 규칙 필터를 통과한 기사에 한해서만 LLM을
호출해 포함/제외/검토필요를 판단한다 (비용 절감 목적).

[핵심 전제 - 반드시 숙지]
"정확도 저하 원인은 기술적 한계가 아니라 담당자 판단 기준의 미이관"이다.
이 모듈의 정확도는 label_examples.json에 누적되는 실제 담당자 판단 사례(few-shot
예시)의 양과 질에 비례해 향상된다. 즉, AI 모델 자체를 교체하거나 프롬프트를
정교화하는 것보다, review_mode.py를 통해 실제 발송본과 자동화 결과를 지속적으로
대조하며 label_examples.json을 갱신하는 것이 정확도 향상의 핵심 경로다.

[TEST_MODE]
.env의 TEST_MODE=True 인 경우 LLM을 호출하지 않고 모든 기사를 "포함"으로
반환한다 (API 키 없이도 전체 파이프라인 검증 가능).
===============================================================================
"""
import os
import json
import re

from dotenv import load_dotenv

load_dotenv()

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
# 확인 필요: LLM_BASE_URL이 설정된 경우(GenSpark 제공 gsk- 키 등 OpenAI 호환
# 프록시 사용 시) 해당 엔드포인트로 라우팅한다. 비어있으면 OpenAI 공식
# 엔드포인트를 기본 사용한다.
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "") or None
TEST_MODE = os.getenv("TEST_MODE", "True").lower() == "true"

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LABEL_EXAMPLES_PATH = os.path.join(DATA_DIR, "label_examples.json")

LABEL_COUNT_MILESTONE = 20  # 20개 이상 누적 시 선별 정확도가 계단식으로 향상될 수 있음(참고용)


# =============================================================================
# SYSTEM_PROMPT - 사용자 지정 원문. 절대 수정 금지.
# =============================================================================
SYSTEM_PROMPT = """
너는 중고차 플랫폼 '오토플러스(리본카)'의 PR 담당자야. 아래 기준으로 기사를 검토하고
포함/제외/검토필요 중 하나로 판단해.

[포함 기준]
- 자사(오토플러스, 리본카) 직접 언급
- 경쟁사의 신규 서비스·전략·M&A·실적 기사
- 중고차 시장 동향·규모·트렌드 분석
- 완성차 브랜드의 신차 출시·국내 판매 동향·사업 전략
- 소비자 영향이 큰 자동차 금융·보험·법규 변화

[제외 기준]
- 경쟁사·완성차의 단순 프로모션·이벤트 단신
- 해외 특정 국가 판매 실적만 다루는 기사
- 자동차와 무관한 일반 ESG·채용 기사
- 이미 발송된 기사(중복)

[특수 처리]
- VIG파트너스 언급 시 "VIG_SENSITIVE"로 표시
- 부정 키워드(사고/소송/리콜/불만/논란/과징금/수사/적발) 포함 시 "NEGATIVE_FLAG"로 표시

[few-shot 예시]
{few_shot_examples}

[판단 대상 기사]
제목: {title} / 매체: {source} / 요약: {summary} / 검색 키워드: {keyword}

아래 JSON 형식으로만 출력해:
{{"decision": "포함/제외/검토필요", "reason": "판단 근거 1줄", "flags": []}}
"""


def _load_label_examples():
    """data/label_examples.json 을 로드한다. 없으면 빈 리스트 반환."""
    if not os.path.exists(LABEL_EXAMPLES_PATH):
        return []
    with open(LABEL_EXAMPLES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_few_shot_text(examples: list, max_examples: int = 15):
    """
    label_examples.json 의 예시를 few-shot 프롬프트 텍스트로 변환한다.
    예시가 너무 많으면 토큰 비용 절감을 위해 최근 max_examples 개만 사용한다.
    """
    if not examples:
        return "(현재 등록된 예시 없음)"

    recent = examples[-max_examples:]
    lines = []
    for ex in recent:
        lines.append(
            f'- 제목: "{ex.get("title", "")}" / 매체: {ex.get("source", "")} / '
            f'키워드: {ex.get("keyword", "")} → 판단: {ex.get("label", "")} '
            f'(근거: {ex.get("reason", "")})'
        )
    return "\n".join(lines)


def _call_llm(prompt: str):
    """
    OpenAI 호환 API를 호출해 LLM 응답을 받는다.
    확인 필요: 실제 운영 시 사용할 LLM 제공사(OpenAI 등) 정책에 맞춰
    엔드포인트/모델명을 재확인할 것.
    """
    from openai import OpenAI

    client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    return response.choices[0].message.content


def _parse_llm_json(raw_text: str):
    """
    LLM 응답에서 JSON 부분만 추출해 파싱한다.
    파싱 실패 시 "검토필요"로 기본 처리 (예외처리 필수, 시스템 중단 방지).
    """
    try:
        # 코드블록(```json ... ```)으로 감싸져 있을 경우 대비
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        json_str = match.group(0) if match else raw_text
        parsed = json.loads(json_str)
        decision = parsed.get("decision", "검토필요")
        reason = parsed.get("reason", "판단 근거 파싱 실패")
        flags = parsed.get("flags", [])
        if decision not in ("포함", "제외", "검토필요"):
            decision = "검토필요"
        return {"decision": decision, "reason": reason, "flags": flags}
    except Exception as e:
        return {"decision": "검토필요", "reason": f"JSON 파싱 실패({e}) - 기본값 검토필요 처리", "flags": []}


def score_article(article: dict, few_shot_text: str):
    """
    단일 기사에 대해 AI 채점을 수행한다.
    TEST_MODE=True 인 경우 LLM 호출 없이 무조건 "포함"으로 반환한다.
    """
    if TEST_MODE:
        return {"decision": "포함", "reason": "TEST_MODE=True - LLM 호출 없이 자동 포함 처리", "flags": []}

    prompt = SYSTEM_PROMPT.format(
        few_shot_examples=few_shot_text,
        title=article.get("title", ""),
        source=article.get("source", ""),
        summary=article.get("summary", ""),
        keyword=article.get("search_keyword", ""),
    )

    try:
        raw_response = _call_llm(prompt)
    except Exception as e:
        # LLM 호출 자체가 실패한 경우(네트워크, API 키 오류 등)도 시스템 중단 없이
        # "검토필요"로 안전하게 처리한다.
        return {"decision": "검토필요", "reason": f"LLM 호출 실패({e}) - 기본값 검토필요 처리", "flags": []}

    return _parse_llm_json(raw_response)


def run_ai_scoring(rule_filter_pass_articles: list, max_workers: int = 8):
    """
    1차 규칙 필터를 통과한 기사 리스트에 대해 2차 AI 채점을 수행한다.
    각 기사에 ai_decision, ai_reason, ai_flags 필드를 추가한다.

    [성능 개선 - 병렬 처리]
    기존에는 기사를 한 건씩 순차 호출해 464건 기준 약 30분이 소요됐다.
    LLM 호출은 서로 독립적인 요청이므로 ThreadPoolExecutor로 동시에 여러
    건을 처리하도록 변경했다(기본 동시 8건). LLM 호출은 네트워크 대기가
    대부분이라 스레드 병렬화만으로도 충분히 효과가 있다(GIL 영향 적음).
    max_workers는 사용하는 LLM 제공사의 동시 요청 제한(rate limit)에 맞춰
    필요시 조정 가능하다(너무 크게 잡으면 429 rate limit 오류가 늘 수 있음).

    반환값: {"포함": [...], "제외": [...], "검토필요": [...]}
    """
    import concurrent.futures

    examples = _load_label_examples()
    few_shot_text = _build_few_shot_text(examples)

    results = {"포함": [], "제외": [], "검토필요": []}

    if not rule_filter_pass_articles:
        return results

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_article = {
            executor.submit(score_article, article, few_shot_text): article
            for article in rule_filter_pass_articles
        }
        for future in concurrent.futures.as_completed(future_to_article):
            article = future_to_article[future]
            try:
                score = future.result()
            except Exception as e:
                # 개별 기사 처리 중 예기치 못한 예외가 나도 전체 파이프라인은
                # 중단되지 않도록 안전하게 "검토필요"로 처리한다.
                score = {"decision": "검토필요", "reason": f"채점 중 예외 발생({e}) - 기본값 검토필요 처리", "flags": []}
            article["ai_decision"] = score["decision"]
            article["ai_reason"] = score["reason"]
            article["ai_flags"] = score["flags"]
            results[score["decision"]].append(article)

    label_count = len(examples)
    print(
        f"[ai_scorer] 채점 완료 - 포함 {len(results['포함'])}건 / "
        f"제외 {len(results['제외'])}건 / 검토필요 {len(results['검토필요'])}건"
    )
    print(
        f"[ai_scorer] 현재 라벨 예시: {label_count}개 | "
        f"{LABEL_COUNT_MILESTONE}개 이상 누적 시 선별 정확도가 계단식으로 향상될 수 있음"
        f"(참고용 로드맵 설명, 확정 지표 아님) | "
        f"정확도는 데이터 부족이 아니라 기준 이관의 문제임"
    )

    return results


if __name__ == "__main__":
    sample_articles = [
        {"title": "리본카, 신규 서비스 출시", "source": "이데일리", "summary": "테스트 요약",
         "search_keyword": "리본카"},
        {"title": "벤츠 사은품 이벤트", "source": "오토IN", "summary": "단순 프로모션",
         "search_keyword": "벤츠"},
    ]
    out = run_ai_scoring(sample_articles)
    print(json.dumps(out, ensure_ascii=False, indent=2))
