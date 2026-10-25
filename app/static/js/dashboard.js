/**
 * EDVEXA Data Quality & ETL Monitor
 * Dashboard Metrics, Chart.js Visuals, Ingestion Workflow & Records Browser
 */

const Dashboard = {
  trendsChart: null,
  distributionChart: null,

  // Pagination states
  jobsPage: 1,
  jobsPageSize: 10,
  errorsPage: 1,
  errorsPageSize: 10,
  recordsPage: 1,
  recordsPageSize: 10,
  currentRecordsDataset: 'customers',
  startDate: null,
  endDate: null,
  datePreset: '30d',
  calendarCurrentDate: new Date(),
  lastSummary: null,

  init() {
    this.bindUploadWorkflow();
    this.bindJobsFilters();
    this.bindErrorsFilters();
    this.bindRecordsTabs();
    this.bindRefreshBtn();
    this.bindThemeListener();
    this.bindDateRangeFilter();
    this.bindKpiCards();

    // Initial overview load
    this.fetchSummary();
    this.fetchTrends();
  },

  bindKpiCards() {
    const cardTotal = document.getElementById('kpiCardTotalRecords');
    const cardAcc = document.getElementById('kpiCardAccepted');
    const cardRej = document.getElementById('kpiCardRejected');
    const cardDup = document.getElementById('kpiCardDuplicates');
    const cardJobs = document.getElementById('kpiCardJobs');
    const cardScore = document.getElementById('kpiCardScore');

    const wireClick = (el, action) => {
      if (!el) return;
      el.addEventListener('click', action);
      el.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          action();
        }
      });
    };

    wireClick(cardTotal, () => App.navigateTo('jobs'));
    wireClick(cardAcc, () => App.navigateTo('records'));
    wireClick(cardRej, () => App.navigateTo('errors'));
    wireClick(cardDup, () => App.navigateTo('jobs'));
    wireClick(cardJobs, () => App.navigateTo('jobs'));
    wireClick(cardScore, () => App.openQualityScoreModal());
  },

  bindDateRangeFilter() {
    const filterBtn = document.getElementById('dateFilterBtn');
    const popover = document.getElementById('dateRangePopover');
    const closeBtn = document.getElementById('closeDatePopoverBtn');
    const cancelBtn = document.getElementById('cancelDateFilterBtn');
    const applyBtn = document.getElementById('applyDateFilterBtn');
    const resetBtn = document.getElementById('resetDateFilterBtn');
    const label = document.getElementById('currentDateRangeLabel');
    const startInput = document.getElementById('filterStartDate');
    const endInput = document.getElementById('filterEndDate');
    const prevMonthBtn = document.getElementById('calPrevMonth');
    const nextMonthBtn = document.getElementById('calNextMonth');

    if (!filterBtn || !popover) return;

    // Toggle popover
    filterBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const isClosed = popover.classList.contains('d-none');
      if (isClosed) {
        popover.classList.remove('d-none');
        filterBtn.setAttribute('aria-expanded', 'true');
        this.renderCalendar();
      } else {
        popover.classList.add('d-none');
        filterBtn.setAttribute('aria-expanded', 'false');
      }
    });

    const closePopover = () => {
      popover.classList.add('d-none');
      filterBtn.setAttribute('aria-expanded', 'false');
    };

    if (closeBtn) closeBtn.addEventListener('click', closePopover);
    if (cancelBtn) cancelBtn.addEventListener('click', closePopover);

    // Click outside popover to close
    document.addEventListener('click', (e) => {
      if (!popover.classList.contains('d-none') && !popover.contains(e.target) && e.target !== filterBtn && !filterBtn.contains(e.target)) {
        closePopover();
      }
    });

    // Preset Buttons
    const presetPills = document.querySelectorAll('.date-preset-pill');
    presetPills.forEach(pill => {
      pill.addEventListener('click', () => {
        const preset = pill.getAttribute('data-preset');
        this.setPreset(preset);
      });
    });

    // Date Inputs change
    if (startInput) {
      startInput.addEventListener('change', () => {
        this.renderCalendar();
      });
    }
    if (endInput) {
      endInput.addEventListener('change', () => {
        this.renderCalendar();
      });
    }

    // Calendar Month Navigation
    if (prevMonthBtn) {
      prevMonthBtn.addEventListener('click', () => {
        this.calendarCurrentDate.setMonth(this.calendarCurrentDate.getMonth() - 1);
        this.renderCalendar();
      });
    }
    if (nextMonthBtn) {
      nextMonthBtn.addEventListener('click', () => {
        this.calendarCurrentDate.setMonth(this.calendarCurrentDate.getMonth() + 1);
        this.renderCalendar();
      });
    }

    // Apply Filter Button
    if (applyBtn) {
      applyBtn.addEventListener('click', () => {
        this.startDate = startInput.value || null;
        this.endDate = endInput.value || null;

        if (this.datePreset === '30d') {
          label.textContent = 'Last 30 days';
        } else if (this.datePreset === '7d') {
          label.textContent = 'Last 7 days';
        } else if (this.datePreset === 'today') {
          label.textContent = 'Today';
        } else if (this.datePreset === '90d') {
          label.textContent = 'Last 90 days';
        } else if (this.datePreset === 'all' || (!this.startDate && !this.endDate)) {
          label.textContent = 'All Time';
        } else {
          label.textContent = `${this.startDate} to ${this.endDate}`;
        }

        closePopover();
        this.fetchSummary();
        this.fetchTrends();
        if (App.currentView === 'jobs') {
          this.jobsPage = 1;
          this.fetchJobs();
        }
        App.showToast(`Applied date filter: ${label.textContent}`, 'info');
      });
    }

    // Reset Filter Button
    if (resetBtn) {
      resetBtn.addEventListener('click', () => {
        this.resetDateFilter();
        this.fetchSummary();
        this.fetchTrends();
        if (App.currentView === 'jobs') {
          this.jobsPage = 1;
          this.fetchJobs();
        }
        App.showToast('Reset date filter to Last 30 days', 'info');
      });
    }

    // Initialize with 30d preset values
    this.setPreset('30d', false);
  },

  setPreset(preset, updateCalendar = true) {
    this.datePreset = preset;
    document.querySelectorAll('.date-preset-pill').forEach(pill => {
      pill.classList.toggle('active', pill.getAttribute('data-preset') === preset);
    });

    const startInput = document.getElementById('filterStartDate');
    const endInput = document.getElementById('filterEndDate');
    if (!startInput || !endInput) return;

    const today = new Date();
    const formatYMD = (d) => {
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, '0');
      const day = String(d.getDate()).padStart(2, '0');
      return `${y}-${m}-${day}`;
    };

    if (preset === 'today') {
      startInput.value = formatYMD(today);
      endInput.value = formatYMD(today);
    } else if (preset === '7d') {
      const past = new Date(today);
      past.setDate(today.getDate() - 7);
      startInput.value = formatYMD(past);
      endInput.value = formatYMD(today);
    } else if (preset === '30d') {
      const past = new Date(today);
      past.setDate(today.getDate() - 30);
      startInput.value = formatYMD(past);
      endInput.value = formatYMD(today);
    } else if (preset === '90d') {
      const past = new Date(today);
      past.setDate(today.getDate() - 90);
      startInput.value = formatYMD(past);
      endInput.value = formatYMD(today);
    } else if (preset === 'all') {
      startInput.value = '';
      endInput.value = '';
    }

    if (updateCalendar) {
      this.renderCalendar();
    }
  },

  resetDateFilter() {
    this.startDate = null;
    this.endDate = null;
    const label = document.getElementById('currentDateRangeLabel');
    if (label) label.textContent = 'Last 30 days';
    const popover = document.getElementById('dateRangePopover');
    if (popover) popover.classList.add('d-none');
    const filterBtn = document.getElementById('dateFilterBtn');
    if (filterBtn) filterBtn.setAttribute('aria-expanded', 'false');
    this.setPreset('30d');
  },

  renderCalendar() {
    const label = document.getElementById('calMonthYearLabel');
    const grid = document.getElementById('calendarDaysGrid');
    const startInput = document.getElementById('filterStartDate');
    const endInput = document.getElementById('filterEndDate');
    if (!label || !grid) return;

    const year = this.calendarCurrentDate.getFullYear();
    const month = this.calendarCurrentDate.getMonth();
    label.textContent = this.calendarCurrentDate.toLocaleDateString('en-US', { month: 'long', year: 'numeric' });

    const firstDayIndex = new Date(year, month, 1).getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();

    const startVal = startInput ? startInput.value : '';
    const endVal = endInput ? endInput.value : '';

    let html = '';
    for (let i = 0; i < firstDayIndex; i++) {
      html += `<div class="cal-day-cell empty"></div>`;
    }

    for (let day = 1; day <= daysInMonth; day++) {
      const dayStr = String(day).padStart(2, '0');
      const mStr = String(month + 1).padStart(2, '0');
      const dateStr = `${year}-${mStr}-${dayStr}`;

      let classes = 'cal-day-cell';
      if (dateStr === startVal || dateStr === endVal) {
        classes += ' selected';
      } else if (startVal && endVal && dateStr > startVal && dateStr < endVal) {
        classes += ' in-range';
      }

      html += `<div class="${classes}" data-date="${dateStr}">${day}</div>`;
    }

    grid.innerHTML = html;

    // Wire day click
    grid.querySelectorAll('.cal-day-cell:not(.empty)').forEach(cell => {
      cell.addEventListener('click', () => {
        const clickedDate = cell.getAttribute('data-date');
        this.datePreset = 'custom';
        document.querySelectorAll('.date-preset-pill').forEach(pill => pill.classList.remove('active'));

        if (!startInput.value || (startInput.value && endInput.value)) {
          startInput.value = clickedDate;
          endInput.value = '';
        } else {
          if (clickedDate < startInput.value) {
            endInput.value = startInput.value;
            startInput.value = clickedDate;
          } else {
            endInput.value = clickedDate;
          }
        }
        this.renderCalendar();
      });
    });
  },

  bindThemeListener() {
    window.addEventListener('themeChanged', (e) => {
      this.updateChartsTheme(e.detail.theme);
    });
  },

  updateChartsTheme(theme) {
    const isDark = (theme === 'dark');
    if (this.trendsChart) {
      if (this.trendsChart.options.scales && this.trendsChart.options.scales.x) {
        this.trendsChart.options.scales.x.ticks.color = isDark ? '#A69888' : '#85786A';
      }
      if (this.trendsChart.options.scales && this.trendsChart.options.scales.y) {
        this.trendsChart.options.scales.y.ticks.color = isDark ? '#A69888' : '#85786A';
        this.trendsChart.options.scales.y.grid.color = isDark ? '#2E2820' : '#F0E8DC';
      }
      if (this.trendsChart.options.plugins && this.trendsChart.options.plugins.tooltip) {
        this.trendsChart.options.plugins.tooltip.backgroundColor = isDark ? '#171411' : '#261A10';
      }
      this.trendsChart.update();
    }

    if (this.distributionChart) {
      if (this.distributionChart.data && this.distributionChart.data.datasets && this.distributionChart.data.datasets[0]) {
        this.distributionChart.data.datasets[0].borderColor = isDark ? '#24201A' : '#FFFFFF';
      }
      if (this.distributionChart.options.plugins && this.distributionChart.options.plugins.tooltip) {
        this.distributionChart.options.plugins.tooltip.backgroundColor = isDark ? '#171411' : '#261A10';
      }
      this.distributionChart.update();
    }
  },

  bindRefreshBtn() {
    const btn = document.getElementById('refreshDataBtn');
    if (btn) {
      btn.addEventListener('click', () => {
        App.showToast('Refreshing live telemetry...', 'info');
        if (App.currentView === 'overview') {
          this.fetchSummary();
          this.fetchTrends();
        } else if (App.currentView === 'jobs') {
          this.fetchJobs();
        } else if (App.currentView === 'errors') {
          this.fetchErrors();
        } else if (App.currentView === 'records') {
          this.fetchRecords();
        }
      });
    }
  },

  async fetchWithRetry(url, options = {}, retries = 3, backoffMs = 1000) {
    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        const res = await fetch(url, options);
        if (res.ok) return res;
        if ((res.status >= 500 || res.status === 429) && attempt < retries) {
          await new Promise(r => setTimeout(r, backoffMs * Math.pow(1.8, attempt)));
          continue;
        }
        return res;
      } catch (err) {
        if (attempt >= retries) throw err;
        await new Promise(r => setTimeout(r, backoffMs * Math.pow(1.8, attempt)));
      }
    }
  },

  /* =========================================================================
     1. OVERVIEW: KPIS & CHARTS
     ========================================================================= */

  async fetchSummary() {
    try {
      const params = new URLSearchParams();
      if (this.startDate) params.append('start_date', this.startDate);
      if (this.endDate) params.append('end_date', this.endDate);
      const qs = params.toString() ? `?${params.toString()}` : '';

      const res = await this.fetchWithRetry(`${window.API_BASE}/api/dashboard/summary${qs}`);
      if (!res.ok) throw new Error(`Failed to fetch dashboard summary: HTTP ${res.status}`);
      const data = await res.json();
      const kpis = data.kpis;
      this.lastSummary = kpis;

      // 1. KPI Cards Values
      const elTotal = document.getElementById('kpiTotalRecords');
      const elAcc = document.getElementById('kpiAcceptedRecords');
      const elRej = document.getElementById('kpiRejectedRecords');
      const elDup = document.getElementById('kpiDuplicateRecords');
      const elJobs = document.getElementById('kpiTotalJobs');
      const elScore = document.getElementById('kpiDataQualityScore');

      if (elTotal) elTotal.textContent = kpis.total_records_processed.toLocaleString();
      if (elAcc) elAcc.textContent = kpis.accepted_records.toLocaleString();
      if (elRej) elRej.textContent = kpis.rejected_records.toLocaleString();
      if (elDup) elDup.textContent = kpis.duplicate_records.toLocaleString();
      if (elJobs) elJobs.textContent = kpis.total_jobs.toLocaleString();

      const hasRecords = kpis.total_records_processed > 0;
      if (elScore) {
        elScore.textContent = hasRecords ? `${kpis.data_quality_rate}%` : '0%';
      }

      // KPI Subtexts / Badges
      const elJobsBadge = document.getElementById('kpiTotalJobsBadge');
      if (elJobsBadge) {
        elJobsBadge.textContent = kpis.total_jobs > 0 ? 'All Completed' : '0 Jobs';
      }

      const elQualBadge = document.getElementById('kpiQualityRateBadge');
      if (elQualBadge) {
        elQualBadge.textContent = hasRecords ? `${kpis.data_quality_rate}% Quality Pass Rate` : 'No data';
      }

      const rejRate = hasRecords
        ? ((kpis.rejected_records / kpis.total_records_processed) * 100).toFixed(1)
        : '0.0';
      const elRejBadge = document.getElementById('kpiRejectedRateBadge');
      if (elRejBadge) elRejBadge.textContent = `${rejRate}% Rejection Rate`;

      // 2. Update Dataset Breakdown Card
      if (data.dataset_metrics && data.dataset_metrics.length > 0) {
        const maxJobs = Math.max(...data.dataset_metrics.map(d => d.job_count || 0), 0);
        data.dataset_metrics.forEach(dm => {
          const type = dm.dataset_type.toLowerCase();
          const capType = type.charAt(0).toUpperCase() + type.slice(1);
          const countEl = document.getElementById(`datasetCount${capType}`);
          const barEl = document.getElementById(`datasetBar${capType}`);
          const jobCount = dm.job_count || 0;
          if (countEl) countEl.textContent = `${jobCount} jobs`;
          if (barEl) {
            const pct = (maxJobs > 0 && jobCount > 0) ? Math.max(Math.round((jobCount / maxJobs) * 100), 5) : 0;
            barEl.style.width = `${pct}%`;
          }
        });
      }

      // 3. Update Distribution Doughnut Chart & Side Legend
      this.renderDistributionChart(kpis);

      // 4. Update Recent Jobs Table in Overview
      const tbody = document.getElementById('recentJobsTableBody');
      if (tbody) {
        if (!data.recent_jobs || data.recent_jobs.length === 0) {
          const emptyMsg = (this.startDate || this.endDate)
            ? 'No processing jobs found for this date range.'
            : 'No jobs recorded yet. Ingest a CSV to start monitoring.';
          tbody.innerHTML = `<tr><td colspan="10" class="text-center py-4 text-muted">${emptyMsg}</td></tr>`;
        } else {
          tbody.innerHTML = data.recent_jobs.map(j => this.renderRecentJobRow(j)).join('');
        }
      }

    } catch (err) {
      console.error('Failed to load dashboard summary:', err);
      const tbody = document.getElementById('recentJobsTableBody');
      if (tbody) {
        tbody.innerHTML = `<tr><td colspan="10" class="text-center py-4 text-muted"><p class="text-danger mb-2 font-bold">Failed to load dashboard metrics from backend.</p><button onclick="Dashboard.fetchSummary(); Dashboard.fetchTrends();" class="btn btn-secondary btn-sm">Retry Loading Metrics</button></td></tr>`;
      }
      ['kpiTotalRecords', 'kpiAcceptedRecords', 'kpiRejectedRecords', 'kpiDuplicateRecords', 'kpiTotalJobs'].forEach(id => {
        const el = document.getElementById(id);
        if (el && el.textContent === '—') el.textContent = '0';
      });
      const elScore = document.getElementById('kpiDataQualityScore');
      if (elScore && elScore.textContent === '—%') elScore.textContent = '0.0%';
    }
  },

  async fetchTrends() {
    try {
      const params = new URLSearchParams();
      if (this.startDate) params.append('start_date', this.startDate);
      if (this.endDate) params.append('end_date', this.endDate);
      const qs = params.toString() ? `?${params.toString()}` : '';

      const res = await this.fetchWithRetry(`${window.API_BASE}/api/dashboard/trends${qs}`);
      if (!res.ok) return;
      const data = await res.json();
      this.renderTrendsChart(data.trends || []);
    } catch (err) {
      console.error('Failed to load trends:', err);
    }
  },

  renderTrendsChart(trends) {
    const ctx = document.getElementById('qualityTrendsChart');
    if (!ctx) return;

    if (this.trendsChart) {
      this.trendsChart.destroy();
    }

    if (!trends || trends.length === 0) {
      this.trendsChart = new Chart(ctx, {
        type: 'bar',
        data: {
          labels: ['No Data'],
          datasets: [{ label: 'Accepted', data: [0], backgroundColor: '#EFE7DA' }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } }
        }
      });
      return;
    }

    // Format labels nicely
    const labels = trends.map(t => {
      if (t.timestamp) {
        const parts = t.timestamp.split(' ');
        if (parts.length >= 2) return `${parts[0]} ${parts[1]}`;
      }
      return `#${t.job_id}`;
    });

    const acceptedData = trends.map(t => t.accepted_rows);
    const rejectedData = trends.map(t => t.rejected_rows);
    const duplicateData = trends.map(t => t.duplicate_rows);

    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';

    this.trendsChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [
          {
            label: 'Accepted',
            data: acceptedData,
            backgroundColor: '#2E8B57', // Forest green matching reference
            borderRadius: 3,
            borderSkipped: false,
            barPercentage: 0.65,
          },
          {
            label: 'Rejected',
            data: rejectedData,
            backgroundColor: '#D9534F', // Coral red matching reference
            borderRadius: 3,
            borderSkipped: false,
            barPercentage: 0.65,
          },
          {
            label: 'Duplicates',
            data: duplicateData,
            backgroundColor: '#F1B24A', // Warm golden amber matching reference
            borderRadius: 3,
            borderSkipped: false,
            barPercentage: 0.65,
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            stacked: true,
            grid: { display: false },
            ticks: {
              color: isDark ? '#A69888' : '#85786A',
              font: { family: 'Inter', size: 10 },
              maxRotation: 0,
              autoSkip: true,
              maxTicksLimit: 12
            }
          },
          y: {
            stacked: true,
            beginAtZero: true,
            grid: { color: isDark ? '#2E2820' : '#F0E8DC' },
            ticks: {
              color: isDark ? '#A69888' : '#85786A',
              font: { family: 'Inter', size: 10 },
              stepSize: 5
            }
          }
        },
        plugins: {
          legend: {
            display: false // Using our top custom chips legend!
          },
          tooltip: {
            backgroundColor: isDark ? '#171411' : '#261A10',
            titleFont: { family: 'Inter', size: 11, weight: 'bold' },
            bodyFont: { family: 'Inter', size: 11 },
            padding: 10,
            cornerRadius: 6,
            callbacks: {
              afterLabel(context) {
                const item = trends[context.dataIndex];
                return `Quality Pass: ${item.quality_score}% | File: ${item.filename}`;
              }
            }
          }
        }
      }
    });
  },

  renderDistributionChart(kpis) {
    const ctx = document.getElementById('qualityDistributionChart');
    if (!ctx) return;

    if (this.distributionChart) {
      this.distributionChart.destroy();
    }

    const total = kpis.total_records_processed;
    const hasData = total > 0;

    // Center text
    const donutTotal = document.getElementById('donutTotalCount');
    if (donutTotal) {
      donutTotal.textContent = total.toLocaleString();
    }

    // Side legend text with exact percentage calculations
    const accPct = hasData ? ((kpis.accepted_records / total) * 100).toFixed(1) : '0.0';
    const rejPct = hasData ? ((kpis.rejected_records / total) * 100).toFixed(1) : '0.0';
    const dupPct = hasData ? ((kpis.duplicate_records / total) * 100).toFixed(1) : '0.0';

    const elAcc = document.getElementById('donutAcceptedText');
    const elRej = document.getElementById('donutRejectedText');
    const elDup = document.getElementById('donutDuplicatesText');

    if (elAcc) elAcc.textContent = `${kpis.accepted_records} (${accPct}%)`;
    if (elRej) elRej.textContent = `${kpis.rejected_records} (${rejPct}%)`;
    if (elDup) elDup.textContent = `${kpis.duplicate_records} (${dupPct}%)`;

    const dataValues = hasData
      ? [kpis.accepted_records, kpis.rejected_records, kpis.duplicate_records]
      : [1, 0, 0];

    const bgColors = hasData
      ? ['#2E8B57', '#D9534F', '#E67E22']
      : ['#EFE7DA', '#EFE7DA', '#EFE7DA'];

    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';

    this.distributionChart = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: hasData ? ['Accepted', 'Rejected', 'Duplicates'] : ['No Data'],
        datasets: [{
          data: dataValues,
          backgroundColor: bgColors,
          borderWidth: 2,
          borderColor: isDark ? '#24201A' : '#FFFFFF',
          hoverOffset: 3
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '75%',
        plugins: {
          legend: {
            display: false // Using dedicated side legend
          },
          tooltip: {
            backgroundColor: isDark ? '#171411' : '#261A10',
            titleFont: { family: 'Inter', size: 11 },
            bodyFont: { family: 'Inter', size: 11 },
            padding: 8,
            cornerRadius: 6,
          }
        }
      }
    });
  },

  renderRecentJobRow(j) {
    const capDataset = j.dataset_type.charAt(0).toUpperCase() + j.dataset_type.slice(1);
    const dateStr = this.formatDateDisplay(j.started_at);

    return `
      <tr>
        <td><span class="job-id-pill">#${j.id}</span></td>
        <td><strong>${capDataset}</strong></td>
        <td><code>${j.filename}</code></td>
        <td><strong>${j.total_rows}</strong></td>
        <td><span class="text-success font-bold">${j.accepted_rows}</span></td>
        <td><span class="text-danger font-bold">${j.rejected_rows}</span></td>
        <td><span class="text-warning font-bold">${j.duplicate_rows}</span></td>
        <td><span class="status-pill-success">${j.status === 'completed' ? 'Completed' : j.status}</span></td>
        <td class="text-muted text-sm">${dateStr}</td>
        <td class="text-center">
          <div class="action-icons-group">
            <!-- View Details (Eye) -->
            <button class="action-circle-btn" onclick="App.openJobModal(${j.id})" title="View Job #${j.id} Details">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>
              </svg>
            </button>
            <!-- Download Excel Report -->
            <button class="action-circle-btn" onclick="App.downloadJobArtifact(${j.id}, 'summary', this)" title="Download Excel Report (.xlsx)">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
              </svg>
            </button>
            <!-- More Options -->
            <button class="action-circle-btn" onclick="App.openJobModal(${j.id})" title="More Actions">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>
              </svg>
            </button>
          </div>
        </td>
      </tr>
    `;
  },

  renderJobRow(j) {
    const statusClass = j.status === 'completed' ? 'badge-success' : (j.status === 'processing' ? 'badge-info' : 'badge-danger');
    const dateStr = new Date(j.started_at).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' });
    const duration = j.processing_duration !== null ? `${j.processing_duration.toFixed(2)}s` : '-';

    return `
      <tr>
        <td><span class="job-id-pill">#${j.id}</span></td>
        <td><span class="file-name" title="${j.filename}">${j.filename}</span></td>
        <td><code>${j.dataset_type}</code></td>
        <td><span class="badge ${statusClass}">${j.status}</span></td>
        <td><strong>${j.total_rows}</strong></td>
        <td class="text-success font-bold">${j.accepted_rows}</td>
        <td class="text-danger font-bold">${j.rejected_rows}</td>
        <td class="text-warning font-bold">${j.duplicate_rows}</td>
        <td>${duration}</td>
        <td class="text-muted text-sm">${dateStr}</td>
        <td class="text-center">
          <button class="btn btn-secondary btn-sm" onclick="App.openJobModal(${j.id})">
            Details
          </button>
        </td>
      </tr>
    `;
  },

  formatDateDisplay(dateStr) {
    if (!dateStr) return '-';
    try {
      const d = new Date(dateStr);
      return d.toLocaleString('en-US', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: true
      });
    } catch {
      return dateStr;
    }
  },

  /* =========================================================================
     2. IMPORT DATA: DRAG & DROP & UPLOAD
     ========================================================================= */

  bindUploadWorkflow() {
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');
    const fileInfoBar = document.getElementById('fileSelectedInfo');
    const fileNameEl = document.getElementById('selectedFileName');
    const fileSizeEl = document.getElementById('selectedFileSize');
    const removeBtn = document.getElementById('removeFileBtn');
    const submitBtn = document.getElementById('submitUploadBtn');
    const form = document.getElementById('uploadForm');
    const feedback = document.getElementById('uploadFeedback');

    const handleFile = (file) => {
      if (!file) return;
      if (!file.name.toLowerCase().endsWith('.csv')) {
        App.showToast('Invalid file. Only .csv files are supported.', 'danger');
        return;
      }
      fileNameEl.textContent = file.name;
      fileSizeEl.textContent = `${(file.size / 1024).toFixed(1)} KB`;
      fileInfoBar.classList.remove('d-none');
      submitBtn.disabled = false;
      feedback.classList.add('d-none');
    };

    if (fileInput) {
      fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
          handleFile(e.target.files[0]);
        }
      });
    }

    if (dropZone) {
      ['dragenter', 'dragover'].forEach(name => {
        dropZone.addEventListener(name, (e) => {
          e.preventDefault();
          dropZone.classList.add('dragover');
        });
      });

      ['dragleave', 'drop'].forEach(name => {
        dropZone.addEventListener(name, (e) => {
          e.preventDefault();
          dropZone.classList.remove('dragover');
        });
      });

      dropZone.addEventListener('drop', (e) => {
        if (e.dataTransfer.files.length > 0) {
          fileInput.files = e.dataTransfer.files;
          handleFile(e.dataTransfer.files[0]);
        }
      });
    }

    if (removeBtn) {
      removeBtn.addEventListener('click', () => {
        fileInput.value = '';
        fileInfoBar.classList.add('d-none');
        submitBtn.disabled = true;
      });
    }

    if (form) {
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const file = fileInput.files[0];
        if (!file) return;

        const datasetType = document.getElementById('datasetSelect').value;
        const formData = new FormData();
        formData.append('file', file);
        formData.append('dataset_type', datasetType);

        submitBtn.disabled = true;
        submitBtn.innerHTML = `<span>Executing ETL Pipeline...</span>`;
        feedback.classList.add('d-none');

        // Dispatch Upload Started notification
        if (window.App && typeof window.App.addNotification === 'function') {
          window.App.addNotification({
            type: 'info',
            title: 'Upload Started',
            message: `Beginning ETL pipeline processing for ${file.name} (${datasetType}).`
          });
        }

        try {
          const res = await fetch(`${window.API_BASE}/api/uploads`, {
            method: 'POST',
            body: formData,
          });

          let result = {};
          try {
            const rawText = await res.text();
            result = rawText ? JSON.parse(rawText) : {};
          } catch (_) {
            result = {};
          }

          if (res.ok) {
            App.showToast(result.message, result.status === 'completed' ? 'success' : 'warning');
            
            if (window.App && typeof window.App.addNotification === 'function') {
              let notifType = 'success';
              let notifTitle = 'Upload Completed Successfully';

              if (result.rejected_rows > 0) {
                notifType = 'warning';
                notifTitle = 'Upload Completed with Rejections';
              } else if (result.duplicate_rows > 0) {
                notifType = 'warning';
                notifTitle = 'Upload Completed with Duplicates';
              }

              window.App.addNotification({
                type: notifType,
                title: notifTitle,
                message: `${file.name} (${datasetType}): ${result.accepted_rows} clean accepted, ${result.rejected_rows} rejections, ${result.duplicate_rows} duplicates prevented.`
              });
            }
            
            const cleanDisabled = result.accepted_rows === 0;
            const errorsDisabled = (result.rejected_rows === 0 && result.duplicate_rows === 0);
            const cleanTooltip = cleanDisabled
              ? 'No clean records were accepted from this job (0 rows)'
              : `Download ${result.accepted_rows} clean records as CSV`;
            const errorsTooltip = errorsDisabled
              ? 'No validation errors were recorded for this job (0 errors)'
              : `Download validation errors as CSV`;

            feedback.className = `upload-feedback card p-3 ${result.status === 'completed' ? 'bg-success-subtle' : 'bg-danger-subtle'}`;
            feedback.classList.remove('d-none');
            feedback.innerHTML = `
              <div class="flex-between flex-wrap gap-2">
                <div>
                  <h4 class="font-bold">${result.status === 'completed' ? 'Ingestion Completed' : 'Ingestion Failed'}</h4>
                  <p class="text-sm mt-1">${result.message}</p>
                  <div class="d-flex gap-3 text-sm mt-2 flex-wrap">
                    <span>Total Rows: <strong>${result.total_rows}</strong></span>
                    <span class="text-success">Accepted: <strong>${result.accepted_rows}</strong></span>
                    <span class="text-danger">Rejected: <strong>${result.rejected_rows}</strong></span>
                    <span class="text-warning">Duplicates: <strong>${result.duplicate_rows}</strong></span>
                    <span>Duration: <strong>${result.processing_duration.toFixed(3)}s</strong></span>
                  </div>
                </div>
                <button class="btn btn-solid-brown btn-sm" onclick="App.openJobModal(${result.job_id})">
                  View Job #${result.job_id} Details &rarr;
                </button>
              </div>

              <!-- Download Actions Section -->
              <div class="mt-3 pt-3 border-top flex-between flex-wrap gap-2">
                <span class="text-xs text-muted font-bold d-flex align-items-center gap-1">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                  Export Job Results:
                </span>
                <div class="d-flex gap-2 flex-wrap">
                  <button class="btn btn-outline-success btn-sm ${cleanDisabled ? 'btn-dimmed' : ''}"
                          title="${cleanTooltip}"
                          onclick="${cleanDisabled ? `App.showToast('Job #${result.job_id} has 0 accepted clean records to export.', 'warning')` : `App.downloadJobArtifact(${result.job_id}, 'clean', this)`}">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                    <span>Clean Records (CSV)</span>
                  </button>
                  <button class="btn btn-outline-danger btn-sm ${errorsDisabled ? 'btn-dimmed' : ''}"
                          title="${errorsTooltip}"
                          onclick="${errorsDisabled ? `App.showToast('Job #${result.job_id} had 0 validation errors recorded.', 'info')` : `App.downloadJobArtifact(${result.job_id}, 'errors', this)`}">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                    <span>Validation Errors (CSV)</span>
                  </button>
                  <button class="btn btn-outline-primary btn-sm"
                          title="Download comprehensive Excel report (.xlsx)"
                          onclick="App.downloadJobArtifact(${result.job_id}, 'summary', this)">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                    <span>Download Excel Report (.xlsx)</span>
                  </button>
                </div>
              </div>
            `;

            // Reset upload form inputs
            fileInput.value = '';
            fileInfoBar.classList.add('d-none');
            submitBtn.disabled = true;

            // Trigger background refresh
            Dashboard.fetchSummary();
            Dashboard.fetchTrends();
          } else {
            const errDetail = result.detail || 'Upload failed.';
            App.showToast(errDetail, 'danger');

            if (window.App && typeof window.App.addNotification === 'function') {
              let notifTitle = 'Ingestion Rejected';
              if (res.status === 413) {
                notifTitle = 'File Size Limit Exceeded';
              } else if (res.status === 415) {
                notifTitle = 'Invalid File Format';
              } else if (res.status === 400) {
                notifTitle = 'CSV Schema / Structural Error';
              }

              window.App.addNotification({
                type: 'danger',
                title: notifTitle,
                message: `${file.name} (${datasetType}): ${errDetail}`
              });
            }

            feedback.className = 'upload-feedback card p-3 bg-danger-subtle';
            feedback.classList.remove('d-none');
            feedback.innerHTML = `
              <h4 class="font-bold text-danger">Upload Error</h4>
              <p class="text-sm mt-1">${errDetail}</p>
            `;
          }
        } catch (err) {
          App.showToast('Network error while uploading file.', 'danger');
          if (window.App && typeof window.App.addNotification === 'function') {
            window.App.addNotification({
              type: 'danger',
              title: 'Upload Network Error',
              message: `Could not connect to backend server while uploading ${file.name}.`
            });
          }
        } finally {
          submitBtn.disabled = fileInput.files.length === 0;
          submitBtn.innerHTML = `
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
            <span>Run Ingestion Pipeline</span>
          `;
        }
      });
    }
  },

  /* =========================================================================
     3. PROCESSING JOBS VIEW
     ========================================================================= */

  bindJobsFilters() {
    const applyBtn = document.getElementById('applyJobsFilterBtn');
    const clearBtn = document.getElementById('clearJobsFilterBtn');
    const searchInput = document.getElementById('jobsSearchInput');

    if (applyBtn) {
      applyBtn.addEventListener('click', () => {
        this.jobsPage = 1;
        this.fetchJobs();
      });
    }

    if (clearBtn) {
      clearBtn.addEventListener('click', () => {
        document.getElementById('jobsSearchInput').value = '';
        document.getElementById('jobsDatasetFilter').value = '';
        document.getElementById('jobsStatusFilter').value = '';
        this.jobsPage = 1;
        this.fetchJobs();
      });
    }

    if (searchInput) {
      searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          this.jobsPage = 1;
          this.fetchJobs();
        }
      });
    }
  },

  async fetchJobs() {
    const tbody = document.getElementById('allJobsTableBody');
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="11" class="text-center py-4 text-muted"><span class="spinner-sm mr-2"></span>Loading processing jobs...</td></tr>`;

    const search = document.getElementById('jobsSearchInput').value.trim();
    const dataset = document.getElementById('jobsDatasetFilter').value;
    const status = document.getElementById('jobsStatusFilter').value;

    const params = new URLSearchParams({
      page: this.jobsPage,
      page_size: this.jobsPageSize,
    });
    if (search) params.append('search', search);
    if (dataset) params.append('dataset_type', dataset);
    if (status) params.append('status', status);
    if (this.startDate) params.append('start_date', this.startDate);
    if (this.endDate) params.append('end_date', this.endDate);

    try {
      const res = await this.fetchWithRetry(`${window.API_BASE}/api/jobs?${params.toString()}`);
      if (!res.ok) throw new Error(`Failed to load jobs: HTTP ${res.status}`);
      const data = await res.json();

      if (data.items.length === 0) {
        const emptyMsg = (this.startDate || this.endDate)
          ? 'No processing jobs found for this date range.'
          : 'No processing jobs match the selected criteria.';
        tbody.innerHTML = `<tr><td colspan="11" class="text-center py-4 text-muted">${emptyMsg}</td></tr>`;
      } else {
        tbody.innerHTML = data.items.map(j => this.renderJobRow(j)).join('');
      }

      this.renderPagination(
        'jobsPaginationInfo',
        'jobsPaginationControls',
        data.total,
        data.page,
        data.page_size,
        (p) => {
          this.jobsPage = p;
          this.fetchJobs();
        }
      );
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="11" class="text-center py-4 text-muted"><p class="text-danger mb-2 font-bold">Failed to load processing jobs.</p><button onclick="Dashboard.fetchJobs()" class="btn btn-secondary btn-sm">Retry Loading Jobs</button></td></tr>`;
    }
  },

  /* =========================================================================
     4. VALIDATION ERRORS VIEW
     ========================================================================= */

  bindErrorsFilters() {
    const applyBtn = document.getElementById('applyErrorsFilterBtn');
    const clearBtn = document.getElementById('clearErrorsFilterBtn');

    if (applyBtn) {
      applyBtn.addEventListener('click', () => {
        this.errorsPage = 1;
        this.fetchErrors();
      });
    }

    if (clearBtn) {
      clearBtn.addEventListener('click', () => {
        document.getElementById('errorsJobIdFilter').value = '';
        document.getElementById('errorsFieldFilter').value = '';
        this.errorsPage = 1;
        this.fetchErrors();
      });
    }
  },

  async fetchErrors() {
    const tbody = document.getElementById('errorsTableBody');
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="8" class="text-center py-4 text-muted"><span class="spinner-sm mr-2"></span>Loading validation errors...</td></tr>`;

    const jobId = document.getElementById('errorsJobIdFilter').value.trim();
    const field = document.getElementById('errorsFieldFilter').value.trim();

    const params = new URLSearchParams({
      page: this.errorsPage,
      page_size: this.errorsPageSize,
    });
    if (jobId) params.append('job_id', jobId);
    if (field) params.append('field_name', field);
    if (this.startDate) params.append('start_date', this.startDate);
    if (this.endDate) params.append('end_date', this.endDate);

    try {
      const res = await this.fetchWithRetry(`${window.API_BASE}/api/errors?${params.toString()}`);
      if (!res.ok) throw new Error(`Failed to load validation errors: HTTP ${res.status}`);
      const data = await res.json();

      if (data.items.length === 0) {
        const emptyMsg = (this.startDate || this.endDate)
          ? 'No validation errors found for this date range.'
          : 'No validation errors found. All processed rows are clean!';
        tbody.innerHTML = `<tr><td colspan="8" class="text-center py-4 text-muted">${emptyMsg}</td></tr>`;
      } else {
        tbody.innerHTML = data.items.map(e => `
          <tr>
            <td>#${e.id}</td>
            <td><a href="#" onclick="App.openJobModal(${e.job_id}); return false;"><strong>Job #${e.job_id}</strong></a></td>
            <td><strong>Row ${e.row_number}</strong></td>
            <td><code>${e.field_name}</code></td>
            <td><span class="badge badge-danger">${e.error_code}</span></td>
            <td>${e.error_message}</td>
            <td><code>${e.original_value !== null ? e.original_value : 'NULL'}</code></td>
            <td class="text-muted text-sm">${new Date(e.created_at).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}</td>
          </tr>
        `).join('');
      }

      this.renderPagination(
        'errorsPaginationInfo',
        'errorsPaginationControls',
        data.total,
        data.page,
        data.page_size,
        (p) => {
          this.errorsPage = p;
          this.fetchErrors();
        }
      );
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="8" class="text-center py-4 text-muted"><p class="text-danger mb-2 font-bold">Failed to load validation errors.</p><button onclick="Dashboard.fetchErrors()" class="btn btn-secondary btn-sm">Retry Loading Errors</button></td></tr>`;
    }
  },

  /* =========================================================================
     5. CLEAN RECORDS BROWSER VIEW
     ========================================================================= */

  bindRecordsTabs() {
    const tabBtns = document.querySelectorAll('#recordsTabGroup .tab-btn');
    tabBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        tabBtns.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        this.currentRecordsDataset = btn.getAttribute('data-dataset');
        this.recordsPage = 1;
        this.fetchRecords();
      });
    });

    const searchBtn = document.getElementById('searchRecordsBtn');
    const searchInput = document.getElementById('recordsSearchInput');
    if (searchBtn) {
      searchBtn.addEventListener('click', () => {
        this.recordsPage = 1;
        this.fetchRecords();
      });
    }
    if (searchInput) {
      searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          this.recordsPage = 1;
          this.fetchRecords();
        }
      });
    }
  },

  async fetchRecords() {
    const thead = document.getElementById('recordsTableHead');
    const tbody = document.getElementById('recordsTableBody');
    if (!tbody || !thead) return;

    const dataset = this.currentRecordsDataset;
    const search = document.getElementById('recordsSearchInput').value.trim();

    // Setup headers
    if (dataset === 'customers') {
      thead.innerHTML = `
        <tr>
          <th>Customer ID</th>
          <th>Full Name</th>
          <th>Email Address</th>
          <th>Phone</th>
          <th>Created Date</th>
          <th>Ingested Timestamp</th>
        </tr>
      `;
    } else if (dataset === 'sales') {
      thead.innerHTML = `
        <tr>
          <th>Sale ID</th>
          <th>Customer ID</th>
          <th>Amount</th>
          <th>Currency</th>
          <th>Sale Date</th>
        </tr>
      `;
    } else if (dataset === 'inventory') {
      thead.innerHTML = `
        <tr>
          <th>Product ID</th>
          <th>Product Title</th>
          <th>Stock Quantity</th>
          <th>Unit Price</th>
          <th>Last Updated</th>
        </tr>
      `;
    }

    tbody.innerHTML = `<tr><td colspan="6" class="text-center py-4 text-muted"><span class="spinner-sm mr-2"></span>Loading ${dataset} records...</td></tr>`;

    const params = new URLSearchParams({
      dataset_type: dataset,
      page: this.recordsPage,
      page_size: this.recordsPageSize,
    });
    if (search) params.append('q', search);
    if (this.startDate) params.append('start_date', this.startDate);
    if (this.endDate) params.append('end_date', this.endDate);

    try {
      const res = await this.fetchWithRetry(`${window.API_BASE}/api/records?${params.toString()}`);
      if (!res.ok) throw new Error(`Failed to load clean records: HTTP ${res.status}`);
      const data = await res.json();

      if (data.items.length === 0) {
        const emptyMsg = (this.startDate || this.endDate)
          ? `No clean ${dataset} records found for this date range.`
          : `No clean ${dataset} records found.`;
        tbody.innerHTML = `<tr><td colspan="6" class="text-center py-4 text-muted">${emptyMsg}</td></tr>`;
      } else {
        if (dataset === 'customers') {
          tbody.innerHTML = data.items.map(r => `
            <tr>
              <td><strong>${r.customer_id}</strong></td>
              <td>${r.name}</td>
              <td><code>${r.email}</code></td>
              <td>${r.phone}</td>
              <td>${r.created_at}</td>
              <td class="text-muted text-sm">${new Date(r.created_timestamp).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}</td>
            </tr>
          `).join('');
        } else if (dataset === 'sales') {
          tbody.innerHTML = data.items.map(r => `
            <tr>
              <td><strong>${r.sale_id}</strong></td>
              <td>${r.customer_id}</td>
              <td class="font-bold text-navy">${r.amount.toFixed(2)}</td>
              <td><span class="badge badge-neutral">${r.currency}</span></td>
              <td>${r.sale_date}</td>
            </tr>
          `).join('');
        } else if (dataset === 'inventory') {
          tbody.innerHTML = data.items.map(r => `
            <tr>
              <td><strong>${r.product_id}</strong></td>
              <td>${r.product_name}</td>
              <td><span class="badge ${r.quantity > 0 ? 'badge-success' : 'badge-danger'}">${r.quantity} in stock</span></td>
              <td class="font-bold text-navy">$${r.unit_price.toFixed(2)}</td>
              <td>${r.updated_at}</td>
            </tr>
          `).join('');
        }
      }

      this.renderPagination(
        'recordsPaginationInfo',
        'recordsPaginationControls',
        data.total,
        data.page,
        data.page_size,
        (p) => {
          this.recordsPage = p;
          this.fetchRecords();
        }
      );

    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="6" class="text-center py-4 text-muted"><p class="text-danger mb-2 font-bold">Failed to load clean ${dataset} records.</p><button onclick="Dashboard.fetchRecords()" class="btn btn-secondary btn-sm">Retry Loading Records</button></td></tr>`;
    }
  },

  /* =========================================================================
     PAGINATION RENDERER
     ========================================================================= */

  renderPagination(infoId, controlsId, total, page, pageSize, onPageChange) {
    const infoEl = document.getElementById(infoId);
    const controlsEl = document.getElementById(controlsId);
    if (!infoEl || !controlsEl) return;

    if (total === 0) {
      infoEl.textContent = 'Showing 0 records';
      controlsEl.innerHTML = '';
      return;
    }

    const start = (page - 1) * pageSize + 1;
    const end = Math.min(page * pageSize, total);
    const totalPages = Math.ceil(total / pageSize);
    infoEl.textContent = `Showing ${start}–${end} of ${total.toLocaleString()}`;

    let html = `
      <button class="btn btn-secondary btn-sm" ${page <= 1 ? 'disabled' : ''} id="${controlsId}-prev">&larr; Prev</button>
      <span class="text-sm text-muted px-2">Page ${page} of ${totalPages}</span>
      <button class="btn btn-secondary btn-sm" ${page >= totalPages ? 'disabled' : ''} id="${controlsId}-next">Next &rarr;</button>
    `;
    controlsEl.innerHTML = html;

    const prevBtn = document.getElementById(`${controlsId}-prev`);
    const nextBtn = document.getElementById(`${controlsId}-next`);

    if (prevBtn && page > 1) {
      prevBtn.onclick = () => onPageChange(page - 1);
    }
    if (nextBtn && page < totalPages) {
      nextBtn.onclick = () => onPageChange(page + 1);
    }
  }
};

document.addEventListener('DOMContentLoaded', () => {
  Dashboard.init();
});
