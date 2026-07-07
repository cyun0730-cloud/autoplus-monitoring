<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>오토플러스 뉴스 모니터링 대시보드</title>
<link rel="stylesheet" href="/static/css/style.css">
<link href="https://cdn.jsdelivr.net/npm/@fortawesome/fontawesome-free@6.4.0/css/all.min.css" rel="stylesheet">
</head>
<body data-is-today="{{ 'true' if is_today else 'false' }}" data-today="{{ today }}" data-start-date="{{ start_date }}" data-end-date="{{ end_date }}">
<header id="main-header">
  <div class="header-inner">
    <h1><i class="fas fa-newspaper"></i> 오토플러스 뉴스 모니터링</h1>
    <nav id="main-nav">
      <a href="/" class="active">대시보드</a>
      <a href="/settings">설정</a>
      <a href="/review">주간 리뷰</a>
    </nav>
  </div>
</header>

<main id="dashboard-main">
  {% if test_mode %}
  <section id="test-mode-banner" class="card banner-warning">
    <i class="fas fa-triangle-exclamation"></i>
    <strong>TEST_MODE 실행 중</strong> —
    현재 <code>.env</code>의 <code>TEST_MODE=True</code> 설정으로 실제 네이버
    뉴스 API/구글 RSS를 호출하지 않고, 더미(가짜) 기사 데이터로 전체 파이프라인을
    시연하고 있습니다. 이 때문에 아래 기사 목록의 링크는
    <code>https://dummy.example.com/...</code> 형태의 가상 주소이며, 클릭해도
    실제 기사로 연결되지 않습니다(정상 동작이며 기사 URL 자체의 오류가 아닙니다).
    네이버 뉴스 검색 API 엔드포인트(<code>https://openapi.naver.com/v1/search/news.json</code>)는
    <code>collector_naver_api.py</code>에 이미 정확히 반영되어 있습니다.
    실제 기사 링크가 열리도록 하려면 <code>.env</code>에서
    <code>TEST_MODE=False</code>로 변경하고 <code>NAVER_CLIENT_ID</code>/
    <code>NAVER_CLIENT_SECRET</code>(네이버 개발자센터 발급)을 입력한 뒤
    서비스를 재시작하세요.
  </section>
  {% endif %}
  <section id="summary-section" class="card">
    <div class="summary-header">
      <div>
        <span class="today-date">{{ today }}</span>
        <span id="pipeline-status" class="status-badge status-{{ pipeline_status }}">{{ pipeline_status }}</span>
        <label for="date-start-input" class="date-select-label">조회 날짜</label>
        <input type="date" id="date-start-input" value="{{ start_date }}">
        <span class="date-range-tilde">~</span>
        <input type="date" id="date-end-input" value="{{ end_date }}">
        <button id="btn-date-today" class="btn btn-outline btn-small" title="오늘로 돌아가기"><i class="fas fa-calendar-day"></i> 오늘</button>
      </div>
      <div id="action-buttons">
        <button id="btn-run" class="btn btn-primary" {% if not is_today %}disabled title="과거 날짜 조회 중에는 실행할 수 없습니다. 날짜를 오늘로 변경하세요."{% endif %}><i class="fas fa-play"></i> 모니터링 실행</button>
        <button id="btn-send-email" class="btn btn-secondary" {% if not is_today %}disabled title="과거 날짜 조회 중에는 발송할 수 없습니다."{% endif %}><i class="fas fa-envelope"></i> 이메일 발송</button>
        <a href="/download/docx" class="btn btn-outline"><i class="fas fa-file-word"></i> Word 다운로드</a>
        <button id="btn-coverage-report" class="btn btn-outline"><i class="fas fa-table"></i> 게재보고 다운로드</button>
        <a href="/download/email-preview" target="_blank" class="btn btn-outline"><i class="fas fa-eye"></i> 메일 미리보기</a>
      </div>
    </div>
    <p id="archive-view-banner" class="archive-view-banner" {% if is_today %}style="display:none;"{% endif %}>
      <i class="fas fa-clock-rotate-left"></i> <span id="archive-view-banner-text">선택한 날짜(기간)의 저장된 기록을 조회 중입니다 (읽기 전용, 라벨 수정 불가).</span>
      "오늘" 버튼을 누르면 실시간 화면으로 돌아갑니다.
    </p>
    <div class="summary-counts">
      <div class="count-box"><span class="count-num" id="total-count">{{ total_count }}</span><span class="count-label">전체</span></div>
      <div class="count-box own"><span class="count-num" id="own-count">{{ own_count }}</span><span class="count-label">자사</span></div>
      <div class="count-box competitor"><span class="count-num" id="competitor-count">{{ competitor_count }}</span><span class="count-label">경쟁사</span></div>
      <div class="count-box industry"><span class="count-num" id="industry-count">{{ industry_count }}</span><span class="count-label">업계</span></div>
      <div class="count-box warning"><span class="count-num" id="warning-count">{{ warning_count }}</span><span class="count-label">경고</span></div>
    </div>
    {% if last_run_at %}
    <p class="last-run-info">마지막 실행: {{ last_run_at }}</p>
    {% endif %}
  </section>

  <div id="dashboard-body">
    <aside id="section-filter" class="card">
      <h3>섹션 필터</h3>
      <ul>
        <li><button class="filter-btn active" data-section="">전체</button></li>
        <li><button class="filter-btn" data-section="자사">자사</button></li>
        <li><button class="filter-btn" data-section="경쟁사">경쟁사</button></li>
        <li><button class="filter-btn" data-flag="negative">부정 이슈</button></li>
        <li><button class="filter-btn" data-flag="vig">VIG 민감</button></li>
      </ul>
      <h3>업계 우선순위</h3>
      <ul>
        <li><button class="filter-btn" data-section="업계1">1순위 중고차/렌터카</button></li>
        <li><button class="filter-btn" data-section="업계2">2순위 자동차금융</button></li>
        <li><button class="filter-btn" data-section="업계3">3순위 브랜드뉴스</button></li>
        <li><button class="filter-btn" data-section="업계4">4순위 업계기획</button></li>
        <li><button class="filter-btn" data-section="업계5">5순위 기타</button></li>
      </ul>
    </aside>

    <section id="article-list-section" class="card">
      <div class="article-list-header">
        <h3>기사 목록</h3>
        <input type="text" id="article-search-input" placeholder="제목/매체명 검색...">
      </div>
      <div id="article-list"><p class="empty-hint">모니터링을 실행하면 결과가 여기에 표시됩니다.</p></div>
    </section>
  </div>
</main>

<footer id="main-footer">
  <p>오토플러스 PR팀 언론 모니터링 자동화 시스템 | "정확도 저하 원인은 기술적 한계가 아니라 담당자 판단 기준의 미이관"</p>
</footer>

<script src="https://cdn.jsdelivr.net/npm/axios@1.6.0/dist/axios.min.js"></script>
<script src="/static/js/app.js"></script>
</body>
</html>
