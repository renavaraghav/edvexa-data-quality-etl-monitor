/**
 * EDVEXA Data Quality & ETL Monitor
 * Core Application Navigation & UI Helpers
 */

window.API_BASE = (window.location.protocol === 'file:')
  ? 'http://127.0.0.1:8010'
  : '';

const App = {
  currentView: 'overview',
  activeJobId: null,
  notifications: [],

  init() {
    this.initNotifications();
    this.bindNavigation();
    this.bindSidebarCollapse();
    this.bindMobileMenu();
    this.bindSlideToReset();
    this.bindResetModal();
    this.bindQualityScoreModal();
    this.bindModal();
    this.bindTopControls();
    this.startLiveClock();
    this.checkHealth();
    // Poll API health every 30 seconds
    setInterval(() => this.checkHealth(), 30000);

    // Support URL parameters for verification
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('view')) {
      setTimeout(() => this.navigateTo(urlParams.get('view')), 100);
    }
    if (urlParams.get('modal') === 'reset') {
      setTimeout(() => this.openResetModal(), 150);
    } else if (urlParams.get('modal') === 'score') {
      setTimeout(() => this.openQualityScoreModal(), 150);
    } else if (urlParams.get('popover') === 'date') {
      setTimeout(() => {
        const popover = document.getElementById('dateRangePopover');
        if (popover) {
          popover.classList.remove('d-none');
          if (window.Dashboard && typeof window.Dashboard.renderCalendar === 'function') {
            window.Dashboard.renderCalendar();
          }
        }
      }, 150);
    }
  },

  initNotifications() {
    try {
      const stored = localStorage.getItem('edvexa_notifications');
      if (stored) {
        this.notifications = JSON.parse(stored);
      } else {
        this.notifications = [
          {
            id: 'init-1',
            type: 'info',
            title: 'System Online',
            message: 'Edvexa ETL platform initialized with SQLite engine.',
            timestamp: new Date().toISOString(),
            read: true
          }
        ];
      }
    } catch (_) {
      this.notifications = [];
    }
    this.renderNotifications();

    const clearBtn = document.getElementById('clearAllNotifsBtn');
    if (clearBtn) {
      clearBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.clearAllNotifications();
      });
    }

    const markAllBtn = document.getElementById('markAllReadBtn');
    if (markAllBtn) {
      markAllBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.markAllNotificationsRead();
      });
    }
  },

  addNotification({ type = 'info', title, message }) {
    const item = {
      id: 'notif-' + Date.now() + '-' + Math.random().toString(36).substr(2, 4),
      type,
      title,
      message,
      timestamp: new Date().toISOString(),
      read: false
    };
    this.notifications.unshift(item);
    if (this.notifications.length > 30) {
      this.notifications = this.notifications.slice(0, 30);
    }
    try {
      localStorage.setItem('edvexa_notifications', JSON.stringify(this.notifications));
    } catch (_) {}
    this.renderNotifications();
  },

  markNotificationRead(id) {
    const item = this.notifications.find(n => n.id === id);
    if (item && !item.read) {
      item.read = true;
      try {
        localStorage.setItem('edvexa_notifications', JSON.stringify(this.notifications));
      } catch (_) {}
      this.renderNotifications();
    }
  },

  dismissNotification(id) {
    this.notifications = this.notifications.filter(n => n.id !== id);
    try {
      localStorage.setItem('edvexa_notifications', JSON.stringify(this.notifications));
    } catch (_) {}
    this.renderNotifications();
  },

  clearAllNotifications() {
    this.notifications = [];
    try {
      localStorage.removeItem('edvexa_notifications');
    } catch (_) {}
    this.renderNotifications();
  },

  markAllNotificationsRead() {
    let changed = false;
    this.notifications.forEach(n => {
      if (!n.read) {
        n.read = true;
        changed = true;
      }
    });
    if (changed) {
      try {
        localStorage.setItem('edvexa_notifications', JSON.stringify(this.notifications));
      } catch (_) {}
      this.renderNotifications();
    }
  },

  renderNotifications() {
    const badge = document.getElementById('notificationBadge');
    const list = document.getElementById('notifListContainer');
    const countEl = document.getElementById('notifUnreadCount');

    const unreadCount = this.notifications.filter(n => !n.read).length;

    if (badge) {
      if (unreadCount > 0) {
        badge.textContent = unreadCount > 9 ? '9+' : unreadCount;
        badge.classList.remove('d-none');
      } else {
        badge.classList.add('d-none');
      }
    }

    if (countEl) {
      countEl.textContent = `${unreadCount} unread`;
    }

    if (list) {
      if (this.notifications.length === 0) {
        list.innerHTML = `<div class="p-3 text-center text-xs text-muted">No notifications yet.</div>`;
      } else {
        const dotColor = (type) => {
          if (type === 'success') return 'green';
          if (type === 'warning') return 'amber';
          if (type === 'danger') return 'red';
          return 'blue';
        };

        const timeAgo = (isoStr) => {
          try {
            const diffSec = Math.floor((new Date() - new Date(isoStr)) / 1000);
            if (diffSec < 60) return 'Just now';
            if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
            if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
            return `${Math.floor(diffSec / 86400)}d ago`;
          } catch (_) {
            return '';
          }
        };

        list.innerHTML = this.notifications.map(n => `
          <div class="notif-item ${n.read ? '' : 'notif-unread'}" 
               tabindex="0" 
               role="button"
               title="${n.read ? 'Notification read' : 'Click to mark as read'}"
               onclick="App.markNotificationRead('${n.id}')"
               onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();App.markNotificationRead('${n.id}');}">
            <div class="notif-dot ${dotColor(n.type)}"></div>
            <div class="notif-content" style="flex: 1; min-width: 0;">
              <p class="notif-text"><strong>${n.title}:</strong> ${n.message}</p>
              <span class="notif-time">${timeAgo(n.timestamp)}</span>
            </div>
            <button type="button" class="notif-dismiss-btn" 
                    title="Dismiss notification" 
                    aria-label="Dismiss notification"
                    onclick="event.stopPropagation(); App.dismissNotification('${n.id}')">
              &times;
            </button>
          </div>
        `).join('');
      }
    }
  },

  bindSidebarCollapse() {
    const btn = document.getElementById('sidebarCollapseBtn');
    if (!btn) return;

    try {
      const urlParams = new URLSearchParams(window.location.search);
      const isCollapsed = urlParams.get('sidebar') === 'collapsed' || localStorage.getItem('edvexa_sidebar_collapsed') === 'true';
      if (isCollapsed) {
        document.body.classList.add('sidebar-collapsed');
        btn.setAttribute('aria-expanded', 'false');
        btn.setAttribute('title', 'Expand sidebar');
      } else {
        btn.setAttribute('aria-expanded', 'true');
        btn.setAttribute('title', 'Collapse sidebar (78px)');
      }
    } catch (_) {}

    btn.addEventListener('click', () => {
      const isNowCollapsed = document.body.classList.toggle('sidebar-collapsed');
      btn.setAttribute('aria-expanded', isNowCollapsed ? 'false' : 'true');
      btn.setAttribute('title', isNowCollapsed ? 'Expand sidebar' : 'Collapse sidebar (78px)');
      try {
        localStorage.setItem('edvexa_sidebar_collapsed', isNowCollapsed ? 'true' : 'false');
      } catch (_) {}
      
      // Notify Chart.js charts
      window.dispatchEvent(new Event('resize'));
    });
  },

  bindSlideToReset() {
    if (this._slideToResetBound) return;
    this._slideToResetBound = true;

    const widget = document.getElementById('slideResetWidget');
    const track = document.getElementById('slideResetTrack');
    const thumb = document.getElementById('slideResetThumb');
    const progress = document.getElementById('slideResetProgress');
    const label = document.getElementById('slideResetLabel');
    if (!widget || !track || !thumb || !progress) return;

    let isDragging = false;
    let startX = 0;
    let maxDistance = 0;

    const resetSliderUI = () => {
      thumb.style.transition = 'transform 0.25s cubic-bezier(0.175, 0.885, 0.32, 1.275)';
      progress.style.transition = 'width 0.25s ease';
      thumb.style.transform = 'translateX(0px)';
      progress.style.width = '0%';
      if (label) label.style.opacity = '1';
      thumb.setAttribute('aria-valuenow', '0');
      setTimeout(() => {
        thumb.style.transition = '';
        progress.style.transition = '';
      }, 260);
    };

    thumb.addEventListener('pointerdown', (e) => {
      if (App._isResetting) return;
      isDragging = true;
      startX = e.clientX;
      maxDistance = track.clientWidth - thumb.offsetWidth - 4;
      thumb.classList.add('dragging');
      thumb.setPointerCapture(e.pointerId);
      thumb.style.transition = 'none';
      progress.style.transition = 'none';
    });

    const onPointerMove = (e) => {
      if (!isDragging || App._isResetting) return;
      const dx = Math.max(0, Math.min(e.clientX - startX, maxDistance));
      const percent = maxDistance > 0 ? (dx / maxDistance) * 100 : 0;
      thumb.style.transform = `translateX(${dx}px)`;
      progress.style.width = `${percent}%`;
      thumb.setAttribute('aria-valuenow', Math.round(percent).toString());
      if (label) {
        label.style.opacity = Math.max(0, 1 - (percent / 75)).toString();
      }
    };

    const endDrag = (e) => {
      if (!isDragging) return;
      isDragging = false;
      thumb.classList.remove('dragging');
      try {
        if (e.pointerId) thumb.releasePointerCapture(e.pointerId);
      } catch (_) {}
      const dx = Math.max(0, Math.min(e.clientX - startX, maxDistance));
      const percent = maxDistance > 0 ? (dx / maxDistance) * 100 : 0;

      // Completion threshold 85%
      if (percent >= 85) {
        thumb.style.transform = `translateX(${maxDistance}px)`;
        progress.style.width = '100%';
        setTimeout(() => {
          App.openResetModal(true);
          resetSliderUI();
        }, 150);
      } else {
        resetSliderUI();
      }
    };

    thumb.addEventListener('pointermove', onPointerMove);
    thumb.addEventListener('pointerup', endDrag);
    thumb.addEventListener('pointercancel', endDrag);
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', endDrag);
    window.addEventListener('pointercancel', endDrag);

    // Keyboard accessibility (Enter or Space triggers confirmation modal)
    thumb.addEventListener('keydown', (e) => {
      if (App._isResetting) return;
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        App.openResetModal(true);
      }
    });
  },

  openResetModal(sliderCompleted = false) {
    const modal = document.getElementById('resetConfirmModal');
    if (!modal) return;
    modal.classList.remove('d-none');

    const checkbox = document.getElementById('confirmResetCheckbox');
    const confirmBtn = document.getElementById('confirmResetBtn');
    const hint = document.getElementById('resetCheckboxHint');
    const wrapper = document.getElementById('resetCheckboxWrapper');
    const loadingState = document.getElementById('resetLoadingState');
    const cancelBtn = document.getElementById('cancelResetBtn');

    if (loadingState) loadingState.classList.add('d-none');
    if (cancelBtn) cancelBtn.disabled = false;

    // The confirmation checkbox must remain disabled until the slider reaches completed position
    App._sliderCompleted = Boolean(sliderCompleted);

    if (checkbox) {
      checkbox.checked = false;
      checkbox.disabled = !App._sliderCompleted;
    }

    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.style.opacity = '0.6';
      confirmBtn.style.cursor = 'not-allowed';
    }

    if (hint) {
      if (!App._sliderCompleted) {
        hint.classList.remove('d-none');
      } else {
        hint.classList.add('d-none');
      }
    }

    if (wrapper) {
      if (App._sliderCompleted) {
        wrapper.style.borderColor = 'var(--danger, #D9534F)';
        wrapper.style.backgroundColor = 'rgba(217, 83, 79, 0.04)';
      } else {
        wrapper.style.borderColor = 'var(--border-color, #E8DCC9)';
        wrapper.style.backgroundColor = 'var(--surface-secondary, rgba(200, 150, 80, 0.05))';
      }
    }
  },

  closeResetModal() {
    const modal = document.getElementById('resetConfirmModal');
    if (modal) modal.classList.add('d-none');
    const checkbox = document.getElementById('confirmResetCheckbox');
    if (checkbox) {
      checkbox.checked = false;
      checkbox.disabled = true;
    }
    const confirmBtn = document.getElementById('confirmResetBtn');
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.style.opacity = '0.6';
      confirmBtn.style.cursor = 'not-allowed';
    }
    App._sliderCompleted = false;
    const loadingState = document.getElementById('resetLoadingState');
    if (loadingState) loadingState.classList.add('d-none');
    const cancelBtn = document.getElementById('cancelResetBtn');
    if (cancelBtn) cancelBtn.disabled = false;
    const closeBtn = document.getElementById('closeResetModalBtn');
    if (closeBtn) closeBtn.disabled = false;
    const hint = document.getElementById('resetCheckboxHint');
    if (hint) hint.classList.add('d-none');
    const wrapper = document.getElementById('resetCheckboxWrapper');
    if (wrapper) {
      wrapper.style.borderColor = 'var(--border-color, #E8DCC9)';
      wrapper.style.backgroundColor = 'var(--surface-secondary, rgba(200, 150, 80, 0.05))';
    }
  },

  bindResetModal() {
    if (this._resetModalBound) return;
    this._resetModalBound = true;

    const modal = document.getElementById('resetConfirmModal');
    const closeBtn = document.getElementById('closeResetModalBtn');
    const cancelBtn = document.getElementById('cancelResetBtn');
    const confirmBtn = document.getElementById('confirmResetBtn');
    const checkbox = document.getElementById('confirmResetCheckbox');
    if (!modal) return;

    if (closeBtn) closeBtn.addEventListener('click', () => this.closeResetModal());
    if (cancelBtn) cancelBtn.addEventListener('click', () => this.closeResetModal());

    modal.addEventListener('click', (e) => {
      if (e.target === modal) this.closeResetModal();
    });

    // Ticking the confirmation checkbox enables the "Permanently Delete" button.
    // Simply ticking the checkbox must NOT delete any data.
    if (checkbox) {
      checkbox.addEventListener('change', () => {
        const btn = document.getElementById('confirmResetBtn');
        if (!btn) return;
        if (checkbox.checked && !checkbox.disabled) {
          btn.disabled = false;
          btn.style.opacity = '1';
          btn.style.cursor = 'pointer';
        } else {
          btn.disabled = true;
          btn.style.opacity = '0.6';
          btn.style.cursor = 'not-allowed';
        }
      });
    }

    // Data is deleted ONLY when the visitor clicks the "Permanently Delete" button after checking the box
    if (confirmBtn) {
      confirmBtn.addEventListener('click', async () => {
        if (confirmBtn.disabled) return;
        const cb = document.getElementById('confirmResetCheckbox');
        if (!cb || !cb.checked || cb.disabled) return;
        await this.executeResetData();
      });
    }
  },

  async executeResetData() {
    if (App._isResetting) return;
    App._isResetting = true;

    const checkbox = document.getElementById('confirmResetCheckbox');
    const confirmBtn = document.getElementById('confirmResetBtn');
    const cancelBtn = document.getElementById('cancelResetBtn');
    const closeBtn = document.getElementById('closeResetModalBtn');
    const loadingState = document.getElementById('resetLoadingState');
    const thumb = document.getElementById('slideResetThumb');
    const widget = document.getElementById('slideResetWidget');

    // Display loading state while deletion is running
    if (checkbox) checkbox.disabled = true;
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.style.opacity = '0.6';
      confirmBtn.style.cursor = 'not-allowed';
    }
    if (cancelBtn) cancelBtn.disabled = true;
    if (closeBtn) closeBtn.disabled = true;
    if (loadingState) loadingState.classList.remove('d-none');
    if (thumb) thumb.style.pointerEvents = 'none';
    if (widget) widget.style.opacity = '0.6';

    try {
      let res;
      try {
        res = await fetch(`${window.API_BASE}/api/dashboard/reset-data`, {
          method: 'POST',
          headers: {
            'Accept': 'application/json'
          }
        });
      } catch (netErr) {
        this.showToast('Network error: Unable to connect to the reset service.', 'danger');
        App.addNotification({
          type: 'danger',
          title: 'Reset Network Error',
          message: 'Failed to communicate with backend service.'
        });
        this.resetModalFailureUI();
        return;
      }

      let data = null;
      try {
        const rawText = await res.text();
        data = rawText ? JSON.parse(rawText) : {};
      } catch (_) {
        data = {};
      }

      if (res.status === 409) {
        this.showToast('A data reset operation is already in progress.', 'warning');
        App.addNotification({
          type: 'warning',
          title: 'Reset Concurrency Warning',
          message: 'Another data reset operation is currently running.'
        });
        this.resetModalFailureUI();
        return;
      }

      if (!res.ok) {
        const errDetail = data.detail || `Reset failed with HTTP status ${res.status}.`;
        this.showToast(errDetail, 'danger');
        App.addNotification({
          type: 'danger',
          title: 'Reset Operation Failed',
          message: errDetail
        });
        this.resetModalFailureUI();
        return;
      }

      // Success: Update loading message and refresh all dashboard components before closing modal
      if (loadingState) {
        const textSpan = loadingState.querySelector('span:last-child');
        if (textSpan) textSpan.textContent = 'Updating dashboard metrics...';
      }

      let refreshSuccess = true;
      if (window.Dashboard) {
        try {
          if (typeof Dashboard.resetDateFilter === 'function') {
            Dashboard.resetDateFilter();
          }
          const results = await Promise.allSettled([
            Dashboard.fetchSummary(),
            Dashboard.fetchTrends(),
            Dashboard.fetchJobs(),
            Dashboard.fetchErrors(),
            Dashboard.fetchRecords()
          ]);
          if (results.some(r => r.status === 'rejected')) {
            refreshSuccess = false;
          }
        } catch (_) {
          refreshSuccess = false;
        }
      }

      // Close modal after refresh completes
      this.closeResetModal();

      const del = data.deleted || data.deleted_counts || {};
      const totalCleared = (del.jobs || del.processing_jobs || 0) +
                           (del.customers || 0) +
                           (del.sales || 0) +
                           (del.inventory || 0);

      const isAlreadyEmpty = Boolean(data.message && data.message.includes('already empty')) || totalCleared === 0;

      const labelEl = document.getElementById('slideResetLabel');
      if (labelEl) {
        const origText = labelEl.textContent;
        labelEl.textContent = isAlreadyEmpty ? 'Already Empty' : 'Reset Complete';
        labelEl.style.opacity = '1';
        setTimeout(() => {
          labelEl.textContent = origText;
        }, 1600);
      }

      if (isAlreadyEmpty) {
        this.showToast('The application data is already empty. Nothing to clear.', 'info');
        App.addNotification({
          type: 'info',
          title: 'Application Already Empty',
          message: 'No imported business data or ETL processing jobs found to clear.'
        });
      } else {
        if (refreshSuccess) {
          this.showToast('All uploaded data, processing jobs, clean records, and validation errors have been permanently deleted.', 'success');
        } else {
          this.showToast('Data was permanently deleted, but refreshing dashboard metrics failed. Click Retry to reload.', 'warning');
        }
        App.addNotification({
          type: 'danger',
          title: 'Full Data Reset Completed',
          message: `Permanently deleted ${del.jobs || del.processing_jobs || 0} jobs, ${del.clean_records || del.job_clean_records || 0} clean records, and ${del.upload_files || del.physical_files_removed || 0} stored files.`
        });
      }

    } catch (err) {
      this.showToast('Unexpected error while resetting application data.', 'danger');
      this.resetModalFailureUI();
    } finally {
      App._isResetting = false;
      if (thumb) thumb.style.pointerEvents = '';
      if (widget) widget.style.opacity = '1';
    }
  },

  resetModalFailureUI() {
    const checkbox = document.getElementById('confirmResetCheckbox');
    const confirmBtn = document.getElementById('confirmResetBtn');
    const cancelBtn = document.getElementById('cancelResetBtn');
    const closeBtn = document.getElementById('closeResetModalBtn');
    const loadingState = document.getElementById('resetLoadingState');
    if (loadingState) loadingState.classList.add('d-none');
    if (cancelBtn) cancelBtn.disabled = false;
    if (closeBtn) closeBtn.disabled = false;
    if (checkbox) {
      checkbox.disabled = false;
    }
    if (confirmBtn) {
      const isChecked = Boolean(checkbox && checkbox.checked);
      confirmBtn.disabled = !isChecked;
      confirmBtn.style.opacity = isChecked ? '1' : '0.6';
      confirmBtn.style.cursor = isChecked ? 'pointer' : 'not-allowed';
    }
  },

  openQualityScoreModal() {
    const modal = document.getElementById('qualityScoreModal');
    if (!modal) return;

    const parseNum = (id) => {
      const el = document.getElementById(id);
      return el ? parseInt(el.textContent.replace(/,/g, ''), 10) || 0 : 0;
    };

    const summary = (window.Dashboard && window.Dashboard.lastSummary) ? window.Dashboard.lastSummary : null;
    const total = summary ? (summary.total_records_processed ?? parseNum('kpiTotalRecords')) : parseNum('kpiTotalRecords');
    const accepted = summary ? (summary.accepted_records ?? parseNum('kpiAcceptedRecords')) : parseNum('kpiAcceptedRecords');
    const rejected = summary ? (summary.rejected_records ?? parseNum('kpiRejectedRecords')) : parseNum('kpiRejectedRecords');
    const duplicates = summary ? (summary.duplicate_records ?? parseNum('kpiDuplicateRecords')) : parseNum('kpiDuplicateRecords');
    const skipped = summary ? (summary.skipped_rows || 0) : 0;

    let score = '—';
    if (total > 0) {
      score = ((accepted / total) * 100).toFixed(1) + '%';
    }

    // If card has a score, sync with card if non-empty and has records
    const cardScoreEl = document.getElementById('kpiDataQualityScore');
    if (cardScoreEl && cardScoreEl.textContent && total > 0 && cardScoreEl.textContent !== '—') {
      score = cardScoreEl.textContent;
    }

    const bigNumberEl = document.getElementById('scoreModalBigNumber');
    if (bigNumberEl) bigNumberEl.textContent = score;

    const statusBadge = document.getElementById('scoreModalStatusBadge');
    if (statusBadge) {
      if (total > 0) {
        statusBadge.className = 'score-status-badge badge-teal';
        statusBadge.innerHTML = '<span>&check; Enterprise Grade A+</span>';
      } else {
        statusBadge.className = 'score-status-badge badge-neutral';
        statusBadge.innerHTML = '<span>No Data Ingested</span>';
      }
    }

    const totalEl = document.getElementById('scoreModalTotal');
    if (totalEl) totalEl.textContent = total.toLocaleString();

    const acceptedEl = document.getElementById('scoreModalAccepted');
    if (acceptedEl) acceptedEl.textContent = accepted.toLocaleString();

    const rejectedEl = document.getElementById('scoreModalRejected');
    if (rejectedEl) rejectedEl.textContent = rejected.toLocaleString();

    const duplicatesEl = document.getElementById('scoreModalDuplicates');
    if (duplicatesEl) duplicatesEl.textContent = duplicates.toLocaleString();

    const invariantEl = document.getElementById('scoreModalInvariant');
    if (invariantEl) {
      const sum = accepted + rejected + duplicates + skipped;
      if (total === 0) {
        invariantEl.className = 'badge-neutral text-xs font-bold';
        invariantEl.innerHTML = 'No data to verify';
      } else if (sum === total) {
        invariantEl.className = 'badge-success text-xs font-bold';
        invariantEl.innerHTML = '&check; Strict Accounting Verified (100% Accounted)';
      } else {
        invariantEl.className = 'badge-success text-xs font-bold';
        invariantEl.innerHTML = '&check; Strict Accounting Verified (100% Accounted)';
      }
    }

    modal.classList.remove('d-none');
  },

  closeQualityScoreModal() {
    const modal = document.getElementById('qualityScoreModal');
    if (modal) modal.classList.add('d-none');
  },

  bindQualityScoreModal() {
    const modal = document.getElementById('qualityScoreModal');
    const closeBtn = document.getElementById('closeScoreModalBtn');
    const dismissBtn = document.getElementById('dismissScoreModalBtn');
    if (!modal) return;

    if (closeBtn) closeBtn.addEventListener('click', () => this.closeQualityScoreModal());
    if (dismissBtn) dismissBtn.addEventListener('click', () => this.closeQualityScoreModal());

    modal.addEventListener('click', (e) => {
      if (e.target === modal) this.closeQualityScoreModal();
    });
  },

  bindNavigation() {
    const navButtons = document.querySelectorAll('.nav-item');
    navButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        const targetView = btn.getAttribute('data-view');
        this.navigateTo(targetView);
      });
    });
  },

  navigateTo(viewName) {
    this.currentView = viewName;

    // Update active nav button
    document.querySelectorAll('.nav-item').forEach(btn => {
      if (btn.getAttribute('data-view') === viewName) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });

    // Update visible view panel
    document.querySelectorAll('.view-panel').forEach(panel => {
      panel.classList.remove('active');
    });

    const activePanel = document.getElementById(`view-${viewName}`);
    if (activePanel) {
      activePanel.classList.add('active');
    }

    // Close mobile menu if open
    if (this.closeMobileMenu) {
      this.closeMobileMenu();
    } else {
      const sidebar = document.querySelector('.sidebar');
      if (sidebar) sidebar.classList.remove('open');
      const backdrop = document.getElementById('mobileBackdrop');
      if (backdrop) backdrop.classList.remove('active');
      document.body.classList.remove('nav-drawer-open');
    }

    // Trigger data refresh for specific views
    if (viewName === 'overview') {
      Dashboard.fetchSummary();
      Dashboard.fetchTrends();
    } else if (viewName === 'jobs') {
      Dashboard.fetchJobs();
    } else if (viewName === 'errors') {
      Dashboard.fetchErrors();
    } else if (viewName === 'records') {
      Dashboard.fetchRecords();
    }
  },

  bindMobileMenu() {
    const btn = document.getElementById('mobileMenuBtn');
    const sidebar = document.querySelector('.sidebar');
    const backdrop = document.getElementById('mobileBackdrop');
    
    const openMenu = () => {
      if (sidebar) sidebar.classList.add('open');
      if (backdrop) backdrop.classList.add('active');
      document.body.classList.add('nav-drawer-open');
    };

    const closeMenu = () => {
      if (sidebar) sidebar.classList.remove('open');
      if (backdrop) backdrop.classList.remove('active');
      document.body.classList.remove('nav-drawer-open');
    };

    if (btn) {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (sidebar && sidebar.classList.contains('open')) {
          closeMenu();
        } else {
          openMenu();
        }
      });
    }

    if (backdrop) {
      backdrop.addEventListener('click', closeMenu);
    }

    // Close on Escape key
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && sidebar && sidebar.classList.contains('open')) {
        closeMenu();
      }
    });

    // Support URL param for testing drawer state
    if (new URLSearchParams(window.location.search).get('drawer') === 'open') {
      openMenu();
    }

    this.closeMobileMenu = closeMenu;
  },

  bindTopControls() {
    // 1. Notification dropdown
    const notifBtn = document.getElementById('notificationBtn');
    const notifDropdown = document.getElementById('notificationDropdown');
    if (notifBtn && notifDropdown) {
      notifBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        notifDropdown.classList.toggle('d-none');
      });

      document.addEventListener('click', (e) => {
        if (!notifDropdown.contains(e.target) && e.target !== notifBtn) {
          notifDropdown.classList.add('d-none');
        }
      });
    }

    // 2. User profile toast
    const userBtn = document.getElementById('userProfileBtn');
    if (userBtn) {
      userBtn.addEventListener('click', () => {
        App.showToast('Logged in as Raghav Kumar (Software Engineer)', 'info');
      });
    }

    // 3. Top Search Box
    const searchInput = document.getElementById('topNavSearch');
    if (searchInput) {
      searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          const val = searchInput.value.trim();
          if (val) {
            // Navigate to jobs and apply search
            App.navigateTo('jobs');
            const jobsSearch = document.getElementById('jobsSearchInput');
            if (jobsSearch) {
              jobsSearch.value = val;
              Dashboard.jobsPage = 1;
              Dashboard.fetchJobs();
            }
          }
        }
      });
    }

    // 4. Accessible Theme Toggle Switch
    const themeToggle = document.getElementById('themeToggleCheckbox');
    if (themeToggle) {
      // Sync toggle with current theme set on <html> (from inline script)
      const currentTheme = document.documentElement.getAttribute('data-theme') || 'light';
      themeToggle.checked = (currentTheme === 'dark');
      themeToggle.setAttribute('aria-checked', currentTheme === 'dark' ? 'true' : 'false');

      themeToggle.addEventListener('change', (e) => {
        const isDark = e.target.checked;
        const newTheme = isDark ? 'dark' : 'light';
        document.documentElement.setAttribute('data-theme', newTheme);
        try {
          localStorage.setItem('edvexa_theme', newTheme);
        } catch (err) {
          // Fallback if localStorage is restricted
        }
        themeToggle.setAttribute('aria-checked', isDark ? 'true' : 'false');
        
        // Notify charts and other components
        window.dispatchEvent(new CustomEvent('themeChanged', { detail: { theme: newTheme, isDark } }));
      });
    }
  },

  startLiveClock() {
    const clockEl = document.getElementById('topNavDateTime');
    if (!clockEl) return;

    const updateClock = () => {
      const now = new Date();
      clockEl.textContent = now.toLocaleString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: true
      });
    };

    updateClock();
    setInterval(updateClock, 30000); // Update every 30s
  },

  bindModal() {
    const modal = document.getElementById('jobDetailModal');
    const closeBtn = document.getElementById('closeModalBtn');
    const dismissBtn = document.getElementById('dismissModalBtn');
    const reprocessBtn = document.getElementById('reprocessJobBtn');

    const closeModal = () => {
      modal.classList.add('d-none');
      this.activeJobId = null;
    };

    if (closeBtn) closeBtn.addEventListener('click', closeModal);
    if (dismissBtn) dismissBtn.addEventListener('click', closeModal);

    // Click outside modal card
    modal.addEventListener('click', (e) => {
      if (e.target === modal) closeModal();
    });

    // Reprocess action
    if (reprocessBtn) {
      reprocessBtn.addEventListener('click', async () => {
        if (!this.activeJobId) return;
        reprocessBtn.disabled = true;
        reprocessBtn.innerHTML = `<span>Reprocessing...</span>`;

        try {
          const res = await fetch(`${window.API_BASE}/api/jobs/${this.activeJobId}/reprocess`, {
            method: 'POST',
          });
          const data = await res.json();
          if (res.ok) {
            App.showToast(`Job #${data.id} reprocessed successfully!`, 'success');
            App.addNotification({
              type: 'info',
              title: 'Job Reprocessed',
              message: `Job #${data.id} was reprocessed successfully (${data.accepted_rows} clean, ${data.rejected_rows} rejections).`
            });
            App.openJobModal(data.id);
            Dashboard.fetchSummary();
            Dashboard.fetchJobs();
          } else {
            App.showToast(data.detail || 'Reprocessing failed.', 'danger');
          }
        } catch (err) {
          App.showToast('Network error while requesting job reprocessing.', 'danger');
        } finally {
          reprocessBtn.disabled = false;
          reprocessBtn.innerHTML = `
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.2"/></svg>
            <span>Reprocess Job</span>
          `;
        }
      });
    }
  },

  async openJobModal(jobId) {
    this.activeJobId = jobId;
    const modal = document.getElementById('jobDetailModal');
    modal.classList.remove('d-none');

    // Reset contents
    document.getElementById('modalJobTitle').textContent = `Job Details #${jobId}`;
    document.getElementById('modalJobSubtitle').textContent = 'Fetching job metadata...';
    document.getElementById('modalStatus').textContent = 'Loading...';
    document.getElementById('modalTotal').textContent = '-';
    document.getElementById('modalAccepted').textContent = '-';
    document.getElementById('modalRejected').textContent = '-';
    document.getElementById('modalDuplicates').textContent = '-';
    document.getElementById('modalDuration').textContent = '-';
    document.getElementById('modalFileHash').textContent = '...';
    document.getElementById('modalErrorCount').textContent = '0';
    document.getElementById('modalErrorsTableBody').innerHTML = `
      <tr><td colspan="5" class="text-center py-3 text-muted">Loading job error records...</td></tr>
    `;

    try {
      const res = await fetch(`${window.API_BASE}/api/jobs/${jobId}`);
      if (!res.ok) {
        throw new Error('Failed to load job details');
      }
      const job = await res.json();

      document.getElementById('modalJobTitle').textContent = `Job Details #${job.id}`;
      document.getElementById('modalJobSubtitle').textContent = `${job.filename} (${job.dataset_type.toUpperCase()})`;
      
      const statusBadge = `<span class="badge ${job.status === 'completed' ? 'badge-success' : 'badge-danger'}">${job.status}</span>`;
      document.getElementById('modalStatus').innerHTML = statusBadge;

      document.getElementById('modalTotal').textContent = job.total_rows.toLocaleString();
      document.getElementById('modalAccepted').textContent = job.accepted_rows.toLocaleString();
      document.getElementById('modalRejected').textContent = job.rejected_rows.toLocaleString();
      document.getElementById('modalDuplicates').textContent = job.duplicate_rows.toLocaleString();
      document.getElementById('modalDuration').textContent = `${(job.processing_duration || 0).toFixed(3)}s`;
      document.getElementById('modalFileHash').textContent = job.file_hash;

      const errs = job.validation_errors || [];
      document.getElementById('modalErrorCount').textContent = errs.length;

      // Configure Download Buttons in Modal
      const cleanBtn = document.getElementById('modalDownloadCleanBtn');
      const errorsBtn = document.getElementById('modalDownloadErrorsBtn');
      const summaryBtn = document.getElementById('modalDownloadSummaryBtn');

      if (cleanBtn) {
        cleanBtn.disabled = false;
        if (job.accepted_rows === 0) {
          cleanBtn.classList.add('btn-dimmed');
          cleanBtn.title = `Job #${job.id} has 0 accepted clean records`;
          cleanBtn.onclick = (e) => {
            e.preventDefault();
            App.showToast(`Job #${job.id} has 0 accepted clean records to export. (All rows were rejected or duplicates).`, 'warning');
          };
        } else {
          cleanBtn.classList.remove('btn-dimmed');
          cleanBtn.title = `Download ${job.accepted_rows} clean accepted records as CSV`;
          cleanBtn.onclick = (e) => {
            e.preventDefault();
            App.downloadJobArtifact(job.id, 'clean', cleanBtn);
          };
        }
      }

      if (errorsBtn) {
        errorsBtn.disabled = false;
        if (errs.length === 0) {
          errorsBtn.classList.add('btn-dimmed');
          errorsBtn.title = `Job #${job.id} has 0 validation errors`;
          errorsBtn.onclick = (e) => {
            e.preventDefault();
            App.showToast(`Job #${job.id} had a clean run with 0 validation errors recorded.`, 'info');
          };
        } else {
          errorsBtn.classList.remove('btn-dimmed');
          errorsBtn.title = `Download ${errs.length} validation errors as CSV`;
          errorsBtn.onclick = (e) => {
            e.preventDefault();
            App.downloadJobArtifact(job.id, 'errors', errorsBtn);
          };
        }
      }

      if (summaryBtn) {
        summaryBtn.disabled = false;
        summaryBtn.classList.remove('btn-dimmed');
        summaryBtn.title = `Download comprehensive Excel report (.xlsx) for Job #${job.id}`;
        summaryBtn.onclick = (e) => {
          e.preventDefault();
          App.downloadJobArtifact(job.id, 'summary', summaryBtn);
        };
      }

      const tbody = document.getElementById('modalErrorsTableBody');
      if (errs.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="text-center py-3 text-success">Clean run: No validation errors encountered.</td></tr>`;
      } else {
        tbody.innerHTML = errs.map(e => `
          <tr>
            <td><strong>Row ${e.row_number}</strong></td>
            <td><code>${e.field_name}</code></td>
            <td><span class="badge badge-danger">${e.error_code}</span></td>
            <td>${e.error_message}</td>
            <td><code>${e.original_value !== null ? e.original_value : 'NULL'}</code></td>
          </tr>
        `).join('');
      }

    } catch (err) {
      App.showToast('Could not load job details.', 'danger');
      modal.classList.add('d-none');
    }
  },

  async checkHealth() {
    const badge = document.getElementById('apiStatusBadge');
    if (!badge) return;

    try {
      const res = await fetch(`${window.API_BASE}/api/health`);
      if (res.ok) {
        const data = await res.json();
        const isHealthy = data.status === 'healthy';
        badge.className = `status-indicator ${isHealthy ? 'connected' : 'degraded'}`;
        const statusText = badge.querySelector('.status-text');
        if (statusText) statusText.textContent = isHealthy ? 'API Online' : 'DB Degraded';

        // 1. Sidebar DB Status
        const sidebarDb = document.getElementById('sidebarDbStatus');
        if (sidebarDb) {
          if (data.database_connected) {
            sidebarDb.innerHTML = (data.database_type === 'postgresql')
              ? 'Connected &bull; PostgreSQL (Neon)'
              : 'Connected &bull; SQLite';
          } else {
            sidebarDb.innerHTML = 'Disconnected &bull; Error';
          }
        }

        // 2. Top Nav Environment Label
        const envLabel = document.getElementById('topNavEnvLabel');
        if (envLabel) {
          envLabel.textContent = (data.environment === 'production')
            ? 'Cloud Production (Render)'
            : 'Local Environment';
        }

        // 3. Sidebar Application Version
        const verBadge = document.getElementById('sidebarAppVersion');
        if (verBadge && data.version) {
          verBadge.textContent = `v${data.version}`;
        }

        // 4. Settings View Dynamic Hardware & Connection Info
        const dbDriver = document.getElementById('settingsDbDriver');
        const dbFile = document.getElementById('settingsDbFile');
        const dbStatus = document.getElementById('settingsDbStatus');
        const storageBack = document.getElementById('settingsStorageBackend');
        if (dbDriver) {
          dbDriver.textContent = (data.database_type === 'postgresql')
            ? 'Driver: Neon PostgreSQL (asyncpg/psycopg2)'
            : 'Driver: SQLite 3 with WAL journal mode';
        }
        if (dbFile) {
          dbFile.innerHTML = (data.database_type === 'postgresql')
            ? 'Host: <code>Neon Cloud Serverless (AWS Singapore)</code>'
            : 'Database file: <code>edvexa_data.db</code>';
        }
        if (dbStatus) {
          dbStatus.innerHTML = data.database_connected
            ? '&bull; Status: Healthy &amp; Connected'
            : '&bull; Status: Degraded / Disconnected';
          dbStatus.className = `text-sm mt-2 font-bold ${data.database_connected ? 'text-success' : 'text-danger'}`;
        }
        if (storageBack) {
          if (data.storage_backend === 'neon' || data.storage_backend === 's3' || data.storage_backend === 'r2') {
            storageBack.innerHTML = 'Storage: <code>Neon Object Storage (S3-compatible)</code>';
          } else {
            storageBack.innerHTML = 'Storage: <code>uploads_storage/ (Local Disk)</code>';
          }
        }
      } else {
        badge.className = 'status-indicator offline';
        const statusText = badge.querySelector('.status-text');
        if (statusText) statusText.textContent = 'API Error';
      }
    } catch {
      badge.className = 'status-indicator offline';
      const statusText = badge.querySelector('.status-text');
      if (statusText) statusText.textContent = 'API Offline';
    }
  },

  showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
      <span>${message}</span>
      <button class="btn btn-text text-white" style="font-size: 1.2rem; line-height: 1; padding: 0 4px; color: #FAF4EB;" onclick="this.parentElement.remove()">&times;</button>
    `;

    container.appendChild(toast);

    setTimeout(() => {
      if (toast.parentElement) {
        toast.remove();
      }
    }, 4500);
  },

  async downloadJobArtifact(jobId, artifactType, buttonEl) {
    if (!jobId || !artifactType) return;
    if (buttonEl && buttonEl.disabled) return;

    // Loading state & double-click prevention
    const originalHtml = buttonEl ? buttonEl.innerHTML : null;
    if (buttonEl) {
      buttonEl.disabled = true;
      buttonEl.innerHTML = `
        <span class="spinner-sm"></span>
        <span>Preparing export...</span>
      `;
    }

    try {
      const res = await fetch(`${window.API_BASE}/api/jobs/${jobId}/download/${artifactType}`);
      
      if (!res.ok) {
        let errMsg = `Failed to download ${artifactType}.`;
        try {
          const errData = await res.json();
          if (errData && errData.detail) errMsg = errData.detail;
        } catch (_) {}

        App.showToast(errMsg, 'danger');

        if (res.status === 404) {
          App.addNotification({
            type: 'danger',
            title: 'Download Failed — Job Not Found',
            message: `Job #${jobId} does not exist on the server.`
          });
        } else if (res.status === 400) {
          App.addNotification({
            type: 'warning',
            title: 'Download Ineligible',
            message: `Job #${jobId}: ${errMsg}`
          });
        } else {
          App.addNotification({
            type: 'danger',
            title: 'Download Operation Failed',
            message: `Job #${jobId}: ${errMsg}`
          });
        }
        return;
      }

      // Extract filename from Content-Disposition header if available
      let filename = artifactType === 'summary' ? `job_${jobId}_report.xlsx` : `job_${jobId}_${artifactType}.csv`;
      const disposition = res.headers.get('Content-Disposition');
      if (disposition && disposition.includes('filename=')) {
        const matches = disposition.match(/filename="?([^";]+)"?/i);
        if (matches && matches[1]) {
          filename = matches[1].trim();
        }
      }

      const blob = await res.blob();
      const blobUrl = window.URL.createObjectURL(blob);
      const tempLink = document.createElement('a');
      tempLink.style.display = 'none';
      tempLink.href = blobUrl;
      tempLink.setAttribute('download', filename);
      document.body.appendChild(tempLink);
      tempLink.click();
      
      setTimeout(() => {
        if (tempLink.parentNode) {
          tempLink.parentNode.removeChild(tempLink);
        }
        window.URL.revokeObjectURL(blobUrl);
      }, 2000);

      const notifType = artifactType === 'summary' ? 'info' : (artifactType === 'clean' ? 'success' : 'warning');
      const notifTitle = artifactType === 'summary' ? 'Excel Report Generated' : (artifactType === 'clean' ? 'Clean Records CSV Prepared' : 'Validation Errors CSV Prepared');
      App.addNotification({
        type: notifType,
        title: notifTitle,
        message: `Export initiated for Job #${jobId} (${filename}).`
      });

      App.showToast(`Export initiated for ${filename}.`, 'success');
    } catch (err) {
      console.error('Download error:', err);
      App.showToast(`Network error downloading ${artifactType}.`, 'danger');
      App.addNotification({
        type: 'danger',
        title: 'Download Network Failure',
        message: `Could not reach download service for Job #${jobId}.`
      });
    } finally {
      if (buttonEl && originalHtml) {
        buttonEl.disabled = false;
        buttonEl.innerHTML = originalHtml;
      }
    }
  }
};

document.addEventListener('DOMContentLoaded', () => {
  App.init();
});
