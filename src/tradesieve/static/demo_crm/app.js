"use strict";

const state = {
  records: [],
  selectedRecordId: null,
  screenings: new Map(),
};

const labels = {
  document: {
    AVAILABLE: ["已提供", "is-available"],
    MISSING: ["缺失", "is-missing"],
    PENDING: ["待补充", "is-pending"],
  },
  signal: {
    RED: ["红灯", "signal-red"],
    YELLOW: ["黄灯", "signal-yellow"],
    GREEN_CANDIDATE: ["绿灯候选", "signal-green"],
  },
  state: {
    ESCALATE: "升级审查",
    INCOMPLETE: "资料不完整",
    REVIEW_REQUIRED: "需要人工复核",
  },
  action: {
    ESCALATE: "暂停并升级",
    REQUEST_EVIDENCE: "暂停并补件",
    HOLD: "保持拦截",
    MONITOR: "等待人工确认",
  },
  finding: {
    SYNTHETIC_EXACT_IDENTIFIER_MATCH: "合成主体强标识符命中",
    END_USER_AND_END_USE_INCOMPLETE: "最终用户与用途不完整",
    GOODS_CLASSIFICATION_UNRESOLVED: "货物归类尚未核实",
    BATTERY_TRANSPORT_DOCUMENTS_INCOMPLETE: "电池运输文件不完整",
    REEXPORT_PATH_UNRESOLVED: "转售与最终去向未闭环",
    SENSITIVE_GOODS_CANDIDATE_INCOMPLETE: "敏感货物候选资料不足",
    END_USE_INCOMPLETE: "最终用途资料不足",
  },
  evidence: {
    OTHER: "主体或政策证明",
    END_USER_STATEMENT: "最终用户声明",
    TECHNICAL_SPECIFICATION: "技术规格资料",
  },
  requiredAction: {
    SUSPEND_AND_RESOLVE_SYNTHETIC_MATCH: "暂停业务并核实合成主体命中",
    OBTAIN_END_USER_STATEMENT: "补充最终用户与用途声明",
    OBTAIN_TECHNICAL_SPECIFICATION: "补充制造商完整技术规格",
    OBTAIN_BATTERY_TRANSPORT_DOCUMENTS: "补充电池运输合规文件",
    OBTAIN_FINAL_USER_AND_REEXPORT_PATH: "核实最终用户及转售路径",
    OBTAIN_FULL_TECHNICAL_PARAMETERS: "补充敏感参数并完成技术复核",
  },
  owner: {
    COMPLIANCE_REVIEWER: "合规复核人",
    SALES_OPERATIONS: "销售运营",
    EXPORT_CONTROL_REVIEWER: "出口管制复核人",
    LOGISTICS_OPERATOR: "物流操作",
  },
};

const elements = {
  queueState: document.querySelector("#queue-state"),
  recordList: document.querySelector("#record-list"),
  detailState: document.querySelector("#detail-state"),
  detailContent: document.querySelector("#detail-content"),
  detailHeading: document.querySelector("#detail-heading"),
  country: document.querySelector("#record-country"),
  customer: document.querySelector("#customer-name"),
  owner: document.querySelector("#record-owner"),
  screenButton: document.querySelector("#screen-button"),
  screeningPanel: document.querySelector("#screening-panel"),
};

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
    ...options,
  });
  if (!response.ok) {
    throw new Error(`请求失败，HTTP ${response.status}`);
  }
  return response.json();
}

function recordStatus(recordId) {
  const screening = state.screenings.get(recordId);
  if (!screening) {
    return ["待审查", ""];
  }
  const [label, signalClass] = labels.signal[screening.result.signal];
  const statusClass = signalClass.replace("signal", "status");
  return [label, statusClass];
}

function renderQueue() {
  elements.queueState.hidden = true;
  elements.recordList.innerHTML = state.records
    .map((record) => {
      const [statusLabel, statusClass] = recordStatus(record.record_id);
      const selected = record.record_id === state.selectedRecordId;
      return `
        <div class="record-list-item" role="listitem">
          <button
            type="button"
            class="record-button${selected ? " is-selected" : ""}"
            data-record-id="${escapeHtml(record.record_id)}"
            aria-pressed="${selected}"
          >
            <span class="record-topline">
              <span class="record-number">${escapeHtml(record.quote_number)}</span>
              <span class="record-status ${escapeHtml(statusClass)}">${escapeHtml(statusLabel)}</span>
            </span>
            <span class="record-customer">${escapeHtml(record.customer_name)}</span>
            <span class="record-route">
              <span>${escapeHtml(record.route_summary)}</span>
              <span>${escapeHtml(record.service_mode)}</span>
            </span>
            <span class="record-goods">${escapeHtml(record.goods_summary)}</span>
            <span class="record-meta">
              <span>${escapeHtml(record.sales_owner)}</span>
              <span class="record-amount">${escapeHtml(record.currency)} ${escapeHtml(record.amount)}</span>
            </span>
          </button>
        </div>`;
    })
    .join("");

  elements.recordList.querySelectorAll("[data-record-id]").forEach((button) => {
    button.addEventListener("click", () => selectRecord(button.dataset.recordId));
  });
}

function setText(selector, value) {
  const element = document.querySelector(selector);
  if (element) {
    element.textContent = value;
  }
}

function renderDocuments(documents) {
  const container = document.querySelector("#document-list");
  container.innerHTML = documents
    .map((item) => {
      const [statusLabel, statusClass] = labels.document[item.status];
      return `
        <div class="document-row">
          <span class="document-name">${escapeHtml(item.label)}</span>
          <span class="document-status ${escapeHtml(statusClass)}">${escapeHtml(statusLabel)}</span>
        </div>`;
    })
    .join("");
}

function renderMapping(mapping) {
  const values = [
    ["外部系统", mapping.external_system],
    ["业务对象", `${mapping.external_object_type} / QUOTE_RELEASE`],
    ["参与方", `${mapping.party_count} 个角色化主体`],
    ["货物与单证", `${mapping.goods_line_count} 行货物 / ${mapping.document_count} 份文件`],
    ["路线与付款", `${mapping.has_route ? "已映射" : "缺失"} / ${mapping.has_payment_path ? "已映射" : "缺失"}`],
    ["输入指纹", `${mapping.canonical_input_hash.slice(0, 20)}...`],
  ];
  document.querySelector("#mapping-list").innerHTML = values
    .map(
      ([term, value]) => `
        <div>
          <dt>${escapeHtml(term)}</dt>
          <dd>${escapeHtml(value)}</dd>
        </div>`,
    )
    .join("");
}

function renderEmptyScreening() {
  const template = document.querySelector("#empty-screening-template");
  elements.screeningPanel.replaceChildren(template.content.cloneNode(true));
}

function renderOfficialScreening(payload) {
  const result = payload.result;
  const [signalLabel, signalClass] = labels.signal[result.signal];
  const actionLabel = labels.action[result.business_action] || result.business_action;
  const priority = result.signal === "RED" ? "P0" : result.signal === "YELLOW" ? "P1" : "NONE";
  const sanctions = result.sanctions.name_evidence.length
    ? result.sanctions.name_evidence
        .map(
          (item) => `
            <article class="risk-item">
              <div class="risk-item-header">
                <strong>EU FSF 官方别名候选</strong>
                <span class="risk-priority priority-p0">${escapeHtml(item.eu_reference_number)}</span>
              </div>
              <p>主体类型：${escapeHtml(item.subject_type)}；强别名：${item.strong_alias ? "是" : "否"}</p>
              <p>证据定位：${escapeHtml(item.source_native_locator)}</p>
            </article>`,
        )
        .join("")
    : '<div class="no-open-risk">本次精确规范化名称没有官方名单候选；这不等于已完成模糊、音译或所有权审查。</div>';
  const technicalAssessment = result.dual_use.technical_assessment;
  const technicalComparisons = technicalAssessment
    ? technicalAssessment.comparisons
        .map(
          (item) =>
            `${escapeHtml(item.fact_id)}：${escapeHtml(item.actual_value)} ${escapeHtml(item.unit)} ` +
            `${escapeHtml(item.operator)} ${escapeHtml(item.threshold_value)}；${item.matched ? "满足" : "不满足"}`,
        )
        .join("<br>")
    : "未提交经审核的结构化技术事实";
  const technicalDetail = technicalAssessment
    ? `<p>技术规则：${escapeHtml(technicalAssessment.rule_id || "未选择")} / ${escapeHtml(technicalAssessment.status)}</p>
       <p>参数比较：${technicalComparisons || "等待补充参数"}</p>
       <p>规则包：${escapeHtml(technicalAssessment.rule_bundle_id)} @ ${escapeHtml(technicalAssessment.rule_version)}</p>`
    : `<p>技术参数判定：${technicalComparisons}</p>`;
  const dualUse = `
    <article class="risk-item">
      <div class="risk-item-header">
        <strong>EU Annex I 官方控制项</strong>
        <span class="risk-priority priority-p1">${escapeHtml(result.dual_use.status)}</span>
      </div>
      <p>控制号：${escapeHtml(result.dual_use.requested_code || "未提供")}</p>
      <p>缺失事实：${escapeHtml(result.dual_use.missing_facts.join("、") || "无")}</p>
      <p>证据定位：${escapeHtml(result.dual_use.source_native_locator || "未命中控制项")}</p>
      ${technicalDetail}
    </article>`;
  const events = payload.integration_events
    .map(
      (event) => `
        <div class="integration-event">
          <div><strong>${escapeHtml(event.label)}</strong><span>${escapeHtml(event.detail)}</span></div>
          <time datetime="${escapeHtml(event.occurred_at)}">${escapeHtml(formatTime(event.occurred_at))}</time>
        </div>`,
    )
    .join("");

  elements.screeningPanel.innerHTML = `
    <section class="decision-block ${escapeHtml(signalClass)}" aria-label="真实官方来源审查结论">
      <div class="decision-meta">
        <span class="signal-label">${escapeHtml(signalLabel)}</span>
        <span class="priority-label">${escapeHtml(priority)}</span>
        <span class="action-label-result">${escapeHtml(actionLabel)}</span>
      </div>
      <h4 class="decision-title">真实活跃官方来源审查</h4>
      <p class="decision-copy">名单与两用物项结果绑定同一个不可变来源版本；系统不会自动放行。</p>
      <p class="result-warning">${escapeHtml(payload.warning)}</p>
    </section>
    <section class="result-section">
      <h4 class="subsection-title">官方制裁名单证据</h4>
      <div class="risk-list">${sanctions}</div>
    </section>
    <section class="result-section">
      <h4 class="subsection-title">官方两用物项证据</h4>
      <div class="risk-list">${dualUse}</div>
    </section>
    <section class="result-section">
      <h4 class="subsection-title">CRM 与 TradeSieve 交互</h4>
      <div class="integration-list">${events}</div>
    </section>
    <section class="result-section">
      <h4 class="subsection-title">来源版本与证据哈希</h4>
      <div class="result-identifiers">
        <div><span>source_bundle_id</span><br>${escapeHtml(result.source_bundle_id)}</div>
        <div><span>FSF snapshot</span><br>${escapeHtml(result.sanctions.source_snapshot_id)}</div>
        <div><span>Annex I snapshot</span><br>${escapeHtml(result.dual_use.source_snapshot_id)}</div>
      </div>
    </section>`;
}

function renderScreening(payload) {
  if (payload.live_official_sources) {
    renderOfficialScreening(payload);
    return;
  }
  const result = payload.result;
  const [signalLabel, signalClass] = labels.signal[result.signal];
  const stateLabel = labels.state[result.state] || result.state;
  const actionLabel = labels.action[result.business_action] || result.business_action;
  const findings = result.findings.length
    ? result.findings
        .map(
          (finding) => `
            <article class="risk-item">
              <div class="risk-item-header">
                <strong>${escapeHtml(labels.finding[finding.kind] || finding.kind)}</strong>
                <span class="risk-priority priority-${escapeHtml(finding.priority.toLowerCase())}">${escapeHtml(finding.priority)}</span>
              </div>
              <p>${escapeHtml(finding.summary)}</p>
              <p>必须动作：${escapeHtml(labels.requiredAction[finding.required_action] || finding.required_action)}<br>责任角色：${escapeHtml(labels.owner[finding.owner_role] || finding.owner_role)}</p>
            </article>`,
        )
        .join("")
    : '<div class="no-open-risk">合成确定性检查没有开放 P0/P1，但仍不能自动放行。</div>';
  const evidence = result.required_evidence.length
    ? result.required_evidence
        .map(
          (item) => `
            <article class="evidence-item">
              <div class="risk-item-header">
                <strong>${escapeHtml(labels.evidence[item.evidence_type] || item.evidence_type)}</strong>
                <span class="evidence-state">待补件</span>
              </div>
              <p>${escapeHtml(item.description)}</p>
            </article>`,
        )
        .join("")
    : '<div class="no-open-risk">当前合成结果没有新增补件要求。</div>';
  const events = payload.integration_events
    .map(
      (event) => `
        <div class="integration-event">
          <div>
            <strong>${escapeHtml(event.label)}</strong>
            <span>${escapeHtml(event.detail)}</span>
          </div>
          <time datetime="${escapeHtml(event.occurred_at)}">${escapeHtml(formatTime(event.occurred_at))}</time>
        </div>`,
    )
    .join("");

  elements.screeningPanel.innerHTML = `
    <section class="decision-block ${escapeHtml(signalClass)}" aria-label="审查结论">
      <div class="decision-meta">
        <span class="signal-label">${escapeHtml(signalLabel)}</span>
        <span class="priority-label">${escapeHtml(result.highest_priority)}</span>
        <span class="action-label-result">${escapeHtml(actionLabel)}</span>
      </div>
      <h4 class="decision-title">${escapeHtml(stateLabel)}</h4>
      <p class="decision-copy">${escapeHtml(result.summary)}</p>
      <p class="result-warning">${escapeHtml(payload.warning)}</p>
    </section>
    <section class="result-section" aria-labelledby="risk-list-heading">
      <h4 id="risk-list-heading" class="subsection-title">风险事项</h4>
      <div class="risk-list">${findings}</div>
    </section>
    <section class="result-section" aria-labelledby="evidence-list-heading">
      <h4 id="evidence-list-heading" class="subsection-title">所需证据</h4>
      <div class="evidence-list">${evidence}</div>
    </section>
    <section class="result-section" aria-labelledby="integration-heading">
      <h4 id="integration-heading" class="subsection-title">CRM 与 TradeSieve 交互</h4>
      <div class="integration-list">${events}</div>
    </section>
    <section class="result-section" aria-labelledby="reference-heading">
      <h4 id="reference-heading" class="subsection-title">外部系统保存的引用</h4>
      <div class="result-identifiers">
        <div><span>screening_id</span><br>${escapeHtml(result.screening_id)}</div>
        <div><span>case_id</span><br>${escapeHtml(result.case_id || "无")}</div>
        <div><span>result_hash</span><br>${escapeHtml(result.result_hash)}</div>
      </div>
    </section>`;
}

function formatTime(value) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(parsed);
}

function renderDetail(payload) {
  const { record, mapping } = payload;
  elements.detailHeading.textContent = record.quote_number;
  elements.country.textContent = record.customer_country;
  elements.customer.textContent = record.customer_name;
  elements.owner.textContent = `负责人：${record.sales_owner}`;
  setText("#service-mode", record.service_mode);
  setText("#incoterm", record.incoterm);
  setText("#origin", record.origin);
  setText("#destination", record.destination);
  setText("#route-summary", record.route_summary);
  setText("#shipment-summary", record.shipment_summary);
  setText("#goods-summary", record.goods_summary);
  setText("#quote-amount", `${record.currency} ${record.amount}`);
  setText("#action-label", record.action_label);
  setText("#action-due-at", record.action_due_at);
  renderDocuments(record.documents);
  renderMapping(mapping);
  const existing = state.screenings.get(record.record_id);
  if (existing) {
    renderScreening(existing);
    elements.screenButton.textContent = "重新执行演示审查";
  } else {
    renderEmptyScreening();
    elements.screenButton.textContent = "发起合规审查";
  }
  elements.detailState.hidden = true;
  elements.detailContent.hidden = false;
}

async function selectRecord(recordId) {
  if (!recordId || state.selectedRecordId === recordId) {
    return;
  }
  state.selectedRecordId = recordId;
  renderQueue();
  elements.detailContent.hidden = true;
  elements.detailState.hidden = false;
  elements.detailState.innerHTML = `
    <div class="detail-skeleton" aria-label="正在加载报价详情">
      <span></span><span></span><span></span>
    </div>`;
  try {
    const payload = await requestJson(`/demo/api/crm/records/${encodeURIComponent(recordId)}`);
    if (state.selectedRecordId === recordId) {
      renderDetail(payload);
    }
  } catch (error) {
    if (state.selectedRecordId === recordId) {
      elements.detailState.innerHTML = `<div class="detail-error">${escapeHtml(error.message)}</div>`;
    }
  }
}

async function screenSelectedRecord() {
  const recordId = state.selectedRecordId;
  if (!recordId) {
    return;
  }
  elements.screenButton.disabled = true;
  elements.screenButton.textContent = "审查中...";
  elements.screeningPanel.innerHTML = `
    <div class="detail-skeleton" aria-label="TradeSieve 正在处理合成审查">
      <span></span><span></span><span></span>
    </div>`;
  try {
    const payload = await requestJson(
      `/demo/api/crm/records/${encodeURIComponent(recordId)}/screen-official`,
      { method: "POST" },
    );
    state.screenings.set(recordId, payload);
    if (state.selectedRecordId === recordId) {
      renderScreening(payload);
      elements.screenButton.textContent = "重新执行官方审查";
    }
    renderQueue();
  } catch (error) {
    if (state.selectedRecordId === recordId) {
      elements.screeningPanel.innerHTML = `<div class="screening-error">审查服务不可用，CRM 必须保持拦截。${escapeHtml(error.message)}</div>`;
      elements.screenButton.textContent = "重试审查";
    }
  } finally {
    elements.screenButton.disabled = false;
  }
}

async function bootstrap() {
  elements.screenButton.addEventListener("click", screenSelectedRecord);
  try {
    const payload = await requestJson("/demo/api/crm/records");
    state.records = payload.records;
    renderQueue();
    if (state.records.length === 0) {
      elements.queueState.hidden = false;
      elements.queueState.innerHTML = '<div class="queue-error">没有可演示的合成报价。</div>';
      return;
    }
    await selectRecord(state.records[0].record_id);
  } catch (error) {
    elements.queueState.innerHTML = `<div class="queue-error">无法加载演示数据。${escapeHtml(error.message)}</div>`;
    elements.recordList.hidden = true;
  }
}

bootstrap();
