/**
 * Edvexa Data Quality & ETL Monitoring Platform
 * Survey Data Scrubbing & Quality Control Client Module
 * Version: 2.7.0
 */

const Surveys = {
  currentAuditId: null,
  currentAudit: null,
  qqData: null,
  rawData: null,
  mappingData: [],
  columns: [],
  respondents: [],
  findings: [],
  currentTab: 'ALL',
  findingFilter: 'ALL',
  charts: {
    disposition: null,
    findings: null
  },

  isAdminAuthenticated: false,

  async init() {
    this.bindEvents();
    this.bindWizard();
    this.bindAuth();
    await this.checkAdminAuth();
    if (this.isAdminAuthenticated) {
      this.loadHistory();
      this.loadLatestDashboard();
    }
  },


  async apiFetch(url, options = {}) {
    options.credentials = options.credentials || 'same-origin';
    options.headers = options.headers || {};
    options.headers['X-Requested-With'] = 'XMLHttpRequest';
    const targetUrl = url.startsWith('http') ? url : `${window.API_BASE || ''}${url}`;
    const resp = await fetch(targetUrl, options);
    if (resp.status === 401) {
      this.isAdminAuthenticated = false;
      const authCard = document.getElementById('surveyAuthRequiredCard');
      if (authCard) authCard.classList.remove('d-none');
      this.showToast('Admin authentication required for Survey Data Scrubbing.', 'warning');
    }
    return resp;

  },



  async checkAdminAuth() {

    try {
      const resp = await fetch('/api/auth/session', { credentials: 'same-origin' });
      const data = await resp.json();
      this.isAdminAuthenticated = !!data.authenticated;
      const authCard = document.getElementById('surveyAuthRequiredCard');
      if (authCard) {
        if (this.isAdminAuthenticated) {
          authCard.classList.add('d-none');
        } else {
          authCard.classList.remove('d-none');
        }
      }
      return this.isAdminAuthenticated;
    } catch (err) {
      console.warn('Could not verify admin session:', err);
      return false;
    }
  },

  bindAuth() {
    const loginBtn = document.getElementById('surveyAdminLoginBtn');
    const pwdInput = document.getElementById('surveyAdminPasswordInput');

    const handleLogin = async () => {
      const key = pwdInput ? pwdInput.value.trim() : '';
      if (!key) {
        this.showToast('Please enter an Admin Secret Key.', 'warning');
        return;
      }
      try {
        if (loginBtn) {
          loginBtn.disabled = true;
          loginBtn.textContent = 'Verifying...';
        }
        const resp = await fetch('/api/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ password: key })
        });
        const data = await resp.json();
        if (resp.ok && data.authenticated) {
          this.isAdminAuthenticated = true;
          this.showToast('Admin session authenticated successfully.', 'success');
          const authCard = document.getElementById('surveyAuthRequiredCard');
          if (authCard) authCard.classList.add('d-none');
          if (pwdInput) pwdInput.value = '';
          this.loadHistory();
          this.loadLatestDashboard();
        } else {
          this.showToast(data.detail || 'Invalid administrative credentials.', 'error');
        }
      } catch (err) {
        this.showToast('Authentication failed: ' + err.message, 'error');
      } finally {
        if (loginBtn) {
          loginBtn.disabled = false;
          loginBtn.textContent = 'Unlock Module';
        }
      }
    };

    if (loginBtn) loginBtn.addEventListener('click', handleLogin);
    if (pwdInput) {
      pwdInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') handleLogin();
      });
    }
  },

  bindEvents() {
    // Dataset Selector redirection
    const datasetSelect = document.getElementById('datasetSelect');
    if (datasetSelect) {
      datasetSelect.addEventListener('change', (e) => {
        if (e.target.value === 'surveys') {
          if (window.App && typeof window.App.navigateTo === 'function') {
            window.App.navigateTo('surveys');
          } else {
            const navBtn = document.getElementById('nav-surveys');
            if (navBtn) navBtn.click();
          }
        }
      });
    }

    // Top action buttons
    const startAuditBtn = document.getElementById('surveyStartAuditBtn');
    if (startAuditBtn) {
      startAuditBtn.addEventListener('click', () => {
        const wizardCard = document.getElementById('surveyWizardCard');
        if (wizardCard) {
          wizardCard.scrollIntoView({ behavior: 'smooth' });
          this.setStep(1);
        }
      });
    }

    const toggleDashboardBtn = document.getElementById('surveyToggleDashboardBtn');
    if (toggleDashboardBtn) {
      toggleDashboardBtn.addEventListener('click', () => {
        const dashboard = document.getElementById('surveyDashboardContainer');
        if (dashboard) dashboard.scrollIntoView({ behavior: 'smooth' });
      });
    }

    const exportBtn = document.getElementById('surveyExportCurrentBtn');
    if (exportBtn) {
      exportBtn.addEventListener('click', () => {
        if (this.currentAuditId) {
          window.location.href = `/api/surveys/audits/${this.currentAuditId}/export/excel`;
        }
      });
    }

    // Survey Reset Modal triggers
    const openResetBtn = document.getElementById('surveyOpenResetBtn');
    const resetModal = document.getElementById('surveyResetModal');
    const closeResetBtn = document.getElementById('closeSurveyResetModalBtn');
    const cancelResetBtn = document.getElementById('cancelSurveyResetBtn');
    const resetCheck = document.getElementById('surveyResetConfirmCheck');
    const confirmResetBtn = document.getElementById('confirmSurveyResetBtn');

    if (openResetBtn && resetModal) {
      openResetBtn.addEventListener('click', () => {
        resetModal.classList.remove('d-none');
        if (resetCheck) resetCheck.checked = false;
        if (confirmResetBtn) confirmResetBtn.disabled = true;
      });
    }

    const closeReset = () => {
      if (resetModal) resetModal.classList.add('d-none');
    };
    if (closeResetBtn) closeResetBtn.addEventListener('click', closeReset);
    if (cancelResetBtn) cancelResetBtn.addEventListener('click', closeReset);

    if (resetCheck && confirmResetBtn) {
      resetCheck.addEventListener('change', () => {
        confirmResetBtn.disabled = !resetCheck.checked;
      });
    }

    if (confirmResetBtn) {
      confirmResetBtn.addEventListener('click', async () => {
        try {
          confirmResetBtn.disabled = true;
          confirmResetBtn.textContent = 'Resetting...';
          const resp = await this.apiFetch('/api/surveys/reset-survey-data', { method: 'POST' });
          const data = await resp.json();
          closeReset();
          this.showToast('Survey audit runs and respondent records permanently reset.', 'success');
          this.currentAuditId = null;
          this.currentAudit = null;
          this.respondents = [];
          this.findings = [];
          this.loadLatestDashboard();
          this.loadHistory();
        } catch (err) {
          this.showToast('Failed to reset survey data: ' + err.message, 'error');
        } finally {
          confirmResetBtn.disabled = false;
          confirmResetBtn.textContent = 'Permanently Reset Surveys';
        }
      });
    }

    // Respondent Search & Tab Filters
    const searchInput = document.getElementById('surveyRespondentSearch');
    if (searchInput) {
      searchInput.addEventListener('input', () => this.renderRespondentsTable());
    }

    ['ALL', 'ACCEPTED', 'REVIEW_REQUIRED', 'REJECTED'].forEach(tab => {
      const btn = document.getElementById(`surveyTab${tab === 'ALL' ? 'All' : tab === 'ACCEPTED' ? 'Accepted' : tab === 'REVIEW_REQUIRED' ? 'Review' : 'Rejected'}`);
      if (btn) {
        btn.addEventListener('click', () => {
          document.querySelectorAll('#view-surveys .tab-btn').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          this.currentTab = tab;
          this.renderRespondentsTable();
        });
      }
    });

    // Finding Severity Filter
    const severitySelect = document.getElementById('surveyFindingSeverityFilter');
    if (severitySelect) {
      severitySelect.addEventListener('change', (e) => {
        this.findingFilter = e.target.value;
        this.renderFindingsTable();
      });
    }

    // Review Modal Close
    const closeReviewBtn = document.getElementById('closeSurveyReviewModalBtn');
    const cancelReviewBtn = document.getElementById('cancelSurveyReviewBtn');
    const reviewModal = document.getElementById('surveyReviewModal');
    const closeReview = () => {
      if (reviewModal) reviewModal.classList.add('d-none');
    };
    if (closeReviewBtn) closeReviewBtn.addEventListener('click', closeReview);
    if (cancelReviewBtn) cancelReviewBtn.addEventListener('click', closeReview);

    // Review Form Save
    const saveReviewBtn = document.getElementById('saveSurveyReviewBtn');
    if (saveReviewBtn) {
      saveReviewBtn.addEventListener('click', async (e) => {
        e.preventDefault();
        await this.submitReviewDecision();
      });
    }
  },

  bindWizard() {
    // Step navigation buttons
    [1, 2, 3, 4].forEach(s => {
      const btn = document.getElementById(`wizardBtn${s}`);
      if (btn) {
        btn.addEventListener('click', () => {
          if (s === 2 && !this.qqData) return;
          if (s === 3 && !this.rawData) return;
          if (s === 4 && (!this.qqData || !this.rawData)) return;
          this.setStep(s);
        });
      }
    });

    // Step 1: Questionnaire Upload
    const qqDropzone = document.getElementById('surveyQqDropzone');
    const qqFileInput = document.getElementById('surveyQqFileInput');
    const step1NextBtn = document.getElementById('surveyStep1NextBtn');

    if (qqDropzone && qqFileInput) {
      qqDropzone.addEventListener('click', () => qqFileInput.click());
      qqDropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        qqDropzone.classList.add('dragover');
      });
      qqDropzone.addEventListener('dragleave', () => qqDropzone.classList.remove('dragover'));
      qqDropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        qqDropzone.classList.remove('dragover');
        if (e.dataTransfer.files.length > 0) {
          this.uploadQuestionnaire(e.dataTransfer.files[0]);
        }
      });
      qqFileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
          this.uploadQuestionnaire(e.target.files[0]);
        }
      });
    }

    if (step1NextBtn) {
      step1NextBtn.addEventListener('click', () => this.setStep(2));
    }

    // Step 2: Raw Data Upload
    const rawDropzone = document.getElementById('surveyRawDropzone');
    const rawFileInput = document.getElementById('surveyRawFileInput');
    const step2PrevBtn = document.getElementById('surveyStep2PrevBtn');
    const step2NextBtn = document.getElementById('surveyStep2NextBtn');
    const sheetSelect = document.getElementById('surveySheetSelect');

    if (rawDropzone && rawFileInput) {
      rawDropzone.addEventListener('click', () => rawFileInput.click());
      rawDropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        rawDropzone.classList.add('dragover');
      });
      rawDropzone.addEventListener('dragleave', () => rawDropzone.classList.remove('dragover'));
      rawDropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        rawDropzone.classList.remove('dragover');
        if (e.dataTransfer.files.length > 0) {
          this.uploadRawData(e.dataTransfer.files[0]);
        }
      });
      rawFileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
          this.uploadRawData(e.target.files[0]);
        }
      });
    }

    if (sheetSelect) {
      sheetSelect.addEventListener('change', async (e) => {
        await this.loadSheetPreview(e.target.value);
      });
    }

    if (step2PrevBtn) step2PrevBtn.addEventListener('click', () => this.setStep(1));
    if (step2NextBtn) {
      step2NextBtn.addEventListener('click', async () => {
        await this.fetchMappingSuggestions();
        this.setStep(3);
      });
    }

    // Step 3: Mapping review
    const step3PrevBtn = document.getElementById('surveyStep3PrevBtn');
    const step3NextBtn = document.getElementById('surveyStep3NextBtn');
    if (step3PrevBtn) step3PrevBtn.addEventListener('click', () => this.setStep(2));
    if (step3NextBtn) step3NextBtn.addEventListener('click', () => this.setStep(4));

    // Step 4: QC Rules & Execute
    const step4PrevBtn = document.getElementById('surveyStep4PrevBtn');
    const executeBtn = document.getElementById('surveyExecuteAuditBtn');
    if (step4PrevBtn) step4PrevBtn.addEventListener('click', () => this.setStep(3));
    if (executeBtn) executeBtn.addEventListener('click', () => this.executeAudit());

    // Step 4 threshold manual override listeners
    const minDurInp = document.getElementById('ruleMinExpectedDuration');
    const maxDurInp = document.getElementById('ruleMaxExpectedDuration');
    const durBadge = document.getElementById('ruleDurationSourceBadge');
    [minDurInp, maxDurInp].forEach(inp => {
      if (inp) {
        inp.addEventListener('input', () => {
          if (durBadge) {
            durBadge.textContent = 'Source: Manual Override';
            durBadge.className = 'badge-warning text-xs';
          }
        });
      }
    });

    const pageTimerEnabled = document.getElementById('rulePageTimerEnabled');
    const pageTimerInp = document.getElementById('rulePageTimer');
    const pageTimerBadge = document.getElementById('rulePageTimerSourceBadge');
    if (pageTimerEnabled && pageTimerInp) {
      pageTimerEnabled.addEventListener('change', () => {
        pageTimerInp.disabled = !pageTimerEnabled.checked;
        if (pageTimerBadge) {
          pageTimerBadge.textContent = pageTimerEnabled.checked ? 'Source: Manual Override' : 'Source: Not in QQ (Telemetry Only)';
          pageTimerBadge.className = pageTimerEnabled.checked ? 'badge-warning text-xs' : 'badge-neutral text-xs';
        }
      });
      pageTimerInp.addEventListener('input', () => {
        if (pageTimerBadge) {
          pageTimerBadge.textContent = 'Source: Manual Override';
          pageTimerBadge.className = 'badge-warning text-xs';
        }
      });
    }
  },

  setStep(stepNum) {
    [1, 2, 3, 4].forEach(s => {
      const stepEl = document.getElementById(`surveyStep${s}`);
      const btnEl = document.getElementById(`wizardBtn${s}`);
      if (stepEl) {
        if (s === stepNum) stepEl.classList.remove('d-none');
        else stepEl.classList.add('d-none');
      }
      if (btnEl) {
        if (s === stepNum) btnEl.classList.add('active');
        else btnEl.classList.remove('active');
      }
    });
  },

  async uploadQuestionnaire(file) {
    const formData = new FormData();
    formData.append('file', file);

    const summaryBox = document.getElementById('surveyQqSummaryBox');
    const fileNameEl = document.getElementById('surveyQqFileName');
    const pillsEl = document.getElementById('surveyQqPills');
    const limitAlert = document.getElementById('surveyQqLimitationsAlert');
    const nextBtn = document.getElementById('surveyStep1NextBtn');

    try {
      this.showToast(`Uploading questionnaire: ${file.name}...`, 'info');
      const resp = await this.apiFetch('/api/surveys/upload-qq', {
        method: 'POST',
        body: formData
      });
      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || 'Failed to parse questionnaire.');
      }
      const data = await resp.json();
      this.qqData = data;

      if (summaryBox) summaryBox.classList.remove('d-none');
      if (fileNameEl) fileNameEl.textContent = `${data.original_name} (${(data.file_size_bytes / 1024).toFixed(1)} KB)`;

      if (data.is_scanned && limitAlert) {
        limitAlert.textContent = data.extraction_notes || 'Scanned or unreadable document detected. OCR or structured text format recommended.';
        limitAlert.classList.remove('d-none');
      } else if (limitAlert) {
        limitAlert.classList.add('d-none');
      }

      if (pillsEl) {
        const totalQ = data.total_questions_detected || data.questions_count || (data.questions ? data.questions.length : 0);
        let durPill = 'Not specified in QQ (Default: 7–15 min)';
        if (data.detected_duration_min && data.detected_duration_max) {
          durPill = `${data.detected_duration_min}–${data.detected_duration_max} min (from QQ)`;
        } else if (data.detected_duration_min) {
          durPill = `${data.detected_duration_min} min (from QQ)`;
        }

        let timerPill = '0 (No timer rule in QQ)';
        if (data.page_timer_threshold_seconds) {
          timerPill = `${data.page_timer_threshold_seconds}s threshold (from QQ)`;
        } else if (data.detected_page_timers && data.detected_page_timers.length > 0) {
          timerPill = `${data.detected_page_timers.length} rules (from QQ)`;
        }

        const screenerCount = data.screening_criteria ? data.screening_criteria.length : (data.screener_rules ? data.screener_rules.length : 0);
        const routingCount = data.routing_logic ? data.routing_logic.length : (data.skip_logic_rules ? data.skip_logic_rules.length : 0);

        pillsEl.innerHTML = `
          <span class="badge-neutral">&bull; Questions: <strong>${totalQ}</strong></span>
          <span class="badge-neutral">&bull; Expected Duration: <strong>${durPill}</strong></span>
          <span class="badge-neutral">&bull; Page Timers: <strong>${timerPill}</strong></span>
          <span class="badge-neutral">&bull; Screeners: <strong>${screenerCount}</strong></span>
          <span class="badge-neutral">&bull; Skip Logic: <strong>${routingCount} rules</strong></span>
        `;
      }

      // Populate Step 4 rules based on extraction
      const minInp = document.getElementById('ruleMinExpectedDuration');
      const maxInp = document.getElementById('ruleMaxExpectedDuration');
      const speedInp = document.getElementById('ruleSpeedingThreshold');
      const durBadgeEl = document.getElementById('ruleDurationSourceBadge');
      const speedHelpEl = document.getElementById('ruleSpeedingHelp');

      if (data.detected_duration_min) {
        if (minInp) minInp.value = data.detected_duration_min;
        if (maxInp) maxInp.value = data.detected_duration_max || Math.round(data.detected_duration_min * 1.5);
        if (durBadgeEl) {
          durBadgeEl.textContent = 'Source: Questionnaire Specification';
          durBadgeEl.className = 'badge-success text-xs';
        }
        const calculatedConcern = Math.max(2, Math.round(data.detected_duration_min * 0.5 * 10) / 10);
        if (speedInp) speedInp.value = calculatedConcern;
        if (speedHelpEl) speedHelpEl.textContent = `Completions faster than ${calculatedConcern}m trigger speeding concern (derived as 1/2 QQ min).`;
      } else {
        if (minInp) minInp.value = 7;
        if (maxInp) maxInp.value = 15;
        if (speedInp) speedInp.value = 5;
        if (durBadgeEl) {
          durBadgeEl.textContent = 'Source: Platform Default (7–15m)';
          durBadgeEl.className = 'badge-neutral text-xs';
        }
      }

      // Page timer rule configuration
      const pageTimerInpEl = document.getElementById('rulePageTimer');
      const pageTimerEnabledEl = document.getElementById('rulePageTimerEnabled');
      const pageTimerBadgeEl = document.getElementById('rulePageTimerSourceBadge');
      const pageTimerHelpEl = document.getElementById('rulePageTimerHelp');

      if (data.page_timer_threshold_seconds) {
        if (pageTimerInpEl) {
          pageTimerInpEl.value = data.page_timer_threshold_seconds;
          pageTimerInpEl.disabled = false;
        }
        if (pageTimerEnabledEl) pageTimerEnabledEl.checked = true;
        if (pageTimerBadgeEl) {
          pageTimerBadgeEl.textContent = 'Source: Questionnaire Specification';
          pageTimerBadgeEl.className = 'badge-success text-xs';
        }
        if (pageTimerHelpEl) pageTimerHelpEl.textContent = `Pages submitted faster than ${data.page_timer_threshold_seconds}s flagged per QQ specification.`;
      } else {
        if (pageTimerEnabledEl) pageTimerEnabledEl.checked = false;
        if (pageTimerInpEl) pageTimerInpEl.disabled = true;
        if (pageTimerBadgeEl) {
          pageTimerBadgeEl.textContent = 'Source: Not in QQ (Telemetry Only)';
          pageTimerBadgeEl.className = 'badge-neutral text-xs';
        }
        if (pageTimerHelpEl) pageTimerHelpEl.textContent = 'No page timer rule in QQ. Timers recorded as contextual telemetry (no rejections applied).';
      }

      if (nextBtn) nextBtn.disabled = false;
      const wizardBtn1 = document.getElementById('wizardBtn1');
      if (wizardBtn1) wizardBtn1.classList.add('completed');

      this.showToast(`Questionnaire parsed: ${data.total_questions_detected} questions identified.`, 'success');
    } catch (err) {
      this.showToast('Error uploading questionnaire: ' + err.message, 'error');
    }
  },

  async uploadRawData(file) {
    const formData = new FormData();
    formData.append('file', file);

    const sheetContainer = document.getElementById('surveySheetSelectContainer');
    const sheetSelect = document.getElementById('surveySheetSelect');
    const metaAlert = document.getElementById('surveyMetadataAlert');
    const previewContainer = document.getElementById('surveyDataPreviewContainer');
    const nextBtn = document.getElementById('surveyStep2NextBtn');

    try {
      this.showToast(`Uploading respondent data: ${file.name}...`, 'info');
      const resp = await this.apiFetch('/api/surveys/upload-raw', {
        method: 'POST',
        body: formData
      });
      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || 'Failed to parse raw dataset.');
      }
      const data = await resp.json();
      this.rawData = data;
      this.columns = data.columns || [];

      // Sheet selection if multi-sheet excel
      if (data.is_excel && data.sheets && data.sheets.length > 1 && sheetContainer && sheetSelect) {
        sheetContainer.classList.remove('d-none');
        sheetSelect.innerHTML = data.sheets.map(s => `<option value="${s}" ${s === data.sheet_name ? 'selected' : ''}>${s}</option>`).join('');
      } else if (sheetContainer) {
        sheetContainer.classList.add('d-none');
      }

      // Metadata alert (e.g. Qualtrics 3-row headers)
      if (data.metadata_rows_detected > 0 && metaAlert) {
        metaAlert.textContent = `Intelligent Header Detection: Row ${data.header_row_index + 1} recognized as question codes, with ${data.metadata_rows_detected} metadata/wording rows preceding respondent records.`;
        metaAlert.classList.remove('d-none');
      } else if (metaAlert) {
        metaAlert.classList.add('d-none');
      }

      // Render preview
      this.renderPreviewTable(data.columns, data.preview_rows, data.total_rows_detected);

      if (nextBtn) nextBtn.disabled = false;
      const wizardBtn2 = document.getElementById('wizardBtn2');
      if (wizardBtn2) wizardBtn2.classList.add('completed');

      this.showToast(`Raw dataset loaded: ${data.total_rows_detected} rows and ${data.columns.length} columns detected.`, 'success');
    } catch (err) {
      this.showToast('Error uploading dataset: ' + err.message, 'error');
    }
  },

  async loadSheetPreview(sheetName) {
    if (!this.rawData) return;
    try {
      const resp = await this.apiFetch('/api/surveys/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          raw_filename: this.rawData.filename,
          sheet_name: sheetName,
          header_row_index: 0
        })
      });
      if (!resp.ok) return;
      const data = await resp.json();
      this.rawData.sheet_name = sheetName;
      this.columns = data.columns || [];
      this.renderPreviewTable(data.columns, data.preview_rows, data.total_rows);
    } catch (err) {
      console.error('Failed to preview sheet:', err);
    }
  },

  renderPreviewTable(cols, rows, totalRows) {
    const previewContainer = document.getElementById('surveyDataPreviewContainer');
    const previewMeta = document.getElementById('surveyPreviewMeta');
    const thead = document.getElementById('surveyPreviewHead');
    const tbody = document.getElementById('surveyPreviewBody');

    if (!previewContainer || !thead || !tbody) return;
    previewContainer.classList.remove('d-none');
    if (previewMeta) previewMeta.textContent = `Showing ${rows ? rows.length : 0} of ${totalRows} total respondent rows (${cols ? cols.length : 0} columns)`;

    if (cols && cols.length > 0) {
      thead.innerHTML = `<tr>${cols.map(c => `<th>${this.escapeHtml(c)}</th>`).join('')}</tr>`;
    } else {
      thead.innerHTML = '';
    }

    if (rows && rows.length > 0) {
      tbody.innerHTML = rows.map(r => `
        <tr>${cols.map(c => `<td>${this.escapeHtml(r[c] !== null && r[c] !== undefined ? String(r[c]) : '')}</td>`).join('')}</tr>
      `).join('');
    } else {
      tbody.innerHTML = '<tr><td colspan="100" class="text-center py-2 text-muted">No rows to display.</td></tr>';
    }
  },

  async fetchMappingSuggestions() {
    if (!this.qqData || !this.rawData) return;
    try {
      this.showToast('Auto-correlating questionnaire questions to dataset columns...', 'info');
      const resp = await this.apiFetch('/api/surveys/suggest-mapping', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          qq_filename: this.qqData.filename,
          raw_filename: this.rawData.filename,
          sheet_name: this.rawData.sheet_name
        })
      });
      if (!resp.ok) throw new Error('Failed to retrieve mapping suggestions.');
      const data = await resp.json();
      this.mappingData = data.mappings || [];
      this.renderMappingTable(data);
      const wizardBtn3 = document.getElementById('wizardBtn3');
      if (wizardBtn3) wizardBtn3.classList.add('completed');
    } catch (err) {
      this.showToast('Error suggesting mapping: ' + err.message, 'error');
    }
  },

  renderMappingTable(data) {
    const tbody = document.getElementById('surveyMappingTableBody');
    const highBadge = document.getElementById('surveyMapHighCount');
    const medBadge = document.getElementById('surveyMapMedCount');
    const lowBadge = document.getElementById('surveyMapLowCount');
    const unmappedBadge = document.getElementById('surveyMapUnmappedCount');
    const teleList = document.getElementById('surveyTelemetryList');

    if (highBadge) highBadge.textContent = `High: ${data.high_confidence_count || 0}`;
    if (medBadge) medBadge.textContent = `Medium: ${data.medium_confidence_count || 0}`;
    if (lowBadge) lowBadge.textContent = `Low: ${data.low_confidence_count || 0}`;
    if (unmappedBadge) unmappedBadge.textContent = `Unmapped: ${data.unmapped_count || 0}`;

    if (teleList) {
      const detected = [];
      if (data.start_time_column) detected.push(`Start: ${data.start_time_column}`);
      if (data.end_time_column) detected.push(`End: ${data.end_time_column}`);
      if (data.duration_column) detected.push(`Duration: ${data.duration_column}`);
      if (data.ip_address_column) detected.push(`IP: ${data.ip_address_column}`);
      if (data.response_id_column) detected.push(`ID: ${data.response_id_column}`);
      teleList.textContent = detected.length > 0 ? detected.join(' | ') : 'Standard telemetry detected automatically during QC.';
    }

    if (!tbody) return;
    if (!this.mappingData || this.mappingData.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="text-center py-3 text-muted">No questionnaire questions found to map.</td></tr>';
      return;
    }

    const colOptions = ['<option value="">-- Unmapped / Skip --</option>'].concat(
      this.columns.map(c => `<option value="${this.escapeHtml(c)}">${this.escapeHtml(c)}</option>`)
    ).join('');

    tbody.innerHTML = this.mappingData.map((m, idx) => {
      const confClass = m.confidence === 'HIGH' ? 'badge-confidence-high' :
                        m.confidence === 'MEDIUM' ? 'badge-confidence-medium' :
                        m.confidence === 'LOW' ? 'badge-confidence-low' : 'badge-confidence-unmapped';

      const selectHtml = `
        <select class="form-control text-xs survey-map-select" data-index="${idx}">
          <option value="">-- Unmapped / Skip --</option>
          ${this.columns.map(c => `<option value="${this.escapeHtml(c)}" ${c === m.matched_column ? 'selected' : ''}>${this.escapeHtml(c)}</option>`).join('')}
        </select>
      `;

      return `
        <tr>
          <td><strong>${this.escapeHtml(m.question_number || '')}</strong></td>
          <td><div class="text-xs" style="max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${this.escapeHtml(m.question_text || '')}">${this.escapeHtml(m.question_text || '')}</div></td>
          <td>${selectHtml}</td>
          <td><span class="${confClass}">${m.confidence}</span></td>
          <td><span class="text-xs text-muted">${this.escapeHtml(m.evidence || '')}</span></td>
        </tr>
      `;
    }).join('');

    // Bind change listeners to selects
    tbody.querySelectorAll('.survey-map-select').forEach(sel => {
      sel.addEventListener('change', (e) => {
        const idx = parseInt(e.target.getAttribute('data-index'), 10);
        if (this.mappingData[idx]) {
          this.mappingData[idx].matched_column = e.target.value || null;
          this.mappingData[idx].confidence = e.target.value ? 'HIGH' : 'UNMAPPED';
          this.mappingData[idx].evidence = e.target.value ? 'Manually verified by auditor' : 'Unmapped';
        }
      });
    });
  },

  async executeAudit() {
    if (!this.rawData) {
      this.showToast('Please upload raw respondent data first.', 'warning');
      return;
    }

    const executeBtn = document.getElementById('surveyExecuteAuditBtn');
    const spinner = document.getElementById('surveyAuditBtnSpinner');
    const btnText = document.getElementById('surveyAuditBtnText');

    try {
      if (executeBtn) executeBtn.disabled = true;
      if (spinner) spinner.classList.remove('d-none');
      if (btnText) btnText.textContent = 'Executing Audit Suite...';

      // Collect confirmed mappings
      const confirmedMappings = this.mappingData.map(m => ({
        question_number: m.question_number,
        question_text: m.question_text,
        matched_column: m.matched_column,
        confidence: m.confidence,
        evidence: m.evidence
      }));

      // Collect rule overrides
      const pageTimerEnabled = document.getElementById('rulePageTimerEnabled')?.checked;
      const pageTimerVal = pageTimerEnabled ? parseFloat(document.getElementById('rulePageTimer')?.value || 20) : null;
      const rejectExtremeSpeeding = document.getElementById('ruleRejectExtremeSpeeding')?.checked || false;
      const durBadgeText = document.getElementById('ruleDurationSourceBadge')?.textContent || '';
      const timerBadgeText = document.getElementById('rulePageTimerSourceBadge')?.textContent || '';

      const payload = {
        raw_filename: this.rawData.filename,
        sheet_name: this.rawData.sheet_name || null,
        qq_filename: this.qqData ? this.qqData.filename : null,
        confirmed_mappings: confirmedMappings,
        min_expected_duration_min: parseFloat(document.getElementById('ruleMinExpectedDuration')?.value || 7),
        max_expected_duration_min: parseFloat(document.getElementById('ruleMaxExpectedDuration')?.value || 15),
        speeding_concern_threshold_min: parseFloat(document.getElementById('ruleSpeedingThreshold')?.value || 5),
        extreme_speeding_cutoff_min: parseFloat(document.getElementById('ruleExtremeSpeeding')?.value || 2),
        page_timer_threshold_sec: pageTimerVal,
        duration_source: durBadgeText.replace('Source: ', '').trim(),
        page_timer_source: timerBadgeText.replace('Source: ', '').trim(),
        reject_speeding_under_2min: rejectExtremeSpeeding,
        straightline_min_statements: parseInt(document.getElementById('ruleStraightlineMinStatements')?.value || 4, 10),
        straightline_threshold_pct: parseFloat(document.getElementById('ruleStraightlineThreshold')?.value || 80)
      };

      this.showToast('Executing comprehensive QC audit on respondent records...', 'info');

      const resp = await this.apiFetch('/api/surveys/run-audit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || 'Audit execution failed.');
      }

      const result = await resp.json();
      this.currentAuditId = result.audit_id;
      this.currentAudit = result;

      // Mark wizard completed
      const wizardBtn4 = document.getElementById('wizardBtn4');
      if (wizardBtn4) wizardBtn4.classList.add('completed');

      this.showToast(`Audit Completed! ${result.total_respondents} respondents: ${result.accepted_count} Accepted, ${result.rejected_count} Rejected, ${result.review_required_count} Review Required.`, 'success');

      // Enable export button
      const exportBtn = document.getElementById('surveyExportCurrentBtn');
      if (exportBtn) exportBtn.disabled = false;

      // Load full audit telemetry and render dashboard
      await this.loadAuditDetails(result.audit_id);
      this.loadHistory();

      // Scroll smoothly to dashboard
      const dashboard = document.getElementById('surveyDashboardContainer');
      if (dashboard) dashboard.scrollIntoView({ behavior: 'smooth' });

    } catch (err) {
      this.showToast('Error during audit: ' + err.message, 'error');
    } finally {
      if (executeBtn) executeBtn.disabled = false;
      if (spinner) spinner.classList.add('d-none');
      if (btnText) btnText.textContent = 'Run Survey Scrubbing Audit';
    }
  },

  async loadAuditDetails(auditId) {
    try {
      this.currentAuditId = auditId;
      const [auditResp, respResp, findResp] = await Promise.all([
        this.apiFetch(`/api/surveys/audits/${auditId}`).then(r => r.json()),
        this.apiFetch(`/api/surveys/audits/${auditId}/respondents`).then(r => r.json()),
        this.apiFetch(`/api/surveys/audits/${auditId}/findings`).then(r => r.json())
      ]);

      this.currentAudit = auditResp;
      this.respondents = respResp;
      this.findings = findResp;

      this.populateKpis(auditResp);
      this.renderCharts(auditResp);
      this.renderCoverageTable(auditResp.qc_coverage || []);
      this.renderRespondentsTable();
      this.renderFindingsTable();

      const exportBtn = document.getElementById('surveyExportCurrentBtn');
      if (exportBtn) exportBtn.disabled = false;

    } catch (err) {
      console.error('Failed to load audit details:', err);
    }
  },

  async loadLatestDashboard() {
    try {
      const resp = await this.apiFetch('/api/surveys/dashboard/summary');
      if (!resp.ok) return;
      const data = await resp.json();

      if (data.latest_audit_id) {
        await this.loadAuditDetails(data.latest_audit_id);
      } else {
        // Reset KPIs to 0
        this.populateKpis({
          total_respondents: 0,
          accepted_count: 0,
          rejected_count: 0,
          review_required_count: 0,
          speeding_flagged_count: 0,
          straightlining_flagged_count: 0,
          gibberish_flagged_count: 0,
          confirmed_duplicates_count: 0,
          potential_duplicates_count: 0,
          routing_violations_count: 0,
          strict_accounting_verified: true
        });
      }
    } catch (err) {
      console.error('Failed to load latest dashboard:', err);
    }
  },

  populateKpis(data) {
    const total = data.total_respondents || 0;
    const accepted = data.accepted_count || 0;
    const rejected = data.rejected_count || 0;
    const review = data.review_required_count || 0;

    const acceptedPct = total > 0 ? ((accepted / total) * 100).toFixed(1) : '0';
    const rejectedPct = total > 0 ? ((rejected / total) * 100).toFixed(1) : '0';
    const reviewPct = total > 0 ? ((review / total) * 100).toFixed(1) : '0';

    this.setText('surveyKpiTotal', total);
    this.setText('surveyKpiAccepted', accepted);
    this.setText('surveyKpiAcceptedPct', `${acceptedPct}% of total`);
    this.setText('surveyKpiRejected', rejected);
    this.setText('surveyKpiRejectedPct', `${rejectedPct}% of total`);
    this.setText('surveyKpiReview', review);
    this.setText('surveyKpiReviewPct', `${reviewPct}% of total`);

    this.setText('surveyKpiSpeeding', data.speeding_flagged_count || 0);
    this.setText('surveyKpiStraightline', data.straightlining_flagged_count || 0);
    this.setText('surveyKpiGibberish', data.gibberish_flagged_count || 0);
    this.setText('surveyKpiConfirmedDup', data.confirmed_duplicates_count || 0);
    this.setText('surveyKpiPotentialDup', data.potential_duplicates_count || 0);
    this.setText('surveyKpiRouting', data.routing_violations_count || 0);

    // Invariant check
    const isReconciled = total === (accepted + rejected + review);
    const badge = document.getElementById('surveyInvariantBadge');
    const formula = document.getElementById('surveyAccountingFormula');
    const timeEl = document.getElementById('surveyAuditTimestamp');

    if (badge) {
      if (isReconciled) {
        badge.className = 'badge-success text-xs font-bold';
        badge.innerHTML = '&check; Strict Accounting Verified';
      } else {
        badge.className = 'badge-danger text-xs font-bold';
        badge.innerHTML = '&#9888; Accounting Discrepancy';
      }
    }

    if (formula) {
      formula.textContent = `Total (${total}) = Accepted (${accepted}) + Rejected (${rejected}) + Review Required (${review}) \u2022 100% Accounted`;
    }

    if (timeEl && data.executed_at) {
      timeEl.textContent = `Audit Run: ${new Date(data.executed_at).toLocaleString()}`;
    }
  },

  renderCharts(data) {
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';

    // 1. Disposition Chart (Doughnut)
    const dispCtx = document.getElementById('surveyDispositionChart');
    if (dispCtx && window.Chart) {
      if (this.charts.disposition) this.charts.disposition.destroy();
      const hasData = (data.total_respondents || 0) > 0;

      this.charts.disposition = new Chart(dispCtx, {
        type: 'doughnut',
        data: {
          labels: hasData ? ['Accepted', 'Review Required', 'Rejected'] : ['No Data'],
          datasets: [{
            data: hasData ? [data.accepted_count, data.review_required_count, data.rejected_count] : [1],
            backgroundColor: hasData ? ['#2E8B57', '#F39C12', '#D9534F'] : ['#E8DCC9'],
            borderWidth: 2,
            borderColor: isDark ? '#24201A' : '#FFFFFF'
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: {
              position: 'bottom',
              labels: {
                color: isDark ? '#E8DCC9' : '#3D2B1F',
                font: { family: 'Inter', size: 11 }
              }
            }
          }
        }
      });
    }

    // 2. Findings by Category (Bar Chart)
    const findCtx = document.getElementById('surveyFindingsCategoryChart');
    if (findCtx && window.Chart) {
      if (this.charts.findings) this.charts.findings.destroy();

      const labels = ['Speeding', 'Straightlining', 'Open-Text', 'Duplicates', 'Routing/Screen'];
      const values = [
        data.speeding_flagged_count || 0,
        data.straightlining_flagged_count || 0,
        data.gibberish_flagged_count || 0,
        (data.confirmed_duplicates_count || 0) + (data.potential_duplicates_count || 0),
        data.routing_violations_count || 0
      ];

      this.charts.findings = new Chart(findCtx, {
        type: 'bar',
        data: {
          labels: labels,
          datasets: [{
            label: 'Flags Count',
            data: values,
            backgroundColor: ['#E67E22', '#9B59B6', '#E74C3C', '#3498DB', '#1ABC9C'],
            borderRadius: 4
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false }
          },
          scales: {
            y: {
              beginAtZero: true,
              ticks: { color: isDark ? '#A69888' : '#85786A', precision: 0 }
            },
            x: {
              ticks: { color: isDark ? '#A69888' : '#85786A' }
            }
          }
        }
      });
    }
  },

  renderCoverageTable(coverageList) {
    const tbody = document.getElementById('surveyCoverageTableBody');
    if (!tbody) return;

    if (!coverageList || coverageList.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="text-center py-3 text-muted">No QC coverage data available.</td></tr>';
      return;
    }

    tbody.innerHTML = coverageList.map(c => {
      const statusClass = c.status === 'PASSED' ? 'badge-success' :
                          c.status === 'FLAGGED' ? 'badge-danger' :
                          c.status === 'REVIEW_NEEDED' ? 'badge-warning' : 'badge-neutral';

      return `
        <tr>
          <td><strong>${this.escapeHtml(c.category || '')}</strong></td>
          <td><span class="${statusClass} text-xs font-bold">${c.status}</span></td>
          <td>${c.flagged_count > 0 ? `<strong class="text-danger">${c.flagged_count}</strong>` : '0'}</td>
          <td><div class="text-xs">${this.escapeHtml(c.description || '')}</div></td>
          <td><div class="text-xs text-muted">${this.escapeHtml(c.limitations || 'No limitations noted.')}</div></td>
        </tr>
      `;
    }).join('');
  },

  renderRespondentsTable() {
    const tbody = document.getElementById('surveyRespondentsTableBody');
    const searchVal = (document.getElementById('surveyRespondentSearch')?.value || '').toLowerCase().trim();

    const allCount = this.respondents.length;
    const accCount = this.respondents.filter(r => r.disposition === 'ACCEPTED').length;
    const revCount = this.respondents.filter(r => r.disposition === 'REVIEW_REQUIRED').length;
    const rejCount = this.respondents.filter(r => r.disposition === 'REJECTED').length;

    this.setText('surveyTabCountAll', allCount);
    this.setText('surveyTabCountAccepted', accCount);
    this.setText('surveyTabCountReview', revCount);
    this.setText('surveyTabCountRejected', rejCount);

    if (!tbody) return;

    let filtered = this.respondents;
    if (this.currentTab !== 'ALL') {
      filtered = filtered.filter(r => r.disposition === this.currentTab);
    }

    if (searchVal) {
      filtered = filtered.filter(r =>
        (r.respondent_key && r.respondent_key.toLowerCase().includes(searchVal)) ||
        (r.ip_address && r.ip_address.toLowerCase().includes(searchVal)) ||
        (r.disposition && r.disposition.toLowerCase().includes(searchVal))
      );
    }

    if (filtered.length === 0) {
      tbody.innerHTML = '<tr><td colspan="10" class="text-center py-3 text-muted">No respondents matching filter.</td></tr>';
      return;
    }

    tbody.innerHTML = filtered.map(r => {
      const dispClass = r.disposition === 'ACCEPTED' ? 'badge-disposition-accepted' :
                        r.disposition === 'REJECTED' ? 'badge-disposition-rejected' : 'badge-disposition-review';

      const durText = r.duration_seconds !== null && r.duration_seconds !== undefined
        ? `${Math.floor(r.duration_seconds / 60)}m ${Math.floor(r.duration_seconds % 60)}s`
        : 'N/A';

      const overnightBadge = r.is_overnight ? '<span class="badge-neutral text-xs" title="Submitted between 00:00 - 05:59">&#9790; Yes</span>' : '<span class="text-muted text-xs">No</span>';
      const speedBadge = r.is_speeding ? '<span class="badge-danger text-xs font-bold">Speeding</span>' : '<span class="text-muted text-xs">&check;</span>';
      const straightBadge = r.is_straightlining ? '<span class="badge-danger text-xs font-bold">Flatlined</span>' : '<span class="text-muted text-xs">&check;</span>';
      const gibberishBadge = r.is_gibberish ? '<span class="badge-warning text-xs font-bold">Suspicious</span>' : '<span class="text-muted text-xs">&check;</span>';
      const dupBadge = r.is_duplicate ? '<span class="badge-danger text-xs font-bold">Duplicate</span>' : '<span class="text-muted text-xs">&check;</span>';

      return `
        <tr>
          <td><strong>${this.escapeHtml(r.respondent_key)}</strong></td>
          <td><span class="${dispClass}">${r.disposition}</span></td>
          <td>${durText}</td>
          <td>${overnightBadge}</td>
          <td>${speedBadge}</td>
          <td>${straightBadge}</td>
          <td>${gibberishBadge}</td>
          <td>${dupBadge}</td>
          <td><strong>${r.findings_count || 0}</strong></td>
          <td>
            <button class="btn btn-outline btn-xs" onclick="Surveys.openReviewModal('${this.escapeHtml(r.respondent_key)}')">
              Review
            </button>
          </td>
        </tr>
      `;
    }).join('');
  },

  renderFindingsTable() {
    const tbody = document.getElementById('surveyFindingsTableBody');
    if (!tbody) return;

    let filtered = this.findings;
    if (this.findingFilter !== 'ALL') {
      filtered = filtered.filter(f => f.severity === this.findingFilter);
    }

    if (filtered.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" class="text-center py-3 text-muted">No findings matching severity filter.</td></tr>';
      return;
    }

    tbody.innerHTML = filtered.map(f => {
      const sevClass = f.severity === 'CRITICAL' ? 'badge-danger' :
                       f.severity === 'WARNING' ? 'badge-warning' : 'badge-neutral';

      return `
        <tr>
          <td>#${f.id || ''}</td>
          <td><strong>${this.escapeHtml(f.respondent_key || '')}</strong></td>
          <td>${this.escapeHtml(f.check_type || '')}</td>
          <td><span class="${sevClass} text-xs font-bold">${f.severity}</span></td>
          <td><div class="text-xs" style="max-width: 300px;">${this.escapeHtml(f.description || '')}</div></td>
          <td><div class="text-xs text-muted" style="max-width: 140px; overflow: hidden; text-overflow: ellipsis;">${this.escapeHtml(f.observed_value || 'N/A')}</div></td>
          <td><div class="text-xs text-muted">${this.escapeHtml(f.threshold_reference || 'N/A')}</div></td>
        </tr>
      `;
    }).join('');
  },

  async loadHistory() {
    const tbody = document.getElementById('surveyAuditRunsTableBody');
    if (!tbody) return;

    try {
      const resp = await this.apiFetch('/api/surveys/audits');
      if (!resp.ok) return;
      const runs = await resp.json();

      if (!runs || runs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" class="text-center py-3 text-muted">No audit runs on record.</td></tr>';
        return;
      }

      tbody.innerHTML = runs.map(run => `
        <tr>
          <td><strong>#${run.id}</strong></td>
          <td>${new Date(run.executed_at).toLocaleString()}</td>
          <td>${this.escapeHtml(run.raw_filename || '')}</td>
          <td>${this.escapeHtml(run.qq_filename || 'None')}</td>
          <td><strong>${run.total_respondents}</strong></td>
          <td class="text-success font-bold">${run.accepted_count}</td>
          <td class="text-warning font-bold">${run.review_required_count}</td>
          <td class="text-danger font-bold">${run.rejected_count}</td>
          <td>
            <div class="flex-center gap-1">
              <button class="btn btn-outline btn-xs" onclick="Surveys.loadAuditDetails(${run.id})">View</button>
              <a href="/api/surveys/audits/${run.id}/export/excel" class="btn btn-solid-brown btn-xs">XLSX</a>
            </div>
          </td>
        </tr>
      `).join('');
    } catch (err) {
      console.error('Failed to load audit history:', err);
    }
  },

  openReviewModal(respondentKey) {
    const modal = document.getElementById('surveyReviewModal');
    const respKeyEl = document.getElementById('surveyReviewRespKey');
    const findingsList = document.getElementById('surveyReviewFindingsList');
    const decisionSel = document.getElementById('surveyReviewDecisionSelect');
    const reviewerNameInp = document.getElementById('surveyReviewerName');
    const notesInp = document.getElementById('surveyReviewNotes');

    const respondent = this.respondents.find(r => r.respondent_key === respondentKey);
    if (!respondent || !modal) return;

    this.activeReviewKey = respondentKey;
    if (respKeyEl) respKeyEl.textContent = respondentKey;
    if (decisionSel) decisionSel.value = respondent.disposition;
    if (reviewerNameInp) reviewerNameInp.value = respondent.reviewed_by || 'QC Auditor';
    if (notesInp) notesInp.value = respondent.review_notes || '';

    // Show findings for this respondent
    const respFindings = this.findings.filter(f => f.respondent_key === respondentKey);
    if (findingsList) {
      if (respFindings.length > 0) {
        findingsList.innerHTML = `
          <strong>Flagged Checks (${respFindings.length}):</strong>
          <ul class="mt-1 pl-3">
            ${respFindings.map(f => `<li><strong>[${f.severity}] ${f.check_type}:</strong> ${this.escapeHtml(f.description)}</li>`).join('')}
          </ul>
        `;
      } else {
        findingsList.innerHTML = '<span class="text-muted">No automated quality control flags recorded for this respondent.</span>';
      }
    }

    modal.classList.remove('d-none');
  },

  async submitReviewDecision() {
    if (!this.currentAuditId || !this.activeReviewKey) return;

    const decisionSel = document.getElementById('surveyReviewDecisionSelect');
    const reviewerNameInp = document.getElementById('surveyReviewerName');
    const notesInp = document.getElementById('surveyReviewNotes');
    const modal = document.getElementById('surveyReviewModal');

    const decision = decisionSel?.value;
    const reviewer_name = reviewerNameInp?.value?.trim();
    const notes = notesInp?.value?.trim();

    if (!reviewer_name) {
      this.showToast('Please provide a reviewer name/ID.', 'warning');
      return;
    }
    if (!notes) {
      this.showToast('Please provide audit notes justifying this decision.', 'warning');
      return;
    }

    try {
      const resp = await this.apiFetch(`/api/surveys/audits/${this.currentAuditId}/review/${encodeURIComponent(this.activeReviewKey)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          decision: decision,
          reviewer_name: reviewer_name,
          notes: notes
        })
      });

      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || 'Review update failed.');
      }

      const updated = await resp.json();
      if (modal) modal.classList.add('d-none');
      this.showToast(`Updated respondent ${this.activeReviewKey} to ${decision}. Invariant reconciled.`, 'success');

      // Reload audit details to update KPIs and counts
      await this.loadAuditDetails(this.currentAuditId);
    } catch (err) {
      this.showToast('Error saving review decision: ' + err.message, 'error');
    }
  },

  setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  },

  escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  },

  showToast(message, type = 'info') {
    if (window.App && typeof window.App.showToast === 'function') {
      window.App.showToast(message, type);
    } else {
      console.log(`[Toast ${type}] ${message}`);
    }
  }
};

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
  Surveys.init();
});
