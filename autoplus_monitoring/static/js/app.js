// =============================================================================
// app.js
// 오토플러스 뉴스 모니터링 대시보드 프론트엔드 로직
// axios(CDN)를 사용해 Flask 백엔드 API와 통신한다.
// =============================================================================

// ---------------------------------------------------------------------------
// 대시보드(index.html) 로직
// ---------------------------------------------------------------------------
function initDashboard() {
  const btnRun = document.getElementById("btn-run");
  const btnSendEmail = document.getElementById("btn-send-email");
  const articleList = document.getElementById("article-list");
  const filterButtons = document.querySelectorAll(".filter-btn");
  const dateSelect = document.getElementById("date-select");

  if (!articleList) return; // index.html이 아니면 아무것도 하지 않음

  // 오늘 날짜를 보고 있는지 여부 (index 라우트에서 body data 속성으로 내려줌).
  // 과거 날짜(아카이브) 조회 중에는 라벨 수정/실행/발송을 막아 실시간 결과와
  // 과거 기록이 뒤섞이지 않도록 한다.
  const isToday = document.body.dataset.isToday === "true";
  const currentDate = document.body.dataset.selectedDate || "";

  let currentSection = "";
  let currentFlag = "";

  // 날짜 선택 드롭다운 - 선택 즉시 해당 날짜 기준으로 페이지 재조회
  if (dateSelect) {
    dateSelect.addEventListener("change", () => {
      window.location.href = "/?date=" + encodeURIComponent(dateSelect.value);
    });
  }

  // 요약 숫자(자사/경쟁사/업계/경고/전체)만 실시간으로 다시 불러와 갱신한다.
  // 전체 페이지를 새로고침하지 않으므로 라벨 수정 직후 반응 속도가 빠르다.
  async function refreshSummary() {
    if (!isToday) return; // 과거 날짜는 값이 고정돼 있으므로 다시 불러올 필요 없음
    try {
      const res = await axios.get("/summary");
      const map = {
        "total-count": res.data.total_count,
        "own-count": res.data.own_count,
        "competitor-count": res.data.competitor_count,
        "industry-count": res.data.industry_count,
        "warning-count": res.data.warning_count,
      };
      Object.entries(map).forEach(([id, value]) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
      });
    } catch (e) {
      console.error("요약 갱신 실패:", e.message);
    }
  }

  // 모니터링 실행 버튼
  btnRun.addEventListener("click", async () => {
    btnRun.disabled = true;
    btnRun.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 실행 중...';
    try {
      await axios.post("/run");
      pollStatus();
    } catch (e) {
      alert("실행 요청 실패: " + e.message);
      btnRun.disabled = false;
      btnRun.innerHTML = '<i class="fas fa-play"></i> 모니터링 실행';
    }
  });

  // 이메일 발송 버튼
  btnSendEmail.addEventListener("click", async () => {
    try {
      const res = await axios.post("/send-email");
      alert("이메일 처리 결과: " + JSON.stringify(res.data));
    } catch (e) {
      alert("이메일 발송 실패: " + e.message);
    }
  });

  // 상태 폴링 (모니터링 실행 완료까지 2초 간격으로 확인)
  function pollStatus() {
    const interval = setInterval(async () => {
      const res = await axios.get("/status");
      const statusBadge = document.getElementById("pipeline-status");
      statusBadge.textContent = res.data.status;
      statusBadge.className = "status-badge status-" + res.data.status;

      if (res.data.status === "done" || res.data.status === "error") {
        clearInterval(interval);
        btnRun.disabled = false;
        btnRun.innerHTML = '<i class="fas fa-play"></i> 모니터링 실행';
        if (res.data.status === "done") {
          location.reload(); // 카운트 갱신을 위해 새로고침
        } else {
          alert("실행 중 오류 발생: " + res.data.error_message);
        }
      }
    }, 2000);
  }

  // 섹션/플래그 필터 버튼
  filterButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      filterButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentSection = btn.dataset.section || "";
      currentFlag = btn.dataset.flag || "";
      loadArticles();
    });
  });

  async function loadArticles() {
    const params = {};
    if (currentSection) params.section = currentSection;
    if (currentFlag) params.flag = currentFlag;
    if (currentDate) params.date = currentDate;

    const res = await axios.get("/articles", { params });
    renderArticles(res.data);
  }

  // TEST_MODE 더미 데이터 판별 함수. collector_naver_api.py의
  // _generate_dummy_articles()가 생성하는 URL은 항상 "dummy.example.com"
  // 도메인을 사용하므로, 이 도메인이면 실제 기사가 아닌 테스트용 가짜 링크로
  // 간주해 클릭 시 실제 이동 대신 안내 메시지를 표시한다.
  // (근본 원인 조치: 사용자가 보고한 "URL 연결 오류"는 네이버 API 엔드포인트
  // 자체의 오류가 아니라, TEST_MODE의 더미 URL이 클릭 가능한 링크로 노출되어
  // 브라우저가 존재하지 않는 도메인에 접속을 시도했기 때문으로 추정됨.)
  function isDummyTestUrl(url) {
    return typeof url === "string" && url.includes("dummy.example.com");
  }

  function renderArticleCard(a) {
    const flagClass = a.negative_flag ? "negative-flag" : (a.sensitive_flag ? "vig-flag" : "");
    const decisions = ["포함", "제외", "검토필요"];
    const labelButtons = decisions
      .map(
        (d) =>
          `<button data-id="${a._id}" data-label="${d}" ${isToday ? "" : "disabled"} class="${a.ai_decision === d ? "active-label" : ""}">${d}</button>`
      )
      .join("");
    const isDummy = isDummyTestUrl(a.url);
    const titleLink = isDummy
      ? `<a href="#" class="dummy-url-link" title="TEST_MODE 더미 링크 - 실제 기사 아님">${a.title} <span class="dummy-badge">TEST</span></a>`
      : `<a href="${a.url}" target="_blank" rel="noopener">${a.title}</a>`;
    return `
    <div class="article-card ${flagClass}">
      <span class="media-badge">${a.source || "미확인매체"}</span>
      <span class="media-badge">${a.keyword_category || ""}</span>
      <div class="article-title">${titleLink}</div>
      <div class="article-reason">${a.ai_reason || ""}</div>
      <div class="label-buttons">${labelButtons}</div>
    </div>`;
  }

  // 포함/검토필요/제외를 같은 목록에 뒤섞어 보여주지 않고, 그룹별로 별도
  // 섹션에 나눠 렌더링한다. 담당자가 버튼을 눌러 라벨을 바꾸면 loadArticles()가
  // 다시 호출되어 최신 ai_decision 기준으로 그룹이 재계산되므로, "제외"로 바꾼
  // 기사는 자동으로 포함 그룹에서 빠져나가 제외 그룹으로 옮겨간다.
  function renderArticles(articles) {
    if (!articles.length) {
      articleList.innerHTML = '<p class="empty-hint">해당 조건의 기사가 없습니다.</p>';
      return;
    }

    const groups = { "포함": [], "검토필요": [], "제외": [] };
    articles.forEach((a) => {
      const bucket = groups[a.ai_decision] ? a.ai_decision : "검토필요";
      groups[bucket].push(a);
    });

    const groupOrder = [
      { key: "포함", label: "포함", cls: "group-included" },
      { key: "검토필요", label: "검토필요", cls: "group-pending" },
      { key: "제외", label: "제외", cls: "group-excluded" },
    ];

    articleList.innerHTML = groupOrder
      .map(({ key, label, cls }) => {
        const items = groups[key];
        const body = items.length
          ? items.map(renderArticleCard).join("")
          : '<p class="empty-hint">해당 없음</p>';
        return `
        <div class="decision-group ${cls}">
          <h4 class="decision-group-title">${label} <span class="decision-group-count">(${items.length})</span></h4>
          <div class="decision-group-body">${body}</div>
        </div>`;
      })
      .join("");

    // TEST_MODE 더미 링크 클릭 시 실제 이동을 막고 안내 메시지 표시
    articleList.querySelectorAll(".dummy-url-link").forEach((link) => {
      link.addEventListener("click", (e) => {
        e.preventDefault();
        alert(
          "이 기사는 TEST_MODE 더미(가짜) 데이터입니다.\n" +
          "실제 존재하는 URL이 아니므로 연결되지 않습니다(정상 동작).\n\n" +
          "실제 기사 링크를 사용하려면 .env에서 TEST_MODE=False로 변경하고 " +
          "NAVER_CLIENT_ID/NAVER_CLIENT_SECRET을 입력한 뒤 서비스를 재시작하세요."
        );
      });
    });

    // 라벨 수정 버튼 이벤트 바인딩 (과거 날짜 조회 중에는 버튼이 disabled 처리되어
    // 클릭 자체가 안 되지만, 안전하게 이벤트도 오늘 조회 중일 때만 바인딩한다)
    if (isToday) {
      articleList.querySelectorAll(".label-buttons button").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const id = btn.dataset.id;
          const label = btn.dataset.label;
          await axios.post(`/articles/${id}/label`, { label, reason: "담당자 웹 대시보드 직접 수정" });
          // 기사 카드(버튼 활성 표시)와 상단 요약 숫자를 페이지 새로고침 없이
          // 즉시 함께 갱신한다 (실시간 반영). loadArticles()가 그룹을 다시
          // 계산하므로 이 기사는 자동으로 새 그룹으로 옮겨간다.
          await Promise.all([loadArticles(), refreshSummary()]);
        });
      });
    }
  }

  loadArticles();
}

// ---------------------------------------------------------------------------
// 설정 페이지(settings.html) 로직
// ---------------------------------------------------------------------------
function initSettings() {
  const btnAdd = document.getElementById("btn-add-subscriber");
  if (!btnAdd) return;

  const input = document.getElementById("new-subscriber-email");
  const list = document.getElementById("subscriber-list");

  btnAdd.addEventListener("click", async () => {
    const email = input.value.trim();
    if (!email) return;
    try {
      const res = await axios.post("/settings/subscribers", { action: "add", email });
      if (res.data.success) {
        location.reload();
      } else {
        alert(res.data.message);
      }
    } catch (e) {
      alert(e.response?.data?.message || e.message);
    }
  });

  list.querySelectorAll(".btn-remove-subscriber").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const email = btn.dataset.email;
      await axios.post("/settings/subscribers", { action: "remove", email });
      location.reload();
    });
  });
}

// ---------------------------------------------------------------------------
// 키워드 관리(설정 페이지 내 섹션) 로직
// 카테고리: own(자사) / competitor(경쟁사) / industry1~5(업계1~5순위) /
//           sensitive(민감) / negative(부정)
// ---------------------------------------------------------------------------
function _escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}

function initKeywords() {
  const groupsRoot = document.getElementById("keyword-category-groups");
  if (!groupsRoot) return; // settings.html이 아니면 아무것도 하지 않음

  const INDUSTRY_LABEL_FALLBACK = {
    "1": "1순위: 중고차/렌터카/구독서비스",
    "2": "2순위: 캐피탈+중고차/카드사(자동차 금융)",
    "3": "3순위: 브랜드뉴스(단순 프로모션 제외)",
    "4": "4순위: 업계 기획/트렌드",
    "5": "5순위: 기타(인프라/법규/자율주행/모빌리티)",
  };

  function renderChipList(category, keywordArr) {
    const container = groupsRoot.querySelector(`.keyword-chip-list[data-category="${category}"]`);
    if (!container) return;
    if (!keywordArr || !keywordArr.length) {
      container.innerHTML = '<span class="empty-hint">등록된 키워드가 없습니다.</span>';
      return;
    }
    container.innerHTML = keywordArr
      .map(
        (kw) => `
        <span class="keyword-chip" data-category="${category}" data-keyword="${_escapeHtml(kw)}">
          ${_escapeHtml(kw)}
          <button class="btn-remove-keyword" data-category="${category}" data-keyword="${_escapeHtml(kw)}" title="삭제">
            <i class="fas fa-times"></i>
          </button>
        </span>`
      )
      .join("");
  }

  function renderAll(snapshot) {
    renderChipList("own", snapshot.own);
    renderChipList("competitor", snapshot.competitor);
    for (let i = 1; i <= 5; i++) {
      renderChipList(`industry${i}`, (snapshot.industry || {})[String(i)]);
      const hintEl = document.getElementById(`industry-label-${i}`);
      if (hintEl) {
        const labels = snapshot.industry_labels || INDUSTRY_LABEL_FALLBACK;
        hintEl.textContent = "(" + (labels[String(i)] || "") + ")";
      }
    }
    renderChipList("sensitive", snapshot.sensitive);
    renderChipList("negative", snapshot.negative);

    // 삭제 버튼 이벤트 바인딩 (매 렌더링마다 재바인딩)
    groupsRoot.querySelectorAll(".btn-remove-keyword").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const category = btn.dataset.category;
        const keyword = btn.dataset.keyword;
        if (!confirm(`'${keyword}' 키워드를 [${category}]에서 삭제하시겠습니까?`)) return;
        try {
          const res = await axios.post("/keywords/remove", { category, keyword });
          if (res.data.success) {
            renderAll(res.data.snapshot);
          } else {
            alert(res.data.message);
          }
        } catch (e) {
          alert(e.response?.data?.message || e.message);
        }
      });
    });
  }

  // 추가 버튼 이벤트 바인딩 (페이지 로드 시 1회만)
  groupsRoot.querySelectorAll(".btn-add-keyword").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const category = btn.dataset.category;
      const input = groupsRoot.querySelector(`.keyword-input[data-category="${category}"]`);
      const keyword = (input.value || "").trim();
      if (!keyword) return;
      try {
        const res = await axios.post("/keywords/add", { category, keyword });
        if (res.data.success) {
          input.value = "";
          renderAll(res.data.snapshot);
        } else {
          alert(res.data.message);
        }
      } catch (e) {
        alert(e.response?.data?.message || e.message);
      }
    });
  });

  // Enter 키로도 추가 가능하도록 지원
  groupsRoot.querySelectorAll(".keyword-input").forEach((input) => {
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        const category = input.dataset.category;
        groupsRoot.querySelector(`.btn-add-keyword[data-category="${category}"]`).click();
      }
    });
  });

  // 초기 렌더링: settings.html이 서버 렌더링 시점에 심어둔 스냅샷을 사용하고,
  // 최신 상태를 다시 한번 API로 동기화한다 (다른 탭에서 변경했을 가능성 대비).
  if (window.__INITIAL_KEYWORDS_SNAPSHOT__) {
    renderAll(window.__INITIAL_KEYWORDS_SNAPSHOT__);
  }
  axios.get("/keywords").then((res) => renderAll(res.data)).catch(() => {});
}

// ---------------------------------------------------------------------------
// 주간 리뷰 페이지(review.html) 로직
// ---------------------------------------------------------------------------
function initReview() {
  const btnRunReview = document.getElementById("btn-run-review");
  if (!btnRunReview) return;

  const btnApplyCandidates = document.getElementById("btn-apply-candidates");
  const candidatesList = document.getElementById("label-candidates-list");
  let lastCandidates = []; // 가장 최근 리뷰 실행 결과의 label_update_candidates 보관

  function renderCandidates(candidates) {
    lastCandidates = candidates;
    if (!candidates.length) {
      candidatesList.innerHTML = '<li class="empty-hint">없음</li>';
      btnApplyCandidates.disabled = true;
      return;
    }
    candidatesList.innerHTML = candidates
      .map(
        (c, i) => `
        <li>
          <label>
            <input type="checkbox" class="candidate-checkbox" data-index="${i}" checked>
            <strong>[${c.suggested_label}]</strong> ${c.title} (${c.source || "미확인"}) —
            <span class="candidate-reason">${c.reason_draft}</span>
          </label>
        </li>`
      )
      .join("");
    btnApplyCandidates.disabled = false;
  }

  btnRunReview.addEventListener("click", async () => {
    const sentUrls = document.getElementById("sent-urls-input").value;
    const res = await axios.post("/review/run", { sent_urls: sentUrls });
    const data = res.data;

    const missedList = document.getElementById("missed-articles-list");
    const wrongList = document.getElementById("wrong-articles-list");

    missedList.innerHTML = data.missed_articles.length
      ? data.missed_articles.map((a) => `<li>${a.title} (${a.source || "미확인"})</li>`).join("")
      : '<li class="empty-hint">없음</li>';

    wrongList.innerHTML = data.wrongly_included_articles.length
      ? data.wrongly_included_articles.map((a) => `<li>${a.title} (${a.source || "미확인"})</li>`).join("")
      : '<li class="empty-hint">없음</li>';

    renderCandidates(data.label_update_candidates || []);
  });

  // "선택 항목 반영하기" - 체크된 후보만 실제 label_examples.json에 저장한다.
  // 이 단계를 거쳐야만 다음 AI 채점부터 이번 리뷰 결과가 학습에 반영된다.
  btnApplyCandidates.addEventListener("click", async () => {
    const checked = Array.from(candidatesList.querySelectorAll(".candidate-checkbox:checked"));
    const approvedIndices = checked.map((cb) => parseInt(cb.dataset.index, 10));

    if (!approvedIndices.length) {
      alert("반영할 항목을 하나 이상 선택해주세요.");
      return;
    }

    try {
      const res = await axios.post("/review/apply", {
        candidates: lastCandidates,
        approved_indices: approvedIndices,
      });
      alert(
        `${res.data.applied_count}건 반영 완료 (누적 라벨 예시 ${res.data.total_label_examples}개).\n` +
        "다음 모니터링 실행부터 이번 리뷰 결과가 AI 채점에 학습됩니다."
      );
      renderCandidates([]); // 반영 후 목록 비우기 (중복 반영 방지)
    } catch (e) {
      alert(e.response?.data?.error || e.message);
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initDashboard();
  initSettings();
  initKeywords();
  initReview();
});
