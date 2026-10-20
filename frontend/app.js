/**
 * Vinted UK Sales & Price Tracker — Frontend Logic
 */

class VintedApp {
  constructor() {
    this.searches = [];
    this.currentSearchId = null;
    this.currentPeriod = 'week'; // 'day' | 'week' | 'month' | 'year'
    
    // Category Taxonomy & Classifier state
    this.availableCategories = [];
    this.selectedCategories = new Set();
    this.editSelectedCategories = new Set();
    this.categoryPickerState = {
      'category-chips-container': { dept: 'All', query: '', expandedGroups: new Set(['outerwear', 'jumpers_sweaters']) },
      'edit-category-chips-container': { dept: 'All', query: '', expandedGroups: new Set() },
    };
    this.debounceTimer = null;

    // Sniper state
    this.sniperActive = false;
    this.sniperPollTimer = null;
    this.sniperTargets = new Set();
    this.sniperTargetsInitialized = false;
    this.sniperDeals = [];
    this.clearedDealIds = new Set();
    this.previousDealCount = 0;
    this.audioCtx = null;
    this.sniperTargetInfo = new Map();
    this.itemMaxPrices = new Map();
    this.focusedDealIndex = -1;
    this.scanningMode = 'poll';

    // Scraper Hub & Batch state
    this.batchTargets = new Set();
    this.batchTargetsInitialized = false;
    this.schedulerStatus = null;
    this.schedulerTickTimer = null;
    this.schedulerTickCount = 0;
    this.lastSeenCycleTime = null;

    // Toast Timer
    this.toastTimer = null;

    // Chart instances
    this.salesQuantityChart = null;
    this.avgPriceChart = null;

    // Database Explorer state
    this.isDbExplorerActive = false;
    this.dbExplorerFilters = {
      q: '',
      search_id: '',
      status_filter: 'all',
      replica_filter: 'all',
      replica_reason: '',
      photo_key: '',
      child_filter: 'all',
      month: '',
      min_price: '',
      max_price: '',
      sort_by: 'newest',
      page: 1,
      page_size: 50,
    };
    this.dbAnalyticsData = null;
    this.dbExplorerDebounceTimer = null;
    this.isReplicaMgmtOpen = false;

    // Scraper Live Activity Terminal state
    this.terminalState = {
      isOpen: false,
      isMaximized: false,
      autoScroll: true,
      activeFilter: 'all', // 'all' | 'db_added' | 'scans'
      pollTimer: null,
      lastEventId: 0,
      events: [],
      totalAddedSession: 0,
      totalInspectedSession: 0,
      totalPriceDropsSession: 0,
    };

    this.init();
  }

  async init() {
    this.setupEventListeners();
    await Promise.all([
      this.loadAvailableCategories(),
      this.loadSearches(),
      this.loadSchedulerStatus(),
      this.loadSniperTargetsInfo(),
      this.loadAntiBanStatus(),
      this.loadDatabaseSummaryBadges()
    ]);
    this.startSchedulerCountdownLoop();
    this.startTerminalPolling();
    this.showSniperView();
    await this.checkExistingSniperStatus();
    if (window.lucide) lucide.createIcons();
  }

  setupEventListeners() {
    // Add Search Modal button
    const openAddBtn = document.getElementById('btn-open-add-modal');
    if (openAddBtn) {
      openAddBtn.addEventListener('click', () => this.openAddModal());
    }

    const brandInput = document.getElementById('input-brand');
    if (brandInput) {
      brandInput.addEventListener('input', () => {
        brandInput.dataset.autoFilled = 'false';
      });
    }

    // Smooth Wheel Scrolling Preservation for Horizontal Tables & Nested Containers
    window.addEventListener('wheel', (e) => {
      const target = e.target;
      if (!target) return;

      // Unfocus number inputs on wheel so price filters don't trap the wheel or step values
      if (target.matches && target.matches('input[type="number"]') && document.activeElement === target) {
        target.blur();
      }

      // If user holds Shift key, they explicitly want horizontal scrolling
      if (e.shiftKey) return;

      // Check if cursor is over a horizontal scroll container (e.g. database explorer table)
      const hScrollContainer = target.closest ? target.closest('.overflow-x-auto, .db-table-scroll-container') : null;
      if (hScrollContainer) {
        const absX = Math.abs(e.deltaX);
        const absY = Math.abs(e.deltaY);

        // If vertical scroll intent dominates (typical up/down trackpad gesture or mouse wheel):
        if (absY > absX && absY > 0) {
          // Prevent horizontal scroller from latching onto and swallowing vertical gestures
          e.preventDefault();

          let delta = e.deltaY;
          if (e.deltaMode === 1) delta *= 16;
          else if (e.deltaMode === 2) delta *= window.innerHeight;

          // Scroll active vertical scroller (main or window)
          const main = document.querySelector('main');
          if (main && (main.scrollHeight > main.clientHeight)) {
            main.scrollTop += delta;
          } else {
            window.scrollBy({ top: delta, behavior: 'auto' });
          }
        }
      }
    }, { passive: false });

    // Unlock Web Audio API context on first user interaction (bypasses browser autoplay policy)
    const unlockAudio = () => {
      try {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (AudioContext && !this.audioCtx) this.audioCtx = new AudioContext();
        if (this.audioCtx && this.audioCtx.state === 'suspended') {
          this.audioCtx.resume();
        }
      } catch (e) {}
      document.removeEventListener('click', unlockAudio);
      document.removeEventListener('keydown', unlockAudio);
      document.removeEventListener('touchstart', unlockAudio);
    };
    document.addEventListener('click', unlockAudio, { passive: true });
    document.addEventListener('keydown', unlockAudio, { passive: true });
    document.addEventListener('touchstart', unlockAudio, { passive: true });

    // Global Power Trader Keyboard Shortcuts (J/K/S/T/?/Esc)
    window.addEventListener('keydown', (e) => {
      // Do not capture if typing in an input, textarea, or select
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName) || e.target.isContentEditable) {
        if (e.key === 'Escape') {
          e.target.blur();
        }
        return;
      }

      if (e.key === 'j' || e.key === 'J') {
        e.preventDefault();
        this.navigateDealCards(1);
      } else if (e.key === 'k' || e.key === 'K') {
        e.preventDefault();
        this.navigateDealCards(-1);
      } else if (e.key === 's' || e.key === 'S') {
        e.preventDefault();
        this.snipeFocusedDeal();
      } else if (e.key === 't' || e.key === 'T') {
        e.preventDefault();
        this.toggleScraperTerminal();
      } else if (e.key === 'Escape') {
        this.closeAllModals();
      } else if (e.key === '?') {
        e.preventDefault();
        this.showKeyboardShortcutsModal();
      }
    });
  }

  async loadAvailableCategories() {
    try {
      const res = await fetch('/api/searches/categories');
      if (!res.ok) throw new Error('Failed to load categories');
      this.availableCategories = await res.json();
    } catch (err) {
      console.error('Categories load error:', err);
    }
  }

  _getPickerState(containerId) {
    if (!this.categoryPickerState[containerId]) {
      this.categoryPickerState[containerId] = {
        dept: 'All',
        query: '',
        expandedGroups: new Set(['outerwear', 'jumpers_sweaters']),
      };
    }
    return this.categoryPickerState[containerId];
  }

  _autoExpandSelectedGroups(containerId, selectedSet) {
    const state = this._getPickerState(containerId);
    if (!this.availableCategories) return;
    this.availableCategories.forEach(cat => {
      if (selectedSet.has(cat.id) && cat.group_id) {
        state.expandedGroups.add(cat.group_id);
      }
    });
  }

  _syncGroupAllSelections(selectedSet) {
    if (!this.availableCategories || !selectedSet) return;
    this.availableCategories.forEach(cat => {
      if (cat.is_group_all && selectedSet.has(cat.id)) {
        const grpId = cat.group_id || cat.parent;
        this.availableCategories
          .filter(c => (c.group_id || c.parent) === grpId && !c.is_group_all)
          .forEach(sc => selectedSet.add(sc.id));
      }
    });
  }

  renderCategoryChips(containerId, selectedSet, preserveSearchFocus = false) {
    const container = document.getElementById(containerId);
    if (!container) return;

    if (!this.availableCategories || this.availableCategories.length === 0) {
      container.innerHTML = '<div class="text-xs text-slate-500 py-2 text-center">No categories loaded</div>';
      return;
    }

    const state = this._getPickerState(containerId);
    const departments = [
      { id: 'All', label: 'All', icon: 'layers' },
      { id: 'Clothing', label: 'Clothing', icon: 'shirt' },
      { id: 'Shoes', label: 'Shoes', icon: 'footprints' },
      { id: 'Bags', label: 'Bags', icon: 'shopping-bag' },
      { id: 'Accessories', label: 'Accessories', icon: 'watch' },
    ];

    // 1. Selected Breadcrumb Pills Bar (condense group-all selections to single clean pill)
    const allCatGroupIds = new Set(
      Array.from(selectedSet)
        .map(cid => this.availableCategories.find(c => c.id === cid))
        .filter(c => c && c.is_group_all && c.group_id)
        .map(c => c.group_id)
    );

    const selectedCats = Array.from(selectedSet)
      .map(cid => this.availableCategories.find(c => c.id === cid))
      .filter(Boolean)
      .filter(c => {
        if (!c.is_group_all && c.group_id && allCatGroupIds.has(c.group_id)) {
          return false;
        }
        return true;
      });

    const selectedBarHtml = selectedCats.length > 0
      ? `
        <div class="flex flex-wrap items-center gap-1.5 pb-2 border-b border-slate-800/80">
          ${selectedCats.map(cat => `
            <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium bg-teal-500/15 text-teal-200 border border-teal-500/40">
              <span class="text-teal-400/80 text-[10px]">${cat.parent} ›</span>
              <span>${cat.short_name || cat.name}</span>
              <button type="button" onclick="app.toggleCategory('${cat.id}', '${containerId}')" class="ml-0.5 text-teal-300 hover:text-white cursor-pointer" title="Remove category">
                <i data-lucide="x" class="w-3 h-3"></i>
              </button>
            </span>
          `).join('')}
          <button type="button" onclick="app.clearSelectedCategories('${containerId}')" class="ml-auto text-[10px] font-semibold text-slate-400 hover:text-rose-400 transition cursor-pointer px-1.5 py-0.5">
            Clear all
          </button>
        </div>
      `
      : `
        <div class="flex items-center justify-between text-[11px] text-slate-400 pb-1.5 border-b border-slate-800/80">
          <span>No category filter selected (all categories allowed)</span>
          <span class="text-[10px] text-slate-500 font-mono">Select below</span>
        </div>
      `;

    // 2. Department Tabs + Quick Search Input
    const deptCounts = {};
    selectedCats.forEach(c => {
      const d = c.department || 'Clothing';
      deptCounts[d] = (deptCounts[d] || 0) + 1;
    });

    const tabsHtml = `
      <div class="flex flex-wrap items-center gap-1">
        ${departments.map(d => {
          const isActive = state.dept === d.id;
          const count = d.id === 'All' ? selectedCats.length : (deptCounts[d.id] || 0);
          const btnCls = isActive
            ? 'bg-vinted/20 text-teal-200 border-vinted/60 font-semibold'
            : 'bg-slate-900/90 text-slate-400 border-slate-800 hover:text-slate-200 hover:border-slate-700';
          return `
            <button type="button"
                    onclick="app.setCategoryDepartment('${containerId}', '${d.id}')"
                    class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg border text-[11px] transition cursor-pointer ${btnCls}">
              <i data-lucide="${d.icon}" class="w-3 h-3"></i>
              <span>${d.label}</span>
              ${count > 0 ? `<span class="px-1.5 py-0.2 rounded-full text-[9px] font-bold bg-teal-500/30 text-teal-200">${count}</span>` : ''}
            </button>
          `;
        }).join('')}
      </div>
      <div class="relative">
        <i data-lucide="search" class="w-3.5 h-3.5 text-slate-500 absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none"></i>
        <input type="text"
               id="${containerId}-search-input"
               value="${(state.query || '').replace(/"/g, '&quot;')}"
               oninput="app.handleCategorySearchInput('${containerId}', this.value)"
               placeholder="Search Vinted categories (e.g. Puffer, Fleece, Gilet, Quarter-zip, Trainers)…"
               class="w-full bg-slate-900 border border-slate-800 rounded-lg pl-8 pr-7 py-1.5 text-[11px] text-white placeholder:text-slate-500 focus:outline-none focus:border-vinted transition">
        ${state.query ? `
          <button type="button" onclick="app.handleCategorySearchInput('${containerId}', '')" class="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white cursor-pointer">
            <i data-lucide="x" class="w-3 h-3"></i>
          </button>
        ` : ''}
      </div>
    `;

    // 3. Build Vinted Groups -> Subcategories Tree HTML
    const treeHtml = this._buildCategoryTreeHtml(containerId, selectedSet);

    if (preserveSearchFocus) {
      const selectedWrapper = container.querySelector('.vinted-cat-selected-bar');
      const tabsWrapper = container.querySelector('.vinted-cat-tabs-bar');
      const treeWrapper = container.querySelector('.vinted-cat-tree-body');
      if (selectedWrapper && tabsWrapper && treeWrapper) {
        selectedWrapper.innerHTML = selectedBarHtml;
        treeWrapper.innerHTML = treeHtml;
        if (window.lucide) lucide.createIcons();
        return;
      }
    }

    container.innerHTML = `
      <div class="vinted-cat-selected-bar">${selectedBarHtml}</div>
      <div class="vinted-cat-tabs-bar space-y-2">${tabsHtml}</div>
      <div class="vinted-cat-tree-body max-h-56 overflow-y-auto pr-0.5 space-y-1.5">${treeHtml}</div>
    `;

    if (window.lucide) lucide.createIcons();
  }

  _buildCategoryTreeHtml(containerId, selectedSet) {
    const state = this._getPickerState(containerId);
    const q = (state.query || '').trim().toLowerCase();

    // Group categories by group_id preserving order
    const groupsMap = new Map();
    this.availableCategories.forEach(cat => {
      if (state.dept !== 'All' && cat.department !== state.dept) return;
      if (q) {
        const hay = `${cat.name} ${cat.short_name || ''} ${cat.parent} ${cat.department} ${cat.vinted_path || ''}`.toLowerCase();
        if (!hay.includes(q)) return;
      }
      const gid = cat.group_id || cat.parent;
      if (!groupsMap.has(gid)) {
        groupsMap.set(gid, {
          id: gid,
          name: cat.parent,
          department: cat.department || 'Clothing',
          allCat: null,
          subcats: [],
        });
      }
      const grp = groupsMap.get(gid);
      if (cat.is_group_all) {
        grp.allCat = cat;
      } else {
        grp.subcats.push(cat);
      }
    });

    if (groupsMap.size === 0) {
      return `<div class="text-xs text-slate-500 py-4 text-center">No matching Vinted categories found</div>`;
    }

    return Array.from(groupsMap.values()).map(grp => {
      const allSelected = grp.allCat && selectedSet.has(grp.allCat.id);
      const isGroupAllActive = allSelected || (grp.subcats.length > 0 && grp.subcats.every(sc => selectedSet.has(sc.id)));
      const selectedSubCount = grp.subcats.filter(sc => selectedSet.has(sc.id) || isGroupAllActive).length;
      const hasAnySelected = isGroupAllActive || selectedSubCount > 0;
      const isExpanded = Boolean(q) || state.expandedGroups.has(grp.id) || hasAnySelected;

      const headerBorder = hasAnySelected
        ? 'border-teal-500/40 bg-slate-900/95'
        : 'border-slate-800/90 bg-slate-900/60 hover:border-slate-700';

      return `
        <div class="rounded-lg border ${headerBorder} overflow-hidden transition">
          <!-- Vinted Level-2 Group Header -->
          <div class="flex items-center justify-between px-2.5 py-1.5 cursor-pointer select-none"
               onclick="app.toggleCategoryGroupExpand('${containerId}', '${grp.id}')">
            <div class="flex items-center space-x-2 min-w-0">
              <i data-lucide="${isExpanded ? 'chevron-down' : 'chevron-right'}" class="w-3.5 h-3.5 text-slate-400 shrink-0"></i>
              <div class="truncate">
                <span class="text-[10px] font-semibold uppercase tracking-wider text-slate-500 mr-1">${grp.department} ›</span>
                <span class="text-xs font-bold text-slate-200">${grp.name}</span>
              </div>
              ${isGroupAllActive
                ? `<span class="px-1.5 py-0.5 rounded text-[9px] font-bold bg-teal-500/25 text-teal-200 border border-teal-500/40 shrink-0">All Selected</span>`
                : (selectedSubCount > 0
                  ? `<span class="px-1.5 py-0.5 rounded text-[9px] font-bold bg-teal-500/20 text-teal-300 border border-teal-500/30 shrink-0">${selectedSubCount} selected</span>`
                  : `<span class="text-[10px] text-slate-500 font-mono shrink-0">(${grp.subcats.length})</span>`)
              }
            </div>

            ${grp.allCat ? `
              <button type="button"
                      onclick="event.stopPropagation(); app.toggleCategory('${grp.allCat.id}', '${containerId}')"
                      class="px-2 py-0.5 rounded text-[10px] font-semibold border transition cursor-pointer shrink-0 ml-2 ${
                        isGroupAllActive
                          ? 'bg-vinted text-white border-vinted shadow-sm'
                          : 'bg-slate-950 text-slate-300 border-slate-700 hover:border-teal-500/50 hover:text-teal-300'
                      }"
                      title="Select entire ${grp.name} category on Vinted">
                ${isGroupAllActive ? '✓ All ' + grp.name : 'All ' + grp.name}
              </button>
            ` : ''}
          </div>

          <!-- Vinted Level-3 Subcategories List -->
          ${isExpanded && grp.subcats.length > 0 ? `
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-1 p-2 pt-1.5 bg-slate-950/70 border-t border-slate-800/70">
              ${grp.subcats.map(sc => {
                const isSelected = selectedSet.has(sc.id) || isGroupAllActive;
                const itemCls = isSelected
                  ? 'bg-teal-500/20 border-teal-500/70 text-teal-100 font-medium shadow-sm'
                  : 'bg-slate-900/80 border-slate-800/90 text-slate-300 hover:text-white hover:border-slate-700';
                return `
                  <button type="button"
                          onclick="app.toggleCategory('${sc.id}', '${containerId}')"
                          class="flex items-center justify-between px-2.5 py-1.5 rounded-md border text-[11px] transition text-left cursor-pointer ${itemCls}"
                          title="${sc.vinted_path || sc.name}">
                    <div class="flex items-center space-x-2 min-w-0">
                      <span class="w-3.5 h-3.5 rounded flex items-center justify-center border shrink-0 ${
                        isSelected ? 'bg-vinted border-vinted text-white' : 'border-slate-600 bg-slate-950'
                      }">
                        ${isSelected ? '<i data-lucide="check" class="w-2.5 h-2.5"></i>' : ''}
                      </span>
                      <span class="truncate">${sc.name}</span>
                    </div>
                  </button>
                `;
              }).join('')}
            </div>
          ` : ''}
        </div>
      `;
    }).join('');
  }

  setCategoryDepartment(containerId, dept) {
    const state = this._getPickerState(containerId);
    state.dept = dept;
    const targetSet = containerId === 'category-chips-container' ? this.selectedCategories : this.editSelectedCategories;
    this.renderCategoryChips(containerId, targetSet);
  }

  handleCategorySearchInput(containerId, value) {
    const state = this._getPickerState(containerId);
    state.query = value || '';
    const targetSet = containerId === 'category-chips-container' ? this.selectedCategories : this.editSelectedCategories;
    const inputEl = document.getElementById(`${containerId}-search-input`);
    if (inputEl && value === '') {
      inputEl.value = '';
    }
    this.renderCategoryChips(containerId, targetSet, Boolean(document.activeElement && document.activeElement.id === `${containerId}-search-input`));
  }

  toggleCategoryGroupExpand(containerId, groupId) {
    const state = this._getPickerState(containerId);
    if (state.expandedGroups.has(groupId)) {
      state.expandedGroups.delete(groupId);
    } else {
      state.expandedGroups.add(groupId);
    }
    const targetSet = containerId === 'category-chips-container' ? this.selectedCategories : this.editSelectedCategories;
    this.renderCategoryChips(containerId, targetSet);
  }

  clearSelectedCategories(containerId) {
    const targetSet = containerId === 'category-chips-container' ? this.selectedCategories : this.editSelectedCategories;
    targetSet.clear();
    this.renderCategoryChips(containerId, targetSet);
  }

  toggleCategory(catId, containerId) {
    const targetSet = containerId === 'category-chips-container' ? this.selectedCategories : this.editSelectedCategories;
    const clickedCat = this.availableCategories.find(c => c.id === catId);
    if (!clickedCat) return;

    const grpId = clickedCat.group_id || clickedCat.parent;
    const grpCats = this.availableCategories.filter(c => (c.group_id || c.parent) === grpId);
    const allCat = grpCats.find(c => c.is_group_all);
    const subcats = grpCats.filter(c => !c.is_group_all);

    if (clickedCat.is_group_all) {
      // Check if group is currently all selected
      const isCurrentlyAll = (allCat && targetSet.has(allCat.id)) || (subcats.length > 0 && subcats.every(sc => targetSet.has(sc.id)));
      if (isCurrentlyAll) {
        // Untick "All" AND untick all individual items in this category
        if (allCat) targetSet.delete(allCat.id);
        subcats.forEach(sc => targetSet.delete(sc.id));
      } else {
        // Tick "All" AND tick every individual item in this category
        if (allCat) targetSet.add(allCat.id);
        subcats.forEach(sc => targetSet.add(sc.id));
        this._getPickerState(containerId).expandedGroups.add(grpId);
      }
    } else {
      // Individual subcategory clicked
      const isAllOn = allCat && targetSet.has(allCat.id);
      const isCurrentlyTicked = targetSet.has(catId) || isAllOn;

      if (isCurrentlyTicked) {
        // Unticking this individual subcategory
        if (isAllOn) {
          // Break the "All" state: keep all OTHER subcats ticked, remove allCat and this catId
          subcats.forEach(sc => {
            if (sc.id !== catId) targetSet.add(sc.id);
          });
          targetSet.delete(allCat.id);
        }
        targetSet.delete(catId);
      } else {
        // Ticking this individual subcategory
        targetSet.add(catId);
        // If all subcategories in the group are now ticked, automatically activate allCat as well
        if (subcats.length > 0 && subcats.every(sc => targetSet.has(sc.id))) {
          if (allCat) targetSet.add(allCat.id);
        }
      }
      this._getPickerState(containerId).expandedGroups.add(grpId);
    }

    this.renderCategoryChips(containerId, targetSet);
  }

  handleSearchInputDebounced(val) {
    clearTimeout(this.debounceTimer);
    const text = (val || '').trim();
    const badge = document.getElementById('ai-suggestion-badge');
    const badgeText = document.getElementById('ai-suggestion-text');
    const brandInput = document.getElementById('input-brand');

    if (!text || text.length < 2) {
      if (badge) badge.classList.add('hidden');
      return;
    }

    this.debounceTimer = setTimeout(async () => {
      try {
        const res = await fetch('/api/searches/suggest-classification', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: text })
        });
        if (!res.ok) return;
        const data = await res.json();

        if (badge && badgeText) {
          badgeText.textContent = data.explanation || `Detected as ${data.detected_item_type}`;
          badge.classList.remove('hidden');
          if (window.lucide) lucide.createIcons();
        }

        if (data.suggested_brand && brandInput && (!brandInput.value || brandInput.dataset.autoFilled === 'true')) {
          brandInput.value = data.suggested_brand;
          brandInput.dataset.autoFilled = 'true';
        }

        if (data.suggested_categories && data.suggested_categories.length > 0) {
          this.selectedCategories = new Set(data.suggested_categories);
          this._syncGroupAllSelections(this.selectedCategories);
          this._autoExpandSelectedGroups('category-chips-container', this.selectedCategories);
          this.renderCategoryChips('category-chips-container', this.selectedCategories);
        }
      } catch (err) {
        console.error('Error suggesting classification:', err);
      }
    }, 280);
  }

  openAddModal() {
    const modal = document.getElementById('add-modal');
    if (modal) {
      modal.classList.remove('hidden');
      const input = document.getElementById('input-search-text');
      const brandInput = document.getElementById('input-brand');
      const excludeInput = document.getElementById('input-must-exclude');
      const badge = document.getElementById('ai-suggestion-badge');

      if (input) input.value = '';
      if (brandInput) {
        brandInput.value = '';
        brandInput.dataset.autoFilled = 'true';
      }
      if (excludeInput) excludeInput.value = '';
      if (badge) badge.classList.add('hidden');
      const childEl = document.getElementById('add-exclude-child-sizes');
      if (childEl) childEl.checked = false;

      this.selectedCategories = new Set();
      const state = this._getPickerState('category-chips-container');
      state.dept = 'All';
      state.query = '';
      state.expandedGroups = new Set(['outerwear', 'jumpers_sweaters']);
      this.renderCategoryChips('category-chips-container', this.selectedCategories);
      this.handleAddChildToggle(false);

      if (input) input.focus();
    }
  }

  closeAddModal() {
    const modal = document.getElementById('add-modal');
    if (modal) modal.classList.add('hidden');
  }

  openEditModal() {
    if (!this.currentSearchId) return;
    const search = this.searches.find(s => String(s.id) === String(this.currentSearchId));
    if (!search) return;

    document.getElementById('edit-search-text').value = search.search_text || '';
    document.getElementById('edit-brand').value = search.brand || '';
    document.getElementById('edit-must-exclude').value = search.must_exclude || '';

    this.editSelectedCategories = new Set(search.categories || []);
    this._syncGroupAllSelections(this.editSelectedCategories);
    const editState = this._getPickerState('edit-category-chips-container');
    editState.dept = 'All';
    editState.query = '';
    editState.expandedGroups = new Set();
    this._autoExpandSelectedGroups('edit-category-chips-container', this.editSelectedCategories);
    if (editState.expandedGroups.size === 0) {
      editState.expandedGroups.add('outerwear');
    }
    this.renderCategoryChips('edit-category-chips-container', this.editSelectedCategories);

    // Populate Anti-Rep Filters
    const arc = search.antirep_config || search.antirep_settings || {
      antirep_enabled: true,
      block_new_with_tags: true,
      block_no_reviews: true,
      min_seller_reviews: 1,
      block_multiple_seller_listings: true,
      max_seller_listings: 2,
      block_duplicate_photos: true,
      block_dormant_accounts: false,
      dormant_account_days: 90
    };

    const enabledEl = document.getElementById('edit-antirep-enabled');
    if (enabledEl) enabledEl.checked = arc.antirep_enabled !== false;

    const bnwtEl = document.getElementById('edit-antirep-block-bnwt');
    if (bnwtEl) bnwtEl.checked = arc.block_new_with_tags !== false;

    const noReviewsEl = document.getElementById('edit-antirep-block-no-reviews');
    if (noReviewsEl) noReviewsEl.checked = arc.block_no_reviews !== false;

    const minRevEl = document.getElementById('edit-antirep-min-reviews');
    if (minRevEl) minRevEl.value = arc.min_seller_reviews || 1;

    const multEl = document.getElementById('edit-antirep-block-multiple');
    if (multEl) multEl.checked = arc.block_multiple_seller_listings !== false;

    const maxListEl = document.getElementById('edit-antirep-max-seller-listings');
    if (maxListEl) maxListEl.value = arc.max_seller_listings || 2;

    const dupEl = document.getElementById('edit-antirep-block-duplicate-photos');
    if (dupEl) dupEl.checked = arc.block_duplicate_photos !== false;

    const dormEl = document.getElementById('edit-antirep-block-dormant');
    if (dormEl) dormEl.checked = arc.block_dormant_accounts === true;

    const dormDaysEl = document.getElementById('edit-antirep-dormant-days');
    if (dormDaysEl) dormDaysEl.value = arc.dormant_account_days || 90;

    this.handleEditChildToggle(Boolean(search.exclude_child_sizes));

    this.toggleEditAntiRepFields(arc.antirep_enabled !== false);

    document.getElementById('edit-modal').classList.remove('hidden');
    if (window.lucide) lucide.createIcons();
  }

  handleEditChildToggle(isChecked) {
    const input = document.getElementById('edit-exclude-child-sizes');
    const badge = document.getElementById('edit-child-status-badge');
    const card = document.getElementById('edit-child-card');
    const checked = Boolean(isChecked);
    if (input) input.checked = checked;
    if (badge) {
      if (checked) {
        badge.textContent = 'EXCLUDED (ON)';
        badge.className = 'px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40';
      } else {
        badge.textContent = 'ALLOWED (OFF)';
        badge.className = 'px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-800 text-slate-400 border border-slate-700';
      }
    }
    if (card) {
      if (checked) {
        card.classList.add('border-amber-500/50', 'bg-amber-950/20');
        card.classList.remove('border-slate-800', 'bg-slate-950/90');
      } else {
        card.classList.remove('border-amber-500/50', 'bg-amber-950/20');
        card.classList.add('border-slate-800', 'bg-slate-950/90');
      }
    }
  }

  toggleEditChildSizes() {
    const input = document.getElementById('edit-exclude-child-sizes');
    const current = input ? input.checked : false;
    this.handleEditChildToggle(!current);
  }

  handleAddChildToggle(isChecked) {
    const input = document.getElementById('add-exclude-child-sizes');
    const badge = document.getElementById('add-child-status-badge');
    const card = document.getElementById('add-child-card');
    const checked = Boolean(isChecked);
    if (input) input.checked = checked;
    if (badge) {
      if (checked) {
        badge.textContent = 'EXCLUDED (ON)';
        badge.className = 'px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40';
      } else {
        badge.textContent = 'ALLOWED (OFF)';
        badge.className = 'px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-800 text-slate-400 border border-slate-700';
      }
    }
    if (card) {
      if (checked) {
        card.classList.add('border-amber-500/50', 'bg-amber-950/20');
        card.classList.remove('border-slate-800', 'bg-slate-950/90');
      } else {
        card.classList.remove('border-amber-500/50', 'bg-amber-950/20');
        card.classList.add('border-slate-800', 'bg-slate-950/90');
      }
    }
  }

  toggleAddChildSizes() {
    const input = document.getElementById('add-exclude-child-sizes');
    const current = input ? input.checked : false;
    this.handleAddChildToggle(!current);
  }

  toggleEditAntiRepFields(enabled) {
    const fields = document.getElementById('edit-antirep-fields');
    if (!fields) return;
    if (enabled) {
      fields.classList.remove('opacity-40', 'pointer-events-none');
    } else {
      fields.classList.add('opacity-40', 'pointer-events-none');
    }
  }

  closeEditModal() {
    const modal = document.getElementById('edit-modal');
    if (modal) modal.classList.add('hidden');
  }

  async handleUpdateSearch(e) {
    if (e) e.preventDefault();
    if (!this.currentSearchId) return;

    const searchText = document.getElementById('edit-search-text').value.trim();
    const brand = document.getElementById('edit-brand').value.trim();
    const mustExclude = document.getElementById('edit-must-exclude').value.trim();
    const categories = Array.from(this.editSelectedCategories);
    const excludeChildSizes = Boolean(document.getElementById('edit-exclude-child-sizes')?.checked);

    const antirepSettings = {
      antirep_enabled: document.getElementById('edit-antirep-enabled')?.checked ?? true,
      block_new_with_tags: document.getElementById('edit-antirep-block-bnwt')?.checked ?? true,
      block_no_reviews: document.getElementById('edit-antirep-block-no-reviews')?.checked ?? true,
      min_seller_reviews: parseInt(document.getElementById('edit-antirep-min-reviews')?.value, 10) || 1,
      block_multiple_seller_listings: document.getElementById('edit-antirep-block-multiple')?.checked ?? true,
      max_seller_listings: parseInt(document.getElementById('edit-antirep-max-seller-listings')?.value, 10) || 2,
      block_duplicate_photos: document.getElementById('edit-antirep-block-duplicate-photos')?.checked ?? true,
      block_dormant_accounts: document.getElementById('edit-antirep-block-dormant')?.checked ?? false,
      dormant_account_days: parseInt(document.getElementById('edit-antirep-dormant-days')?.value, 10) || 90
    };

    const saveBtn = document.getElementById('btn-save-filters');
    const origText = saveBtn ? saveBtn.innerHTML : '';
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.innerHTML = '<i data-lucide="loader-2" class="w-3.5 h-3.5 mr-1.5 animate-spin"></i> Applying Filters…';
      if (window.lucide) lucide.createIcons();
    }

    try {
      const res = await fetch(`/api/searches/${this.currentSearchId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          search_text: searchText,
          brand: brand,
          categories: categories,
          must_exclude: mustExclude,
          exclude_child_sizes: excludeChildSizes,
          antirep_settings: antirepSettings
        })
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to update search filters');
      }

      const data = await res.json();
      this.closeEditModal();

      const search = this.searches.find(s => String(s.id) === String(this.currentSearchId));
      if (search) {
        search.search_text = searchText;
        search.brand = brand;
        search.categories = categories;
        search.must_exclude = mustExclude;
        search.exclude_child_sizes = excludeChildSizes;
        search.antirep_settings = antirepSettings;
        search.antirep_config = antirepSettings;
      }

      if (data.purged_count > 0) {
        this.showToast(`Saved filters & pruned ${data.purged_count} non-compliant items (${data.remaining_count} verified genuine items remain).`, 'success');
      } else {
        this.showToast('Saved filters! Brand, category & size rules active.', 'success');
      }

      await this.loadSearches();
      await this.loadItemDetail();
    } catch (err) {
      this.showToast(err.message, 'error');
    } finally {
      if (saveBtn) {
        saveBtn.disabled = false;
        saveBtn.innerHTML = origText;
        if (window.lucide) lucide.createIcons();
      }
    }
  }

  showToast(message, type = 'info', resolution = null) {
    const toast = document.getElementById('toast');
    const toastTitle = document.getElementById('toast-title');
    const toastMsg = document.getElementById('toast-message');
    const toastIcon = document.getElementById('toast-icon');
    const resBox = document.getElementById('toast-resolution-container');
    const resText = document.getElementById('toast-resolution');

    if (!toast) return;

    if (this.toastTimer) {
      clearTimeout(this.toastTimer);
      this.toastTimer = null;
    }

    if (toastMsg) toastMsg.textContent = message;

    // Automatic diagnostic resolutions for common error patterns
    if (type === 'error' && !resolution) {
      const lower = (message || '').toLowerCase();
      if (lower.includes('rate limit') || lower.includes('429')) {
        resolution = 'Vinted rate limit hit: Wait 60–90 seconds for cooldown, or restart router/VPN for a fresh IP.';
      } else if (lower.includes('address already in use') || lower.includes('errno 48')) {
        resolution = 'Port 8000 is occupied: Another server instance is running. Terminate it via terminal or restart session.';
      } else if (lower.includes('failed to fetch') || lower.includes('connection refused')) {
        resolution = 'Backend server unreachable: Ensure FastAPI is actively running on http://localhost:8000.';
      } else if (lower.includes('replica') || lower.includes('blocked')) {
        resolution = 'Listings filtered: Open "Edit Filters" to relax BNWT, seller reviews, or duplicate photo thresholds.';
      } else if (lower.includes('not found') || lower.includes('no items')) {
        resolution = 'No items found: Check search spelling, or verify brand and category selections in Edit Filters.';
      }
    }

    if (resBox && resText) {
      if (resolution) {
        resText.textContent = resolution;
        resBox.classList.remove('hidden');
      } else {
        resBox.classList.add('hidden');
      }
    }

    // Color schemes & titles
    toast.className = 'fixed bottom-6 right-6 z-50 transition-all duration-300 max-w-md p-4 rounded-2xl shadow-2xl border text-xs font-medium space-y-2 backdrop-blur-md ';
    if (type === 'success') {
      toast.classList.add('bg-teal-950/95', 'border-teal-500/50', 'text-teal-200');
      if (toastTitle) toastTitle.textContent = 'Success';
      if (toastIcon) toastIcon.setAttribute('data-lucide', 'check-circle-2');
    } else if (type === 'error') {
      toast.classList.add('bg-rose-950/95', 'border-rose-500/50', 'text-rose-200');
      if (toastTitle) toastTitle.textContent = 'Action Required';
      if (toastIcon) toastIcon.setAttribute('data-lucide', 'alert-triangle');
    } else {
      toast.classList.add('bg-slate-900/95', 'border-slate-700', 'text-slate-200');
      if (toastTitle) toastTitle.textContent = 'Information';
      if (toastIcon) toastIcon.setAttribute('data-lucide', 'info');
    }

    toast.classList.remove('translate-y-20', 'opacity-0');
    toast.classList.add('translate-y-0', 'opacity-100');
    if (window.lucide) lucide.createIcons();

    // Errors stay for 12 seconds so users can read resolution; success/info stay for 4.5s
    const duration = type === 'error' ? 12000 : 4500;
    this.toastTimer = setTimeout(() => {
      this.dismissToast();
    }, duration);
  }

  dismissToast() {
    const toast = document.getElementById('toast');
    if (!toast) return;
    if (this.toastTimer) {
      clearTimeout(this.toastTimer);
      this.toastTimer = null;
    }
    toast.classList.remove('translate-y-0', 'opacity-100');
    toast.classList.add('translate-y-20', 'opacity-0');
  }

  // ── Search Management ───────────────────────────────────────────────────

  async loadSearches() {
    try {
      const res = await fetch('/api/searches');
      if (!res.ok) throw new Error('Failed to load searches');
      this.searches = await res.json();
      this.renderSidebar();
    } catch (err) {
      console.error(err);
      this.showToast('Could not load tracked searches', 'error');
    }
  }

  filterSidebarItems(query) {
    this.sidebarFilterQuery = (query || '').toLowerCase().trim();
    this.renderSidebar();
  }

  renderSidebar() {
    const listEl = document.getElementById('tracked-searches-list');
    const badgeEl = document.getElementById('search-count-badge');
    if (badgeEl) badgeEl.textContent = this.searches.length;

    const sniperBtn = document.getElementById('nav-sniper-btn');
    const dbBtn = document.getElementById('nav-db-explorer-btn');

    if (this.isDbExplorerActive) {
      if (sniperBtn) sniperBtn.classList.remove('active');
      if (dbBtn) dbBtn.classList.add('active');
    } else if (this.currentSearchId) {
      if (sniperBtn) sniperBtn.classList.remove('active');
      if (dbBtn) dbBtn.classList.remove('active');
    } else {
      if (sniperBtn) sniperBtn.classList.add('active');
      if (dbBtn) dbBtn.classList.remove('active');
    }

    let filteredSearches = this.searches;
    if (this.sidebarFilterQuery) {
      filteredSearches = this.searches.filter(s => 
        (s.search_text || '').toLowerCase().includes(this.sidebarFilterQuery) ||
        (s.brand || '').toLowerCase().includes(this.sidebarFilterQuery)
      );
    }

    if (filteredSearches.length === 0) {
      listEl.innerHTML = this.searches.length === 0
        ? '<div class="text-xs text-slate-500 px-3 py-4 text-center">No items tracked yet.</div>'
        : '<div class="text-xs text-slate-500 px-3 py-4 text-center">No matching searches found.</div>';
      return;
    }

    listEl.innerHTML = filteredSearches.map(s => {
      const isActive = !this.isDbExplorerActive && String(s.id) === String(this.currentSearchId);
      return `
        <div class="group relative">
          <button onclick="app.selectSearch(${s.id})" 
                  class="sidebar-item w-full flex items-center justify-between px-3 py-2.5 rounded-xl text-xs font-semibold text-slate-300 hover:bg-slate-800/90 hover:text-white transition ${isActive ? 'active' : ''}">
            <div class="flex items-center space-x-2.5 truncate">
              <div class="w-6 h-6 rounded-lg bg-slate-800/90 border border-slate-700/60 flex items-center justify-center text-slate-400 group-hover:text-vinted group-hover:border-vinted/40 shrink-0 transition">
                <i data-lucide="tag" class="w-3 h-3"></i>
              </div>
              <div class="truncate text-left">
                <span class="truncate block text-slate-200 group-hover:text-white">${s.search_text}</span>
                ${s.brand ? `<span class="text-[10px] text-slate-500 block truncate">${s.brand}</span>` : ''}
              </div>
            </div>
            <div class="flex items-center space-x-1.5 shrink-0">
              <span class="px-2 py-0.5 text-[10px] font-mono font-bold rounded-full bg-slate-800 text-slate-300 border border-slate-700/80">${s.item_count}</span>
            </div>
          </button>
        </div>
      `;
    }).join('');

    const mobListEl = document.getElementById('mobile-tracked-searches-list');
    if (mobListEl) {
      mobListEl.innerHTML = listEl.innerHTML;
    }

    const mobSniper = document.getElementById('mob-nav-sniper');
    const mobDb = document.getElementById('mob-nav-db');
    if (mobSniper && mobDb) {
      if (this.isDbExplorerActive) {
        mobSniper.className = 'mobile-nav-btn flex flex-col items-center justify-center text-slate-400 hover:text-teal-400 p-1 min-h-[44px] min-w-[56px] transition cursor-pointer';
        mobDb.className = 'mobile-nav-btn flex flex-col items-center justify-center text-indigo-400 p-1 min-h-[44px] min-w-[56px] transition cursor-pointer';
      } else if (!this.currentSearchId) {
        mobSniper.className = 'mobile-nav-btn flex flex-col items-center justify-center text-teal-400 p-1 min-h-[44px] min-w-[56px] transition cursor-pointer';
        mobDb.className = 'mobile-nav-btn flex flex-col items-center justify-center text-slate-400 hover:text-white p-1 min-h-[44px] min-w-[56px] transition cursor-pointer';
      } else {
        mobSniper.className = 'mobile-nav-btn flex flex-col items-center justify-center text-slate-400 hover:text-teal-400 p-1 min-h-[44px] min-w-[56px] transition cursor-pointer';
        mobDb.className = 'mobile-nav-btn flex flex-col items-center justify-center text-slate-400 hover:text-white p-1 min-h-[44px] min-w-[56px] transition cursor-pointer';
      }
    }

    if (window.lucide) lucide.createIcons();
  }

  async handleCreateSearch(e) {
    e.preventDefault();
    const input = document.getElementById('input-search-text');
    const brandInput = document.getElementById('input-brand');
    const excludeInput = document.getElementById('input-must-exclude');

    const searchText = input.value.trim();
    if (!searchText) return;

    const brand = brandInput ? brandInput.value.trim() : '';
    const mustExclude = excludeInput ? excludeInput.value.trim() : '';
    const categories = Array.from(this.selectedCategories);

    const antirepEnabled = document.getElementById('add-antirep-enabled')?.checked ?? true;
    const antirepSettings = {
      antirep_enabled: antirepEnabled,
      block_new_with_tags: document.getElementById('add-antirep-block-bnwt')?.checked ?? true,
      block_no_reviews: document.getElementById('add-antirep-block-no-reviews')?.checked ?? true,
      min_seller_reviews: 1,
      block_multiple_seller_listings: true,
      max_seller_listings: 2,
      block_duplicate_photos: document.getElementById('add-antirep-block-duplicate-photos')?.checked ?? true,
      block_dormant_accounts: false,
      dormant_account_days: 90
    };

    const excludeChildSizes = Boolean(document.getElementById('add-exclude-child-sizes')?.checked);

    const submitBtn = e.target.querySelector('button[type="submit"]');
    const origText = submitBtn ? submitBtn.innerHTML : '';
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.innerHTML = '<i data-lucide="loader-2" class="w-3.5 h-3.5 mr-1.5 animate-spin"></i> Adding...';
      if (window.lucide) lucide.createIcons();
    }

    try {
      const res = await fetch('/api/searches', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          search_text: searchText,
          brand: brand || null,
          categories: categories.length > 0 ? categories : null,
          must_exclude: mustExclude || null,
          exclude_child_sizes: excludeChildSizes,
          antirep_settings: antirepSettings
        })
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to add search');
      }

      const newSearch = await res.json();
      this.closeAddModal();
      this.showToast(`Added '${searchText}' with brand, category & Anti-Rep filters!`, 'success');
      await this.loadSearches();
      this.selectSearch(newSearch.id);
    } catch (err) {
      this.showToast(err.message, 'error');
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.innerHTML = origText;
        if (window.lucide) lucide.createIcons();
      }
    }
  }

  async deleteCurrentItem() {
    if (!this.currentSearchId) return;
    const search = this.searches.find(s => String(s.id) === String(this.currentSearchId));
    if (!confirm(`Are you sure you want to stop tracking '${search?.search_text}' and delete its data?`)) {
      return;
    }

    try {
      const res = await fetch(`/api/searches/${this.currentSearchId}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('Failed to delete search');

      this.showToast(`Removed '${search?.search_text}'`, 'info');
      await this.loadSearches();
      this.showSniperView();
    } catch (err) {
      this.showToast(err.message, 'error');
    }
  }

  // ── Views & Switching ───────────────────────────────────────────────────

  showSniperView() {
    this.currentSearchId = null;
    this.isDbExplorerActive = false;

    const sniperBtn = document.getElementById('nav-sniper-btn');
    if (sniperBtn) sniperBtn.classList.add('active');
    const dbBtn = document.getElementById('nav-db-explorer-btn');
    if (dbBtn) dbBtn.classList.remove('active');
    
    const itemDetail = document.getElementById('item-detail-view');
    if (itemDetail) itemDetail.classList.add('hidden');
    const dbView = document.getElementById('database-explorer-view');
    if (dbView) dbView.classList.add('hidden');
    
    const sniperView = document.getElementById('sniper-view');
    if (sniperView) sniperView.classList.remove('hidden');

    this.renderSidebar();
    this.renderSniperTargets();
    if (window.lucide) lucide.createIcons();
  }

  async selectSearch(id) {
    this.currentSearchId = id;
    this.isDbExplorerActive = false;

    const sniperBtn = document.getElementById('nav-sniper-btn');
    if (sniperBtn) sniperBtn.classList.remove('active');
    const dbBtn = document.getElementById('nav-db-explorer-btn');
    if (dbBtn) dbBtn.classList.remove('active');

    const sniperView = document.getElementById('sniper-view');
    if (sniperView) sniperView.classList.add('hidden');
    const dbView = document.getElementById('database-explorer-view');
    if (dbView) dbView.classList.add('hidden');

    const itemDetail = document.getElementById('item-detail-view');
    if (itemDetail) itemDetail.classList.remove('hidden');

    this.renderSidebar();
    await this.loadItemDetail();
  }

  async showDatabaseExplorerView() {
    this.currentSearchId = null;
    this.isDbExplorerActive = true;

    const sniperBtn = document.getElementById('nav-sniper-btn');
    if (sniperBtn) sniperBtn.classList.remove('active');
    const dbBtn = document.getElementById('nav-db-explorer-btn');
    if (dbBtn) dbBtn.classList.add('active');

    const itemDetail = document.getElementById('item-detail-view');
    if (itemDetail) itemDetail.classList.add('hidden');
    const sniperView = document.getElementById('sniper-view');
    if (sniperView) sniperView.classList.add('hidden');

    const dbView = document.getElementById('database-explorer-view');
    if (dbView) dbView.classList.remove('hidden');

    this.renderSidebar();
    await this.loadDatabaseExplorerData();
    if (window.lucide) lucide.createIcons();
  }

  setPeriod(period) {
    this.currentPeriod = period;
    
    // Update active UI buttons
    document.querySelectorAll('#period-toggles .filter-btn').forEach(btn => {
      if (btn.dataset.period === period) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });

    const labels = { day: 'DAILY', week: 'WEEKLY', month: 'MONTHLY', year: 'ANNUAL' };
    document.getElementById('chart1-period-badge').textContent = labels[period];
    document.getElementById('chart2-period-badge').textContent = labels[period];

    if (this.currentSearchId) {
      this.loadAnalyticsCharts();
    }
  }

  // ── Item Actions: Scrape ───────────────────────────────────────────────

  async scrapeCurrentItem() {
    if (!this.currentSearchId) return;
    const btn = document.getElementById('btn-scrape-now');
    const textEl = document.getElementById('scrape-btn-text');
    let icon = btn.querySelector('#scrape-icon') || btn.querySelector('svg') || btn.querySelector('i');
    
    btn.disabled = true;
    btn.classList.add('opacity-75', 'cursor-wait');
    if (icon) icon.classList.add('animate-spin');
    if (textEl) textEl.textContent = 'Scraping Vinted…';
    this.openScraperTerminal();
    this.showToast('Connecting to Vinted.co.uk and fetching listings…', 'info');

    try {
      const res = await fetch(`/api/searches/${this.currentSearchId}/refresh`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Scrape request failed');
      }
      
      let msg = `Scrape complete! Stored ${data.new_items} new listings.`;
      if (data.total_blocked > 0) {
        const blockedParts = [];
        if (data.filtered_antirep > 0) blockedParts.push(`${data.filtered_antirep} replica`);
        if (data.filtered_spam > 0) blockedParts.push(`${data.filtered_spam} off-brand`);
        msg = `Scrape complete: stored ${data.new_items} new items (${blockedParts.join(', ')} blocked).`;
      }
      this.showToast(msg, 'success');

      await this.loadSearches();
      await this.loadItemDetail();
    } catch (err) {
      this.showToast(err.message || 'Vinted rate limit or connection issue. Try again in a moment.', 'error');
    } finally {
      btn.disabled = false;
      btn.classList.remove('opacity-75', 'cursor-wait');
      const curIcon = btn.querySelector('#scrape-icon') || btn.querySelector('svg') || btn.querySelector('i');
      if (curIcon) curIcon.classList.remove('animate-spin');
      if (textEl) textEl.textContent = 'Scrape Vinted (.co.uk)';
      if (window.lucide) lucide.createIcons();
    }
  }

  // ── Detailed Item View Loader ──────────────────────────────────────────

  async loadItemDetail() {
    const search = this.searches.find(s => String(s.id) === String(this.currentSearchId));
    if (!search) return;

    document.getElementById('item-title').textContent = search.search_text;
    this.renderFilterBadges(search);

    const isExcluded = Boolean(search.exclude_child_sizes);

    // 1-Click Child Quick Toggle Button
    const toggleChildBtn = document.getElementById('btn-toggle-child-filter');
    const toggleChildText = document.getElementById('btn-toggle-child-filter-text');
    if (toggleChildBtn && toggleChildText) {
      if (isExcluded) {
        toggleChildBtn.className = 'inline-flex items-center px-3 py-1.5 rounded-lg text-xs font-semibold bg-amber-500/15 hover:bg-amber-500/25 text-amber-300 border border-amber-500/40 transition cursor-pointer shadow-sm';
        toggleChildText.textContent = 'No Kids: ON';
      } else {
        toggleChildBtn.className = 'inline-flex items-center px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-900 hover:bg-slate-800 text-slate-400 border border-slate-800 hover:border-slate-700 transition cursor-pointer';
        toggleChildText.textContent = 'No Kids: OFF';
      }
    }

    // Live Status Pill on "Edit Filters" Button
    const editChildPill = document.getElementById('btn-edit-child-pill');
    if (editChildPill) {
      if (isExcluded) {
        editChildPill.className = 'ml-2 px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30 inline-flex items-center gap-1';
        editChildPill.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-amber-400 inline-block animate-pulse"></span>No Kids';
      } else {
        editChildPill.className = 'ml-2 px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-800 text-slate-400 border border-slate-700';
        editChildPill.textContent = 'All Sizes';
      }
    }

    // Fetch summary stats, analytics charts, and recent sales
    await Promise.all([
      this.loadSummaryStats(),
      this.loadAnalyticsCharts(),
      this.loadRecentSales()
    ]);
    if (window.lucide) lucide.createIcons();
  }

  async toggleCurrentItemChildFilter() {
    if (!this.currentSearchId) return;
    try {
      const res = await fetch(`/api/searches/${this.currentSearchId}/toggle-child-filter`, { method: 'POST' });
      if (!res.ok) throw new Error('Failed to toggle child sizes filter');
      const data = await res.json();

      const search = this.searches.find(s => String(s.id) === String(this.currentSearchId));
      if (search) {
        search.exclude_child_sizes = Boolean(data.exclude_child_sizes);
      }

      this.showToast(data.message || 'Child size filter updated', 'success');
      await this.loadSearches();
      await this.loadItemDetail();
    } catch (err) {
      this.showToast(err.message, 'error');
    }
  }

  renderFilterBadges(search) {
    const container = document.getElementById('item-filters-badge-row');
    if (!container) return;

    const badges = [];

    // Brand Badge
    if (search.brand) {
      badges.push(`
        <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-teal-500/10 text-teal-300 border border-teal-500/30">
          <i data-lucide="tag" class="w-3 h-3 mr-1 text-teal-400"></i>
          Brand: <strong class="ml-1 text-white">${search.brand}</strong>
        </span>
      `);
    }

    // Category Badges
    if (search.categories && search.categories.length > 0) {
      search.categories.forEach(cid => {
        const catInfo = this.availableCategories.find(c => c.id === cid);
        const label = catInfo
          ? (catInfo.is_group_all
              ? `${catInfo.department} › ${catInfo.short_name || catInfo.name}`
              : `${catInfo.parent} › ${catInfo.short_name || catInfo.name}`)
          : cid;
        badges.push(`
          <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-indigo-500/10 text-indigo-300 border border-indigo-500/30" title="${catInfo ? (catInfo.vinted_path || catInfo.name) : cid}">
            <i data-lucide="folder-tree" class="w-3 h-3 mr-1 text-indigo-400"></i>
            ${label}
          </span>
        `);
      });
    }

    // Exclusion Badge
    if (search.must_exclude) {
      badges.push(`
        <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-rose-500/10 text-rose-300 border border-rose-500/30">
          <i data-lucide="ban" class="w-3 h-3 mr-1 text-rose-400"></i>
          Exclude: ${search.must_exclude}
        </span>
      `);
    }

    // Anti-Rep Badges
    const arc = search.antirep_config || search.antirep_settings;
    if (arc && arc.antirep_enabled !== false) {
      badges.push(`
        <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-500/10 text-emerald-300 border border-emerald-500/30">
          <i data-lucide="shield-check" class="w-3 h-3 mr-1 text-emerald-400"></i>
          Anti-Rep Active
        </span>
      `);
      if (arc.block_new_with_tags !== false) {
        badges.push(`
          <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] bg-slate-800 text-slate-300 border border-slate-700">
            🚫 No BNWT
          </span>
        `);
      }
      if (arc.block_no_reviews !== false) {
        badges.push(`
          <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] bg-slate-800 text-slate-300 border border-slate-700">
            ⭐ Min ${arc.min_seller_reviews || 1} Review${(arc.min_seller_reviews || 1) > 1 ? 's' : ''}
          </span>
        `);
      }
      if (arc.block_duplicate_photos !== false) {
        badges.push(`
          <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] bg-slate-800 text-slate-300 border border-slate-700">
            📸 Anti-Stock Photo
          </span>
        `);
      }
      if (arc.block_multiple_seller_listings !== false) {
        badges.push(`
          <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] bg-slate-800 text-slate-300 border border-slate-700">
            📦 Max ${arc.max_seller_listings || 2}/Seller
          </span>
        `);
      }
      if (arc.block_dormant_accounts === true) {
        badges.push(`
          <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] bg-slate-800 text-slate-300 border border-slate-700">
            ⏳ Dormancy &lt;${arc.dormant_account_days || 90}d
          </span>
        `);
      }
    }

    // Child Sizes Excluded Badge
    if (search.exclude_child_sizes) {
      badges.push(`
        <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-amber-500/10 text-amber-300 border border-amber-500/30">
          <i data-lucide="baby" class="w-3 h-3 mr-1 text-amber-400"></i>
          Child Sizes Excluded
        </span>
      `);
    }

    if (badges.length === 0) {
      badges.push(`
        <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-[11px] text-slate-400 bg-slate-900 border border-slate-800">
          No filters active (all items allowed)
        </span>
      `);
    }

    container.innerHTML = badges.join('');
    if (window.lucide) lucide.createIcons();
  }


  async loadSummaryStats() {
    try {
      const res = await fetch(`/api/analytics/${this.currentSearchId}/summary`);
      if (!res.ok) throw new Error('Failed to load summary');
      const s = await res.json();

      document.getElementById('stat-total-count').textContent = s.total_items.toLocaleString();
      
      // Card 2: Average Price Sold (strictly from sold items, or £0.00 if no sales yet)
      const avgPriceSold = s.sold_count > 0 ? s.sold_avg_price : 0.0;
      document.getElementById('stat-avg-price').textContent = `£${avgPriceSold.toFixed(2)}`;
      
      // Update subtext under Average Price Sold
      const avgSubEl = document.querySelector('#stat-avg-price + div');
      if (avgSubEl) {
        avgSubEl.textContent = s.sold_count > 0 
          ? `Based on ${s.sold_count.toLocaleString()} verified sale${s.sold_count === 1 ? '' : 's'}`
          : '0 verified sales recorded';
      }

      // Card 3: Price range of sold items (or min-max observed)
      document.getElementById('stat-price-range').textContent = s.sold_count > 0 
        ? `£${s.min_price.toFixed(0)} - £${s.max_price.toFixed(0)}` 
        : (s.total_items > 0 ? `£${s.min_price.toFixed(0)} - £${s.max_price.toFixed(0)}` : '£0 - £0');
      
      // Card 4: Gross Volume Value (cumulative sales total)
      const gross = s.sold_gross_value !== undefined ? s.sold_gross_value : (s.sold_count * avgPriceSold);
      document.getElementById('stat-gross-value').textContent = `£${gross.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
      
      const grossSubEl = document.querySelector('#stat-gross-value + div');
      if (grossSubEl) {
        grossSubEl.textContent = `${s.sold_count.toLocaleString()} sold items cumulative`;
      }
    } catch (err) {
      console.error(err);
    }
  }

  async loadAnalyticsCharts() {
    try {
      const res = await fetch(`/api/analytics/${this.currentSearchId}/sales-over-time?period=${this.currentPeriod}`);
      if (!res.ok) throw new Error('Failed to load time series data');
      const data = await res.json();

      this.renderTimeSeriesCharts(data.data);
    } catch (err) {
      console.error(err);
    }
  }

  renderTimeSeriesCharts(series) {
    const labels = series.map(d => d.period_label);
    const counts = series.map(d => d.count);
    const avgPrices = series.map(d => d.avg_price);
    const minPrices = series.map(d => d.min_price);
    const maxPrices = series.map(d => d.max_price);

    // 1. Sales Quantity Chart
    const ctx1 = document.getElementById('salesQuantityChart').getContext('2d');
    if (this.salesQuantityChart) this.salesQuantityChart.destroy();

    const tealGradient = ctx1.createLinearGradient(0, 0, 0, 300);
    tealGradient.addColorStop(0, 'rgba(9, 177, 186, 0.85)');
    tealGradient.addColorStop(1, 'rgba(9, 177, 186, 0.15)');

    this.salesQuantityChart = new Chart(ctx1, {
      type: 'bar',
      data: {
        labels: labels.length ? labels : ['No Data'],
        datasets: [{
          label: 'Items Sold',
          data: counts.length ? counts : [0],
          backgroundColor: tealGradient,
          borderColor: '#09b1ba',
          borderWidth: 1.5,
          borderRadius: 6,
          barPercentage: 0.6,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: '#0f172a',
            titleColor: '#f8fafc',
            bodyColor: '#38cbd4',
            borderColor: '#334155',
            borderWidth: 1,
            padding: 10,
            callbacks: {
              label: (ctx) => ` Sales Count: ${ctx.parsed.y} items`
            }
          }
        },
        scales: {
          x: {
            grid: { color: 'rgba(255, 255, 255, 0.05)' },
            ticks: { color: '#94a3b8', font: { size: 11 } }
          },
          y: {
            beginAtZero: true,
            grid: { color: 'rgba(255, 255, 255, 0.05)' },
            ticks: { color: '#94a3b8', font: { size: 11 }, precision: 0 }
          }
        }
      }
    });

    // 2. Average Price & Corridor Chart
    const ctx2 = document.getElementById('avgPriceChart').getContext('2d');
    if (this.avgPriceChart) this.avgPriceChart.destroy();

    const priceGradient = ctx2.createLinearGradient(0, 0, 0, 300);
    priceGradient.addColorStop(0, 'rgba(16, 185, 129, 0.3)');
    priceGradient.addColorStop(1, 'rgba(16, 185, 129, 0.0)');

    this.avgPriceChart = new Chart(ctx2, {
      type: 'line',
      data: {
        labels: labels.length ? labels : ['No Data'],
        datasets: [
          {
            label: 'Avg Sold Price (£)',
            data: avgPrices.length ? avgPrices : [0],
            borderColor: '#10b981',
            backgroundColor: priceGradient,
            fill: true,
            tension: 0.35,
            borderWidth: 3,
            pointBackgroundColor: '#10b981',
            pointRadius: 4,
            pointHoverRadius: 6,
          },
          {
            label: 'Max Price (£)',
            data: maxPrices.length ? maxPrices : [0],
            borderColor: 'rgba(148, 163, 184, 0.4)',
            borderDash: [4, 4],
            borderWidth: 1.5,
            fill: false,
            pointRadius: 0,
            tension: 0.35
          },
          {
            label: 'Min Price (£)',
            data: minPrices.length ? minPrices : [0],
            borderColor: 'rgba(148, 163, 184, 0.4)',
            borderDash: [4, 4],
            borderWidth: 1.5,
            fill: false,
            pointRadius: 0,
            tension: 0.35
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            display: true,
            position: 'top',
            labels: { color: '#94a3b8', boxWidth: 12, font: { size: 11 } }
          },
          tooltip: {
            backgroundColor: '#0f172a',
            borderColor: '#334155',
            borderWidth: 1,
            padding: 10,
            callbacks: {
              label: (ctx) => ` ${ctx.dataset.label}: £${ctx.parsed.y.toFixed(2)}`
            }
          }
        },
        scales: {
          x: {
            grid: { color: 'rgba(255, 255, 255, 0.05)' },
            ticks: { color: '#94a3b8', font: { size: 11 } }
          },
          y: {
            beginAtZero: false,
            grid: { color: 'rgba(255, 255, 255, 0.05)' },
            ticks: {
              color: '#94a3b8',
              font: { size: 11 },
              callback: (val) => `£${val}`
            }
          }
        }
      }
    });
  }

  async loadRecentSales() {
    const tbody = document.getElementById('recent-sales-tbody');
    const countLabel = document.getElementById('table-count-label');
    if (!tbody) return;

    if (!this.currentSearchId) {
      if (countLabel) countLabel.textContent = 'No item selected';
      tbody.innerHTML = `
        <tr>
          <td colspan="6" class="text-center py-10 text-slate-500">
            Select an item from the left sidebar to view recent sales.
          </td>
        </tr>
      `;
      return;
    }

    // Immediately clear previous rows and show a loading placeholder
    tbody.innerHTML = `
      <tr>
        <td colspan="6" class="text-center py-10 text-slate-500">
          <div class="inline-flex items-center space-x-2">
            <i data-lucide="loader-2" class="w-4 h-4 animate-spin text-teal-400"></i>
            <span>Loading sales records…</span>
          </div>
        </td>
      </tr>
    `;
    if (window.lucide) lucide.createIcons();

    try {
      const res = await fetch(`/api/analytics/${this.currentSearchId}/recent-sales?limit=40`);
      if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to load sales`);
      const data = await res.json();

      if (countLabel) {
        countLabel.textContent = `Showing ${data.items.length} of ${data.total} recorded items`;
      }

      if (data.items.length === 0) {
        tbody.innerHTML = `
          <tr>
            <td colspan="6" class="text-center py-10 text-slate-500">
              No sales recorded yet. Click <strong>"Scrape Vinted (.co.uk)"</strong> above to populate.
            </td>
          </tr>
        `;
        return;
      }

      tbody.innerHTML = data.items.map(item => {
        const photo = item.photo_url || 'https://via.placeholder.com/80x80/1e293b/94a3b8?text=Vinted';
        const dateStr = item.captured_at ? new Date(item.captured_at).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : 'Unknown';
        const isDupPhoto = Boolean(item.is_duplicate_photo && item.photo_key);
        const dupCountBadge = (item.duplicate_photo_count && item.duplicate_photo_count > 1) ? ` (${item.duplicate_photo_count})` : '';
        const safePk = (item.photo_key || '').replace(/'/g, "\\'");

        return `
          <tr class="hover:bg-slate-900/70 transition group border-b border-slate-850/60">
            <td class="py-2.5 px-3 flex items-center space-x-3">
              <img src="${photo}" alt="" class="w-10 h-10 object-cover rounded-lg border border-slate-750 shrink-0 bg-slate-900 shadow-sm" onerror="this.src='https://via.placeholder.com/80x80/1e293b/94a3b8?text=No+Img'">
              <div class="truncate max-w-[220px] md:max-w-xs space-y-0.5">
                <div class="font-medium text-slate-200 group-hover:text-vinted transition truncate">${item.title || 'Vinted Listing'}</div>
                <div class="flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500">
                  <span class="font-mono text-slate-500">#${item.vinted_item_id || item.id}</span>
                  ${item.is_replica ? `<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-rose-500/20 text-rose-300 border border-rose-500/30" title="${(item.replica_reason || '').replace(/"/g, '&quot;')}">⚠️ Flagged</span>` : ''}
                  ${isDupPhoto ? `
                    <button type="button"
                            onclick="app.filterByDuplicatePhoto('${safePk}', '${item.vinted_item_id || item.id}')"
                            class="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold bg-amber-500/15 hover:bg-amber-500/25 text-amber-300 border border-amber-500/35 transition cursor-pointer"
                            title="Filter the entire database for all items sharing this repeating image">
                      <i data-lucide="images" class="w-3 h-3 mr-1 text-amber-400"></i>
                      <span>Find Repeating Image${dupCountBadge}</span>
                    </button>
                  ` : ''}
                </div>
              </div>
            </td>
            <td class="py-2.5 px-3 text-slate-300 font-medium">${item.brand || '—'}</td>
            <td class="py-2.5 px-3">
              <span class="px-2 py-0.5 rounded text-[11px] font-mono bg-slate-800 text-slate-300 border border-slate-700">${item.size || 'N/A'}</span>
              ${item.is_child_size ? `<span class="ml-1.5 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/30" title="${(item.child_size_reason || 'Child / Youth Size').replace(/"/g, '&quot;')}">👶 Child</span>` : ''}
            </td>
            <td class="py-2.5 px-3">
              <div class="flex items-center space-x-2">
                <span class="font-bold text-emerald-400 font-mono text-sm tabular-nums tracking-tight">£${item.price.toFixed(2)}</span>
                <span class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold ${item.is_sold ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30' : 'bg-cyan-500/15 text-cyan-300 border border-cyan-500/30'}">
                  <span class="w-1.5 h-1.5 rounded-full mr-1 ${item.is_sold ? 'bg-emerald-400' : 'bg-cyan-400'}"></span>
                  ${item.is_sold ? 'SOLD' : 'ACTIVE'}
                </span>
              </div>
            </td>
            <td class="py-2.5 px-3 text-slate-400 text-[11px] font-mono">${dateStr}</td>
            <td class="py-2.5 px-3 text-right">
              ${(() => {
                let itemUrl = item.url || (item.vinted_item_id ? `https://www.vinted.co.uk/items/${item.vinted_item_id}` : '');
                if (itemUrl && !itemUrl.startsWith('http')) {
                  itemUrl = 'https://www.vinted.co.uk' + (itemUrl.startsWith('/') ? '' : '/') + itemUrl;
                }
                return itemUrl ? `
                  <a href="${itemUrl}" target="_blank" rel="noopener noreferrer" class="inline-flex items-center px-2.5 py-1 rounded-lg text-[11px] font-semibold bg-slate-800/90 hover:bg-teal-500/20 hover:text-teal-300 text-slate-300 border border-slate-700/80 transition shadow-sm">
                    <span>View</span>
                    <i data-lucide="external-link" class="w-3 h-3 ml-1"></i>
                  </a>
                ` : '—';
              })()}
            </td>
          </tr>
        `;
      }).join('');

      if (window.lucide) lucide.createIcons();
    } catch (err) {
      console.error(err);
      tbody.innerHTML = `
        <tr>
          <td colspan="6" class="text-center py-10 text-slate-500">
            No sales recorded yet. Click <strong>"Scrape Vinted (.co.uk)"</strong> above to fetch listings.
          </td>
        </tr>
      `;
    }
  }

  // ── Deal Sniper Controls & Feed ─────────────────────────────────────────

  async loadSniperTargetsInfo() {
    try {
      const res = await fetch('/api/sniper/targets-info');
      if (!res.ok) return;
      const list = await res.json();
      this.sniperTargetInfo = new Map(list.map(item => [item.id, item]));
      
      const sniperView = document.getElementById('sniper-view');
      if (sniperView && !sniperView.classList.contains('hidden')) {
        this.renderSniperTargets();
      }
    } catch (err) {
      console.warn('Failed to load sniper targets info:', err);
    }
  }

  renderSniperTargets() {
    const container = document.getElementById('sniper-target-chips-container');
    if (!container) return;

    if (this.searches.length === 0) {
      container.innerHTML = '<div class="text-xs text-slate-500 py-3 text-center">No tracked items found. Click "Track New Item" to add some!</div>';
      return;
    }

    // Default select all targets only on first initialization
    if (!this.sniperTargetsInitialized) {
      this.searches.forEach(s => this.sniperTargets.add(s.id));
      this.sniperTargetsInitialized = true;
    }

    const statTargetsEl = document.getElementById('sniper-stat-targets');
    if (statTargetsEl) statTargetsEl.textContent = this.sniperTargets.size;

    const globalMaxPrice = parseFloat(document.getElementById('sniper-max-price')?.value) || 50.0;

    container.innerHTML = this.searches.map(s => {
      const isChecked = this.sniperTargets.has(s.id);
      const info = this.sniperTargetInfo ? this.sniperTargetInfo.get(s.id) : null;
      const customPrice = this.itemMaxPrices.get(s.id);
      const currentItemPrice = customPrice != null ? customPrice : globalMaxPrice;

      const soldCount = info ? (info.total_sold_count || 0) : (s.item_count || 0);
      const avgPrice = info && info.avg_sold_price > 0 ? `£${info.avg_sold_price.toFixed(2)}` : null;
      const recPrice = info && info.recommended_profitable_price ? info.recommended_profitable_price : null;

      return `
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 p-3 rounded-xl border transition ${
          isChecked 
            ? 'bg-slate-900/95 border-teal-500/40 shadow-sm shadow-teal-500/5' 
            : 'bg-slate-950/90 border-slate-800/80 opacity-60 hover:opacity-100'
        }">
          <!-- Checkbox, Item Name & Historical Market Stats -->
          <div class="flex items-start sm:items-center space-x-3 min-w-0 flex-1">
            <input type="checkbox" class="custom-checkbox mt-0.5 sm:mt-0" ${isChecked ? 'checked' : ''} onchange="app.toggleSniperTarget(${s.id})">
            <div class="min-w-0 flex-1">
              <div class="flex items-center space-x-2">
                <span class="text-xs font-bold text-white truncate">${s.search_text}</span>
                ${s.brand ? `<span class="px-1.5 py-0.2 rounded text-[10px] font-medium bg-slate-800 text-slate-400 truncate max-w-[120px]">${s.brand}</span>` : ''}
              </div>
              <div class="flex flex-wrap items-center gap-2 mt-1 text-[11px] text-slate-400">
                <span class="flex items-center">
                  <i data-lucide="shopping-bag" class="w-3 h-3 mr-1 text-slate-500"></i>
                  Sold: <strong class="text-slate-200 ml-1 font-mono">${soldCount} items</strong>
                </span>
                <span class="text-slate-600">•</span>
                <span class="flex items-center">
                  <i data-lucide="pound-sterling" class="w-3 h-3 mr-1 text-slate-500"></i>
                  Avg: <strong class="text-emerald-400 ml-1 font-mono">${avgPrice || 'No data'}</strong>
                </span>
              </div>
            </div>
          </div>

          <!-- Price Controls & Profitable Recommendation Button -->
          <div class="flex items-center space-x-2 shrink-0 self-end sm:self-center">
            ${recPrice ? `
              <button type="button" 
                      onclick="app.applyRecommendedPrice(${s.id}, ${recPrice})" 
                      class="inline-flex items-center px-2 py-1 rounded-lg text-[10px] font-semibold bg-amber-500/10 hover:bg-amber-500/20 text-amber-300 border border-amber-500/30 transition cursor-pointer" 
                      title="Apply recommended profitable buy price (£${recPrice}) for ~35% resale margin">
                <i data-lucide="sparkles" class="w-3 h-3 mr-1 text-amber-400"></i>
                Rec: ≤ £${recPrice}
              </button>
            ` : ''}

            <div class="flex items-center space-x-1.5 bg-slate-950 p-1 rounded-lg border border-slate-700/80">
              <span class="text-[11px] text-slate-400 font-mono pl-1">Max: £</span>
              <input type="number" 
                     id="item-max-price-${s.id}" 
                     value="${currentItemPrice}" 
                     min="1" 
                     max="10000" 
                     step="1"
                     onchange="app.setItemMaxPrice(${s.id}, this.value)"
                     class="w-16 bg-slate-900 border border-slate-700 rounded px-1.5 py-0.5 text-xs font-mono font-bold text-emerald-400 text-center focus:outline-none focus:border-vinted">
            </div>
          </div>
        </div>
      `;
    }).join('');

    if (window.lucide) lucide.createIcons();
  }

  toggleSniperTarget(id) {
    if (this.sniperTargets.has(id)) {
      this.sniperTargets.delete(id);
    } else {
      this.sniperTargets.add(id);
    }
    this.renderSniperTargets();
    this.syncActiveSniperConfig();
  }

  selectAllSniperTargets(selectAll) {
    this.sniperTargetsInitialized = true;
    if (selectAll) {
      this.searches.forEach(s => this.sniperTargets.add(s.id));
    } else {
      this.sniperTargets.clear();
    }
    this.renderSniperTargets();
    this.syncActiveSniperConfig();
  }

  setItemMaxPrice(searchId, priceVal) {
    const price = parseFloat(priceVal);
    if (!isNaN(price) && price > 0) {
      this.itemMaxPrices.set(searchId, price);
    } else {
      this.itemMaxPrices.delete(searchId);
    }
    this.syncActiveSniperConfig();
  }

  applyRecommendedPrice(searchId, price) {
    this.setItemMaxPrice(searchId, price);
    const input = document.getElementById(`item-max-price-${searchId}`);
    if (input) input.value = price;
    const s = this.searches.find(x => x.id === searchId);
    this.showToast(`Set profitable max buy price for "${s ? s.search_text : 'item'}" to £${price}.`, 'info');
  }

  applyAllRecommendedPrices() {
    let count = 0;
    for (const s of this.searches) {
      const info = this.sniperTargetInfo ? this.sniperTargetInfo.get(s.id) : null;
      if (info && info.recommended_profitable_price) {
        this.itemMaxPrices.set(s.id, info.recommended_profitable_price);
        count++;
      }
    }
    this.renderSniperTargets();
    this.syncActiveSniperConfig();
    if (count > 0) {
      this.showToast(`Applied recommended profitable prices across ${count} items based on market sales history!`, 'success');
    } else {
      this.showToast('No sales data recorded yet to compute recommended prices. Scrape items first!', 'info');
    }
  }

  handleGlobalMaxPriceChange(val) {
    const price = parseFloat(val);
    if (isNaN(price)) return;
    this.renderSniperTargets();
    this.syncActiveSniperConfig();
  }

  setQuickMaxPrice(val) {
    const input = document.getElementById('sniper-max-price');
    if (input) {
      input.value = val;
      this.handleGlobalMaxPriceChange(val);
    }
  }

  handleMaxAgeChange(val) {
    const age = parseInt(val, 10);
    if (isNaN(age) || age < 1) return;
    this.syncActiveSniperConfig();
  }

  setQuickMaxAge(val) {
    const input = document.getElementById('sniper-max-age');
    if (input) input.value = val;
    this.handleMaxAgeChange(val);
  }

  async syncActiveSniperConfig() {
    if (!this.sniperActive) return;
    const targetIds = Array.from(this.sniperTargets);
    if (targetIds.length === 0) return;

    const maxPrice = parseFloat(document.getElementById('sniper-max-price')?.value) || 50.0;
    const maxAge = parseInt(document.getElementById('sniper-max-age')?.value, 10) || 15;
    const itemMaxPricesObj = {};
    for (const [id, price] of this.itemMaxPrices.entries()) {
      if (price > 0 && targetIds.includes(id)) {
        itemMaxPricesObj[id] = price;
      }
    }

    try {
      const res = await fetch('/api/sniper/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          search_ids: targetIds,
          max_price: maxPrice,
          item_max_prices: itemMaxPricesObj,
          max_age_minutes: maxAge,
          poll_delay_seconds: 5.0
        })
      });
      if (res.ok) {
        const data = await res.json();
        this.updateSniperStatsAndDeals(data);
      }
    } catch (err) {
      console.warn('Could not sync active sniper config:', err);
    }
  }

  handleHeaderSniperClick() {
    this.showSniperView();
    if (!this.sniperActive) {
      if (this.sniperTargets.size === 0 && this.searches && this.searches.length > 0) {
        this.searches.forEach(s => this.sniperTargets.add(s.id));
        this.renderSniperTargets();
      }
      if (this.sniperTargets.size > 0) {
        this.startSniper();
      } else {
        this.showToast('Please add at least one tracked item before starting Deal Sniper.', 'info');
      }
    }
  }

  toggleSniper() {
    if (this.sniperActive) {
      this.stopSniper();
    } else {
      this.startSniper();
    }
  }

  async startSniper() {
    const targetIds = Array.from(this.sniperTargets);
    if (targetIds.length === 0) {
      this.showToast('Please select at least one tracked item for the sniper to scan.', 'error');
      return;
    }

    const maxPrice = parseFloat(document.getElementById('sniper-max-price')?.value) || 50.0;
    const maxAge = parseInt(document.getElementById('sniper-max-age')?.value, 10) || 15;

    // Construct per-item max price overrides dictionary
    const itemMaxPricesObj = {};
    for (const [id, price] of this.itemMaxPrices.entries()) {
      if (price > 0 && targetIds.includes(id)) {
        itemMaxPricesObj[id] = price;
      }
    }

    const toggleBtn = document.getElementById('btn-sniper-toggle');
    const toggleText = document.getElementById('sniper-toggle-text');
    const toggleIcon = document.getElementById('sniper-toggle-icon');

    if (toggleBtn) toggleBtn.disabled = true;

    try {
      const res = await fetch('/api/sniper/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          search_ids: targetIds,
          max_price: maxPrice,
          item_max_prices: itemMaxPricesObj,
          max_age_minutes: maxAge,
          poll_delay_seconds: 5.0
        })
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to start sniper');
      }

      this.sniperActive = true;
      this.updateSniperUIState(true);
      this.showToast(`Deal Sniper active! Polling newest listings (<=${maxAge}m old) with custom price thresholds...`, 'success');

      // Start live polling loop
      if (this.sniperPollTimer) clearInterval(this.sniperPollTimer);
      this.sniperPollTimer = setInterval(() => this.pollSniperStatus(), 2500);
      await this.pollSniperStatus();

    } catch (err) {
      this.showToast(err.message, 'error');
    } finally {
      if (toggleBtn) toggleBtn.disabled = false;
    }
  }

  async stopSniper() {
    const toggleBtn = document.getElementById('btn-sniper-toggle');
    if (toggleBtn) toggleBtn.disabled = true;

    try {
      await fetch('/api/sniper/stop', { method: 'POST' });
      this.sniperActive = false;
      if (this.sniperPollTimer) {
        clearInterval(this.sniperPollTimer);
        this.sniperPollTimer = null;
      }
      this.updateSniperUIState(false);
      this.showToast('Deal Sniper stopped.', 'info');
      await this.pollSniperStatus();
      await this.loadSearches();
      if (this.isDbExplorerActive) {
        await this.loadDatabaseExplorerData();
      }
    } catch (err) {
      this.showToast('Failed to stop sniper: ' + err.message, 'error');
    } finally {
      if (toggleBtn) toggleBtn.disabled = false;
    }
  }

  updateSniperUIState(isActive) {
    const toggleBtn = document.getElementById('btn-sniper-toggle');
    const toggleText = document.getElementById('sniper-toggle-text');
    const toggleIcon = document.getElementById('sniper-toggle-icon');
    const statusPill = document.getElementById('sniper-status-pill');
    const statusText = document.getElementById('sniper-status-text');
    const statusDot = document.getElementById('sniper-status-dot');
    const navDot = document.getElementById('sniper-nav-dot');
    const navPing = document.getElementById('sniper-nav-ping');

    // Header Deal Sniper Quick Controller
    const headerSniperBtn = document.getElementById('btn-header-sniper-status');
    const headerSniperDot = document.getElementById('header-sniper-dot');
    const headerSniperText = document.getElementById('header-sniper-text');

    if (isActive) {
      if (toggleBtn) {
        toggleBtn.className = 'inline-flex items-center px-4 py-2.5 rounded-xl text-xs font-bold bg-rose-600 hover:bg-rose-500 text-white transition shadow-lg shadow-rose-600/25 cursor-pointer';
      }
      if (toggleText) toggleText.textContent = 'Stop Sniping';
      if (toggleIcon) toggleIcon.setAttribute('data-lucide', 'square');

      if (statusPill) {
        statusPill.className = 'inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 radar-sweep-active';
      }
      if (statusText) statusText.textContent = 'SNIPER LIVE';
      if (statusDot) statusDot.className = 'w-2 h-2 rounded-full bg-emerald-400 mr-1.5 pulse-live';

      if (navDot) navDot.className = 'relative inline-flex rounded-full h-3 w-3 bg-emerald-400';
      if (navPing) navPing.classList.remove('hidden');

      if (headerSniperBtn) {
        headerSniperBtn.className = 'inline-flex items-center px-2.5 sm:px-3 py-1.5 sm:py-2 rounded-xl text-xs font-semibold bg-emerald-950/40 hover:bg-emerald-900/40 text-emerald-300 border border-emerald-500/50 shadow-sm cursor-pointer transition select-none';
      }
      if (headerSniperDot) {
        headerSniperDot.className = 'w-2 h-2 rounded-full bg-emerald-400 mr-1.5 sm:mr-2 pulse-live shrink-0';
      }
      if (headerSniperText) {
        headerSniperText.innerHTML = `LIVE <button type="button" onclick="event.stopPropagation(); app.stopSniper();" class="ml-1.5 px-1.5 py-0.5 rounded bg-rose-500/20 hover:bg-rose-500 text-rose-300 hover:text-white text-[10px] font-bold uppercase transition border border-rose-500/40 cursor-pointer" title="Stop live sniper">Stop</button>`;
      }
    } else {
      if (toggleBtn) {
        toggleBtn.className = 'inline-flex items-center px-4 py-2.5 rounded-xl text-xs font-bold bg-emerald-600 hover:bg-emerald-500 text-white transition shadow-lg shadow-emerald-600/25 cursor-pointer';
      }
      if (toggleText) toggleText.textContent = 'Start Sniping';
      if (toggleIcon) toggleIcon.setAttribute('data-lucide', 'play');

      if (statusPill) {
        statusPill.className = 'inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-slate-800 text-slate-400 border border-slate-700';
      }
      if (statusText) statusText.textContent = 'SCANNER IDLE';
      if (statusDot) statusDot.className = 'w-2 h-2 rounded-full bg-slate-500 mr-1.5';

      if (navDot) navDot.className = 'relative inline-flex rounded-full h-3 w-3 bg-slate-500';
      if (navPing) navPing.classList.add('hidden');

      if (headerSniperBtn) {
        headerSniperBtn.className = 'inline-flex items-center px-2.5 sm:px-3 py-1.5 sm:py-2 rounded-xl text-xs font-semibold bg-slate-950/80 hover:bg-slate-800 text-slate-300 border border-slate-800 hover:border-teal-500/40 transition shadow-sm cursor-pointer select-none';
      }
      if (headerSniperDot) {
        headerSniperDot.className = 'w-2 h-2 rounded-full bg-slate-500 mr-1.5 sm:mr-2 shrink-0';
      }
      if (headerSniperText) {
        headerSniperText.innerHTML = `<span class="text-slate-400 font-medium">Idle</span>`;
      }
    }

    if (window.lucide) lucide.createIcons();
  }

  async checkExistingSniperStatus() {
    try {
      const res = await fetch('/api/sniper/status');
      if (!res.ok) return;
      const data = await res.json();

      const isRunning = Boolean(data.is_running || data.is_active);
      const targetIds = data.selected_search_ids || data.target_ids || [];

      if (isRunning) {
        this.sniperActive = true;
        if (targetIds.length > 0) {
          this.sniperTargets = new Set(targetIds);
        }
        if (data.max_price) {
          const maxPriceEl = document.getElementById('sniper-max-price');
          if (maxPriceEl) maxPriceEl.value = data.max_price;
        }
        if (data.max_age_minutes) {
          const maxAgeEl = document.getElementById('sniper-max-age');
          if (maxAgeEl) maxAgeEl.value = data.max_age_minutes;
        }
        this.updateSniperUIState(true);
        if (this.sniperPollTimer) clearInterval(this.sniperPollTimer);
        this.sniperPollTimer = setInterval(() => this.pollSniperStatus(), 2500);
      } else {
        this.updateSniperUIState(false);
      }

      this.updateSniperStatsAndDeals(data);
    } catch (err) {
      console.error('Failed to check sniper status:', err);
    }
  }

  async pollSniperStatus() {
    try {
      const res = await fetch('/api/sniper/status');
      if (!res.ok) return;
      const data = await res.json();

      this.updateSniperStatsAndDeals(data);

      const isRunning = Boolean(data.is_running || data.is_active);
      if (!isRunning && this.sniperActive) {
        this.sniperActive = false;
        if (this.sniperPollTimer) {
          clearInterval(this.sniperPollTimer);
          this.sniperPollTimer = null;
        }
        this.updateSniperUIState(false);
      }
    } catch (err) {
      console.error('Sniper poll error:', err);
    }
  }

  updateSniperStatsAndDeals(data) {
    const statTargets = document.getElementById('sniper-stat-targets');
    const statChecks = document.getElementById('sniper-stat-checks');
    const statScraped = document.getElementById('sniper-stat-scraped');
    const statDeals = document.getElementById('sniper-stat-deals');
    const navBadge = document.getElementById('sniper-deals-badge');
    const streamCount = document.getElementById('sniper-stream-count');

    const activeMaxAge = parseInt(document.getElementById('sniper-max-age')?.value, 10) || data.max_age_minutes || 15;

    // Filter deals against clearedDealIds and maxAge
    const rawDeals = data.deals || [];
    const currentDeals = rawDeals.filter(
      d => d && d.id && !this.clearedDealIds.has(String(d.id)) && Number(d.age_minutes ?? 0) <= activeMaxAge
    );
    this.sniperDeals = currentDeals;

    const targetCount = (data.selected_search_ids ? data.selected_search_ids.length : 0) || data.target_count || this.sniperTargets.size;
    const checksCount = data.total_polls_count ?? data.checks_completed ?? 0;
    const itemsSaved = data.total_items_saved ?? 0;
    const totalDb = data.total_db_items ?? 0;
    const snipesCount = currentDeals.length;

    if (statTargets) statTargets.textContent = targetCount;
    if (statChecks) statChecks.textContent = checksCount;
    if (statScraped) statScraped.textContent = `+${itemsSaved}`;
    const dbSub = document.getElementById('sniper-stat-db-sub');
    if (dbSub && totalDb > 0) {
      dbSub.innerHTML = `<i data-lucide="database" class="w-3 h-3 text-emerald-400 mr-1"></i>${totalDb.toLocaleString()} in DB (${data.total_items_inspected ?? 0} checked)`;
    }
    if (statDeals) statDeals.textContent = snipesCount;

    // When newly scraped items are persisted to the database by the sniper, refresh live counts
    if (this.lastSniperSavedCount !== undefined && itemsSaved > this.lastSniperSavedCount) {
      this.loadSearches();
      if (this.isDbExplorerActive) {
        this.loadDatabaseExplorerData();
      } else if (this.currentSearchId) {
        this.loadRecentSales();
        this.loadSummaryStats();
      }
    }
    this.lastSniperSavedCount = itemsSaved;
    if (navBadge) {
      navBadge.textContent = snipesCount;
      if (snipesCount > 0) {
        navBadge.className = 'px-2 py-0.5 text-[11px] font-mono font-bold rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/40';
      } else {
        navBadge.className = 'px-2 py-0.5 text-[11px] font-mono font-bold rounded-full bg-slate-800 text-slate-400 border border-slate-700';
      }
    }
    if (streamCount) streamCount.textContent = `${snipesCount} sniped deals`;

    // Check if new deals arrived
    if (currentDeals.length > this.previousDealCount) {
      const isSoundEnabled = document.getElementById('sniper-sound-alert')?.checked ?? true;
      if (isSoundEnabled) {
        this.playChime();
      }
      const newCount = currentDeals.length - this.previousDealCount;
      this.showToast(`🎯 SNIPED DEAL FOUND! ${newCount} new bargain matching your rules.`, 'success');
    }
    this.previousDealCount = currentDeals.length;

    this.renderSniperDeals(currentDeals);
    this.renderSniperLogs(data.logs || data.recent_logs || []);

    if (data.antiban) {
      this.applyAntiBanStatusUI(data.antiban);
    }
  }

  playChime() {
    try {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      if (!AudioContext) return;
      if (!this.audioCtx) this.audioCtx = new AudioContext();
      if (this.audioCtx.state === 'suspended') {
        this.audioCtx.resume();
      }

      const osc = this.audioCtx.createOscillator();
      const gain = this.audioCtx.createGain();

      osc.type = 'sine';
      osc.frequency.setValueAtTime(587.33, this.audioCtx.currentTime); // D5
      osc.frequency.exponentialRampToValueAtTime(880.00, this.audioCtx.currentTime + 0.12); // A5

      gain.gain.setValueAtTime(0.2, this.audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, this.audioCtx.currentTime + 0.4);

      osc.connect(gain);
      gain.connect(this.audioCtx.destination);

      osc.start();
      osc.stop(this.audioCtx.currentTime + 0.45);
    } catch (e) {
      console.warn('Audio chime notice:', e);
    }
  }

  renderSniperDeals(deals) {
    const container = document.getElementById('sniper-deals-grid');
    if (!container) return;

    if (!deals || deals.length === 0) {
      container.innerHTML = `
        <div class="col-span-full py-12 text-center space-y-3">
          <div class="w-12 h-12 mx-auto rounded-full bg-slate-900 border border-slate-800 flex items-center justify-center text-slate-500">
            <i data-lucide="crosshair" class="w-6 h-6 text-vinted"></i>
          </div>
          <h4 class="text-sm font-semibold text-slate-300">Radar Standby</h4>
          <p class="text-xs text-slate-500 max-w-md mx-auto">
            ${this.sniperActive 
              ? 'Radar is continuously scanning newest listings... bargains matching your maximum price & age limits will pop up instantly!' 
              : 'Configure your targets, max price and age limits above, then click <strong>"Start Sniping"</strong>.'}
          </p>
        </div>
      `;
      if (window.lucide) lucide.createIcons();
      return;
    }

    container.innerHTML = deals.map(deal => {
      const photo = deal.photo_url || 'https://via.placeholder.com/200x200/1e293b/94a3b8?text=Vinted';
      const ageText = deal.age_minutes != null ? `${deal.age_minutes}m ago` : 'Just now';
      const discountPill = deal.discount_pct && deal.discount_pct > 0
        ? `<span class="px-2.5 py-0.5 rounded-full text-[10px] font-extrabold bg-gradient-to-r from-emerald-500/30 to-teal-500/20 text-emerald-300 border border-emerald-500/40 shadow-sm flex items-center">
             <i data-lucide="flame" class="w-3 h-3 mr-1 text-emerald-400"></i>
             -${deal.discount_pct.toFixed(0)}% OFF
           </span>`
        : (deal.savings && deal.savings > 0 
            ? `<span class="px-2.5 py-0.5 rounded-full text-[10px] font-extrabold bg-teal-500/20 text-teal-300 border border-teal-500/40">Save £${deal.savings.toFixed(0)}</span>`
            : '');

      let vintedUrl = deal.url || (deal.id ? `https://www.vinted.co.uk/items/${deal.id}` : '');
      if (vintedUrl && !vintedUrl.startsWith('http')) {
        vintedUrl = 'https://www.vinted.co.uk' + (vintedUrl.startsWith('/') ? '' : '/') + vintedUrl;
      }

      const askingPrice = Number(deal.price) || 0;
      const buyerFee = 0.70 + (askingPrice * 0.05);
      const estShipping = 2.70;
      const totalBuyCost = askingPrice + buyerFee + estShipping;
      const estResaleVal = Number(deal.market_avg_price) || (askingPrice + Number(deal.savings || 15));
      const netProfit = Math.max(0, estResaleVal - totalBuyCost);
      const roiPct = totalBuyCost > 0 ? (netProfit / totalBuyCost) * 100 : 0;

      return `
        <div class="deal-card deal-card-new p-4 rounded-2xl flex flex-col justify-between space-y-3 relative group" data-deal-id="${deal.id}" tabindex="0" onclick="app.setFocusedDealById('${deal.id}')">
          <!-- Dismiss button -->
          <button onclick="event.stopPropagation(); app.dismissSniperDeal('${deal.id}')" 
                  class="absolute top-3 right-3 w-7 h-7 rounded-lg bg-slate-950/80 hover:bg-rose-500/20 border border-slate-800 hover:border-rose-500/40 flex items-center justify-center text-slate-400 hover:text-rose-300 transition shadow-sm z-10 cursor-pointer" 
                  title="Dismiss this deal">
            <i data-lucide="x" class="w-3.5 h-3.5"></i>
          </button>

          <!-- Top row: Thumbnail & Metadata -->
          <div class="flex items-start space-x-3.5 pr-6">
            <div class="relative shrink-0 overflow-hidden rounded-xl border border-slate-700/80 bg-slate-850 shadow-md">
              <img src="${photo}" alt="" class="w-20 h-20 sm:w-24 sm:h-24 object-cover rounded-xl transition-transform duration-500 group-hover:scale-105" onerror="this.src='https://via.placeholder.com/200x200/1e293b/94a3b8?text=No+Img'">
              <span class="absolute top-1 left-1 px-1.5 py-0.2 rounded text-[9px] font-mono font-black bg-emerald-400 text-slate-950 uppercase shadow-md">
                SNIPED
              </span>
            </div>

            <div class="flex-1 min-w-0">
              <div class="flex items-center justify-between gap-1 mb-1">
                <span class="px-2 py-0.5 rounded-md text-[10px] font-bold bg-slate-800/90 text-teal-300 border border-slate-700/80 truncate max-w-[120px]">${deal.brand || 'Vinted'}</span>
                <span class="text-[10px] text-cyan-300 font-mono shrink-0 flex items-center">
                  <span class="w-1.5 h-1.5 rounded-full bg-cyan-400 mr-1 pulse-live"></span>
                  ${ageText}
                </span>
              </div>
              
              <h4 class="text-xs font-bold text-white truncate hover:text-teal-300 transition" title="${deal.title}">
                ${deal.title || 'Vinted Listing'}
              </h4>

              <!-- Price & Discount -->
              <div class="flex items-baseline space-x-2 mt-1.5">
                <span class="text-2xl font-black font-mono text-emerald-400 tracking-tight">£${deal.price.toFixed(2)}</span>
                ${discountPill}
              </div>

              <div class="flex items-center space-x-2 text-[10px] text-slate-400 mt-1">
                ${deal.market_avg_price ? `<span>Market: <strong class="text-slate-300 font-mono">£${deal.market_avg_price.toFixed(2)}</strong></span>` : ''}
                ${deal.savings && deal.savings > 0 ? `<span class="text-teal-400 font-semibold">• Save £${deal.savings.toFixed(2)}</span>` : ''}
              </div>
            </div>
          </div>

          <!-- Reseller Net Margin & Fee Breakdown (Persona 1 & 4 UX improvement) -->
          <div class="p-2.5 rounded-xl bg-slate-950/70 border border-slate-800/80 space-y-1 text-[11px]">
            <div class="flex items-center justify-between text-slate-400">
              <span class="flex items-center">
                <i data-lucide="calculator" class="w-3 h-3 mr-1 text-teal-400"></i>
                Vinted Fee + Shipping:
              </span>
              <span class="font-mono text-slate-300 tabular-nums">+£${(buyerFee + estShipping).toFixed(2)}</span>
            </div>
            <div class="flex items-center justify-between text-slate-400">
              <span>Total Landed Cost:</span>
              <span class="font-mono text-slate-200 tabular-nums font-semibold">£${totalBuyCost.toFixed(2)}</span>
            </div>
            <div class="flex items-center justify-between pt-1 border-t border-slate-800/80 font-bold">
              <span class="text-emerald-400">Est. Resale Profit:</span>
              <span class="font-mono text-emerald-400 font-extrabold tabular-nums">+£${netProfit.toFixed(2)} <span class="text-[10px] text-emerald-400/80 font-normal">(${roiPct.toFixed(0)}% ROI)</span></span>
            </div>
          </div>

          <!-- Bottom bar: Size, Condition & Action Button -->
          <div class="pt-2.5 border-t border-slate-800/80 flex items-center justify-between gap-2">
            <div class="flex items-center space-x-1.5 truncate text-[10px] text-slate-400">
              <span class="px-2 py-0.5 rounded-md font-mono font-semibold bg-slate-950/80 text-slate-300 border border-slate-800">
                ${deal.size ? `Size: ${deal.size}` : 'Standard'}
              </span>
              ${deal.condition ? `<span class="hidden sm:inline-block text-slate-500">• ${deal.condition}</span>` : ''}
            </div>

            ${vintedUrl ? `
              <a href="${vintedUrl}" target="_blank" rel="noopener noreferrer" class="btn-primary-glow inline-flex items-center px-3.5 py-1.5 rounded-xl text-xs font-bold text-white transition shadow-sm cursor-pointer shrink-0">
                <span>Snipe Deal</span>
                <i data-lucide="arrow-up-right" class="w-3.5 h-3.5 ml-1"></i>
              </a>
            ` : '—'}
          </div>
        </div>
      `;
    }).join('');

    if (window.lucide) lucide.createIcons();
  }

  renderSniperLogs(logs) {
    const terminal = document.getElementById('sniper-logs-terminal');
    if (!terminal) return;

    if (!logs || logs.length === 0) {
      terminal.innerHTML = '<div class="text-slate-600">[System] Radar listening. No activity yet.</div>';
      return;
    }

    terminal.innerHTML = logs.map(entry => {
      const timeStr = entry.timestamp ? new Date(entry.timestamp).toLocaleTimeString('en-GB') : '';
      let colorClass = 'text-slate-400';
      if (entry.event === 'deal_sniped') colorClass = 'text-emerald-400 font-bold';
      else if (entry.event === 'error') colorClass = 'text-rose-400';
      else if (entry.event === 'started') colorClass = 'text-cyan-400';

      return `<div class="${colorClass}">[${timeStr}] ${entry.message}</div>`;
    }).join('');

    terminal.scrollTop = terminal.scrollHeight;
  }

  async dismissSniperDeal(dealId) {
    if (!dealId) return;
    const strId = String(dealId);
    this.clearedDealIds.add(strId);
    this.sniperDeals = this.sniperDeals.filter(d => String(d.id) !== strId);
    this.previousDealCount = this.sniperDeals.length;
    this.renderSniperDeals(this.sniperDeals);

    const statDeals = document.getElementById('sniper-stat-deals');
    if (statDeals) statDeals.textContent = this.sniperDeals.length;
    const navBadge = document.getElementById('sniper-deals-badge');
    if (navBadge) {
      navBadge.textContent = this.sniperDeals.length;
      if (this.sniperDeals.length === 0) {
        navBadge.className = 'px-2 py-0.5 text-[11px] font-mono font-bold rounded-full bg-slate-800 text-slate-400 border border-slate-700';
      }
    }
    const streamCount = document.getElementById('sniper-stream-count');
    if (streamCount) streamCount.textContent = `${this.sniperDeals.length} sniped deals`;

    try {
      await fetch(`/api/sniper/deals/${encodeURIComponent(strId)}/dismiss`, { method: 'POST' });
    } catch (err) {
      console.error('Failed to dismiss sniper deal:', err);
    }
  }

  async clearSniperDeals() {
    try {
      if (this.sniperDeals && this.sniperDeals.length > 0) {
        this.sniperDeals.forEach(d => {
          if (d && d.id) this.clearedDealIds.add(String(d.id));
        });
      }
      this.sniperDeals = [];
      this.previousDealCount = 0;
      this.renderSniperDeals([]);
      const statDeals = document.getElementById('sniper-stat-deals');
      if (statDeals) statDeals.textContent = '0';
      const navBadge = document.getElementById('sniper-deals-badge');
      if (navBadge) {
        navBadge.textContent = '0';
        navBadge.className = 'px-2 py-0.5 text-[11px] font-mono font-bold rounded-full bg-slate-800 text-slate-400 border border-slate-700';
      }
      const streamCount = document.getElementById('sniper-stream-count');
      if (streamCount) streamCount.textContent = '0 sniped deals';

      await fetch('/api/sniper/clear', { method: 'POST' });
      this.showToast('Cleared sniped deals stream.', 'info');
    } catch (err) {
      console.error('Failed to clear sniper deals:', err);
    }
  }

  // ── Scraper Hub & Scheduler Controls ────────────────────────────────────

  openScraperModal() {
    this.renderBatchTargets();
    this.loadSchedulerStatus();
    this.updateSchedulerModalControls();
    this.renderSchedulerCountdownUI();
    const modal = document.getElementById('scraper-modal');
    if (modal) modal.classList.remove('hidden');
    if (window.lucide) lucide.createIcons();
  }

  closeScraperModal() {
    const modal = document.getElementById('scraper-modal');
    if (modal) modal.classList.add('hidden');
  }

  startSchedulerCountdownLoop() {
    if (this.schedulerTickTimer) clearInterval(this.schedulerTickTimer);
    this.schedulerTickTimer = setInterval(() => {
      this.schedulerTickCount += 1;
      this.renderSchedulerCountdownUI();

      const modal = document.getElementById('scraper-modal');
      const isModalOpen = modal && !modal.classList.contains('hidden');
      const isConstant = Boolean(this.schedulerStatus && this.schedulerStatus.constant_scrape);
      const isRunning = Boolean(this.schedulerStatus && this.schedulerStatus.is_cycle_running);

      let secsLeft = null;
      if (this.schedulerStatus && this.schedulerStatus.next_run_time) {
        secsLeft = Math.max(0, Math.floor((new Date(this.schedulerStatus.next_run_time).getTime() - Date.now()) / 1000));
      }

      // Poll backend every 3s when Constant Scrape / Cycle / Modal is active or timer hits 0, otherwise every 15s
      const pollFreq = (isConstant || isRunning || isModalOpen || (secsLeft !== null && secsLeft <= 2)) ? 3 : 15;
      if (this.schedulerTickCount % pollFreq === 0) {
        this.loadSchedulerStatus();
      }
    }, 1000);
  }

  _formatDurationCountdown(totalSeconds) {
    const secs = Math.max(0, Math.floor(Number(totalSeconds) || 0));
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = secs % 60;
    if (h > 0) {
      return `${h}h ${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s`;
    }
    return `${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s`;
  }

  _formatShortCountdown(totalSeconds) {
    const secs = Math.max(0, Math.floor(Number(totalSeconds) || 0));
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = secs % 60;
    if (h > 0) {
      return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    }
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }

  renderSchedulerCountdownUI() {
    if (!this.schedulerStatus) return;

    const st = this.schedulerStatus;
    const isConstant = Boolean(st.constant_scrape);
    const isRunning = Boolean(st.is_cycle_running);
    const intervalSecs = Math.max(60, (Number(st.interval_minutes) || 30) * 60);

    let secsLeft = null;
    if (st.next_run_time) {
      secsLeft = Math.max(0, Math.floor((new Date(st.next_run_time).getTime() - Date.now()) / 1000));
    } else if (typeof st.seconds_until_next_run === 'number') {
      secsLeft = Math.max(0, st.seconds_until_next_run);
    }

    const headerTimer = document.getElementById('header-next-poll-timer');
    const sidebarTimer = document.getElementById('sidebar-next-poll-timer');
    const countdownDisplay = document.getElementById('scheduler-countdown-display');
    const countdownLabel = document.getElementById('scheduler-countdown-label');
    const countdownDot = document.getElementById('scheduler-countdown-dot');
    const progressEl = document.getElementById('scheduler-countdown-progress');
    const lastRunSummary = document.getElementById('scheduler-last-run-summary');
    const nextRunClock = document.getElementById('scheduler-next-run-clock');

    // 1. Update Top Header & Sidebar Timer Badges
    if (isRunning) {
      const idxInfo = st.total_searches_in_cycle > 0 ? ` (${st.current_search_index}/${st.total_searches_in_cycle})` : '';
      if (headerTimer) {
        headerTimer.textContent = `⚡ Scraping${idxInfo}…`;
        headerTimer.className = 'ml-1.5 px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 pulse-live';
      }
      if (sidebarTimer) {
        sidebarTimer.textContent = 'SCRAPING';
        sidebarTimer.className = 'text-[10px] px-1.5 py-0.5 rounded font-mono font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 pulse-live';
      }
    } else if (secsLeft !== null) {
      const shortStr = this._formatShortCountdown(secsLeft);
      if (headerTimer) {
        headerTimer.textContent = `Next: ${shortStr}`;
        headerTimer.className = 'ml-1.5 px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-slate-800 text-teal-300 border border-slate-700';
      }
      if (sidebarTimer) {
        sidebarTimer.textContent = isConstant ? `24/7 • ${shortStr}` : shortStr;
        sidebarTimer.className = isConstant
          ? 'text-[10px] px-1.5 py-0.5 rounded font-mono font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/30'
          : 'text-[10px] px-1.5 py-0.5 rounded font-mono font-semibold bg-slate-800 text-teal-400 border border-slate-700';
      }
    }

    // 2. Update Modal Auto-Poll Countdown Bar
    if (isRunning) {
      const activeTarget = st.current_search_text
        ? `Scraping '${st.current_search_text}' (${st.current_search_index || 1}/${st.total_searches_in_cycle || this.searches.length})…`
        : 'Scrape cycle in progress…';
      if (countdownLabel) countdownLabel.textContent = activeTarget;
      if (countdownDisplay) {
        countdownDisplay.textContent = 'RUNNING NOW';
        countdownDisplay.className = 'text-xs font-mono font-bold text-emerald-400 tracking-wide';
      }
      if (countdownDot) countdownDot.className = 'w-2 h-2 rounded-full bg-emerald-400 animate-ping';
      if (progressEl) progressEl.style.width = '100%';
    } else if (secsLeft !== null) {
      if (countdownLabel) countdownLabel.textContent = 'Next Auto-Poll Scrape In:';
      if (countdownDisplay) {
        countdownDisplay.textContent = this._formatDurationCountdown(secsLeft);
        countdownDisplay.className = 'text-sm font-mono font-bold text-teal-300 tracking-wide';
      }
      if (countdownDot) countdownDot.className = 'w-2 h-2 rounded-full bg-teal-400 pulse-live';
      const elapsedPct = Math.min(100, Math.max(2, ((intervalSecs - secsLeft) / intervalSecs) * 100));
      if (progressEl) progressEl.style.width = `${elapsedPct.toFixed(1)}%`;
    }

    if (lastRunSummary) {
      if (st.last_cycle_time) {
        const agoSecs = Math.max(0, Math.floor((Date.now() - new Date(st.last_cycle_time).getTime()) / 1000));
        const agoLabel = agoSecs < 60 ? `${agoSecs}s ago` : `${Math.floor(agoSecs / 60)}m ago`;
        lastRunSummary.textContent = `Last cycle: ${agoLabel} (+${st.last_cycle_new_items || 0} new)`;
      } else {
        lastRunSummary.textContent = 'Last cycle: Waiting for first run';
      }
    }

    if (nextRunClock && st.next_run_time) {
      const dt = new Date(st.next_run_time);
      nextRunClock.textContent = `At ${dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
    }

    // 3. Update Constant Continuous Scrape Live Panel
    const statusPill = document.getElementById('constant-scrape-status-pill');
    const livePanel = document.getElementById('constant-scrape-live-panel');
    const liveActivity = document.getElementById('constant-scrape-live-activity');
    const liveBadge = document.getElementById('constant-scrape-live-badge');
    const cyclesCountEl = document.getElementById('constant-scrape-cycles-count');
    const newCountEl = document.getElementById('constant-scrape-new-count');
    const cardEl = document.getElementById('constant-scrape-card');

    if (statusPill) {
      if (isConstant) {
        statusPill.textContent = 'ACTIVE 24/7';
        statusPill.className = 'px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40 pulse-live';
      } else {
        statusPill.textContent = 'OFF';
        statusPill.className = 'px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-800 text-slate-400 border border-slate-700';
      }
    }

    if (cardEl) {
      cardEl.className = isConstant
        ? 'p-4 rounded-xl bg-amber-950/15 border border-amber-500/50 transition space-y-2.5 cursor-pointer select-none shadow-lg shadow-amber-950/10'
        : 'p-4 rounded-xl bg-slate-950/80 border border-slate-800 hover:border-amber-500/40 transition space-y-2.5 cursor-pointer select-none';
    }

    if (livePanel) {
      if (isConstant) {
        livePanel.classList.remove('hidden');
        if (cyclesCountEl) cyclesCountEl.textContent = String(st.constant_scrape_cycles || 0);
        if (newCountEl) newCountEl.textContent = `+${st.constant_scrape_total_new || 0}`;

        if (isRunning && st.current_search_text) {
          if (liveActivity) {
            liveActivity.textContent = st.total_searches_in_cycle > 0
              ? `⚡ Scraping (${st.current_search_index}/${st.total_searches_in_cycle}): ${st.current_search_text}`
              : `⚡ ${st.current_search_text}`;
          }
          if (liveBadge) liveBadge.textContent = 'SCRAPING';
        } else if (st.constant_scrape_phase === 'cooldown') {
          let coolSecs = st.constant_cooldown_seconds ?? 10;
          if (liveActivity) liveActivity.textContent = `⏳ Polite anti-ban pause before next loop (${coolSecs}s)…`;
          if (liveBadge) liveBadge.textContent = 'PAUSE';
        } else {
          if (liveActivity) liveActivity.textContent = '🔄 Continuous 24/7 scrape loop active…';
          if (liveBadge) liveBadge.textContent = 'ACTIVE';
        }
      } else {
        livePanel.classList.add('hidden');
      }
    }
  }

  updateSchedulerModalControls() {
    if (!this.schedulerStatus) return;

    if (this.schedulerStatus.interval_minutes) {
      const badge = document.getElementById('scheduler-current-interval-badge');
      if (badge) badge.textContent = `Every ${this.schedulerStatus.interval_minutes}m`;
      document.querySelectorAll('#scheduler-interval-pills button').forEach(pill => {
        const m = parseInt(pill.dataset.minutes, 10);
        if (m === this.schedulerStatus.interval_minutes) {
          pill.classList.add('active');
        } else {
          pill.classList.remove('active');
        }
      });
    }

    const constantToggle = document.getElementById('toggle-constant-scrape');
    if (constantToggle) {
      constantToggle.checked = Boolean(this.schedulerStatus.constant_scrape);
    }
  }

  renderBatchTargets() {
    const container = document.getElementById('batch-scrape-checklist');
    if (!container) return;

    if (this.searches.length === 0) {
      container.innerHTML = '<div class="text-xs text-slate-500 col-span-2 py-3 text-center">No tracked items found.</div>';
      return;
    }

    if (!this.batchTargetsInitialized) {
      this.searches.forEach(s => this.batchTargets.add(s.id));
      this.batchTargetsInitialized = true;
    }

    container.innerHTML = this.searches.map(s => {
      const isChecked = this.batchTargets.has(s.id);
      return `
        <label class="flex items-center space-x-2 p-1.5 rounded-lg border text-xs cursor-pointer ${
          isChecked 
            ? 'bg-teal-500/10 border-teal-500/40 text-teal-200' 
            : 'bg-slate-900 border-slate-800 text-slate-400'
        }">
          <input type="checkbox" class="custom-checkbox" ${isChecked ? 'checked' : ''} onchange="app.toggleBatchTarget(${s.id})">
          <span class="truncate font-medium">${s.search_text}</span>
        </label>
      `;
    }).join('');
  }

  toggleBatchTarget(id) {
    if (this.batchTargets.has(id)) {
      this.batchTargets.delete(id);
    } else {
      this.batchTargets.add(id);
    }
    this.renderBatchTargets();
  }

  selectAllBatchTargets(selectAll) {
    this.batchTargetsInitialized = true;
    if (selectAll) {
      this.searches.forEach(s => this.batchTargets.add(s.id));
    } else {
      this.batchTargets.clear();
    }
    this.renderBatchTargets();
  }

  async runBatchScrape() {
    const targetIds = Array.from(this.batchTargets);
    if (targetIds.length === 0) {
      this.showToast('Please select at least one tracked item for batch scraping.', 'error');
      return;
    }

    const btn = document.getElementById('btn-run-batch-scrape');
    const icon = document.getElementById('batch-scrape-icon');
    const btnText = document.getElementById('batch-scrape-btn-text');

    if (btn) btn.disabled = true;
    if (icon) icon.classList.add('animate-spin');
    if (btnText) btnText.textContent = `Scraping ${targetIds.length} items…`;
    this.closeScraperModal();
    this.openScraperTerminal();
    this.showToast(`Starting batch scrape across ${targetIds.length} tracked items...`, 'info');

    try {
      const res = await fetch('/api/scraper/batch-scrape', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ search_ids: targetIds })
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Batch scrape failed');
      }

      const summary = await res.json();
      const blockedCount = (summary.total_spam_blocked || 0) + (summary.total_rep_blocked || 0) + (summary.total_blocked || 0);
      const searchCount = summary.total_searches || summary.scraped_searches || targetIds.length;
      this.showToast(`Batch scrape complete! Stored ${summary.total_new_items || 0} new listings (${blockedCount} filtered/blocked) across ${searchCount} searches.`, 'success');
      await this.loadSchedulerStatus();
      await this.loadSearches();
      await this.loadSniperTargetsInfo();
      if (this.currentSearchId) {
        await this.loadItemDetail();
      }
      this.closeScraperModal();
    } catch (err) {
      this.showToast(err.message, 'error');
    } finally {
      if (btn) btn.disabled = false;
      if (icon) icon.classList.remove('animate-spin');
      if (btnText) btnText.textContent = 'Run Batch Scrape Now';
      if (window.lucide) lucide.createIcons();
    }
  }

  async loadSchedulerStatus() {
    try {
      const res = await fetch('/api/scraper/schedule');
      if (!res.ok) return;
      const newStatus = await res.json();

      // Detect if a background cycle (Auto-Poll or Constant Scrape) just finished
      if (
        newStatus.last_cycle_time &&
        this.lastSeenCycleTime &&
        newStatus.last_cycle_time !== this.lastSeenCycleTime
      ) {
        this.lastSeenCycleTime = newStatus.last_cycle_time;
        this.loadSearches();
        this.loadDatabaseSummaryBadges();
        if (this.currentSearchId) {
          this.loadItemDetail();
        } else if (this.isDbExplorerActive) {
          this.loadDatabaseExplorerData();
        }
      } else if (newStatus.last_cycle_time && !this.lastSeenCycleTime) {
        this.lastSeenCycleTime = newStatus.last_cycle_time;
      }

      this.schedulerStatus = newStatus;

      const intervalText = document.getElementById('header-poll-interval-text');
      if (intervalText && this.schedulerStatus.interval_minutes) {
        intervalText.textContent = `Every ${this.schedulerStatus.interval_minutes}m`;
      }

      const constantBadge = document.getElementById('header-constant-badge');
      if (constantBadge) {
        if (this.schedulerStatus.constant_scrape) {
          constantBadge.classList.remove('hidden');
        } else {
          constantBadge.classList.add('hidden');
        }
      }

      this.updateSchedulerModalControls();
      this.renderSchedulerCountdownUI();
    } catch (err) {
      console.warn('Could not load scheduler status:', err);
    }
  }

  async setScheduleInterval(minutes) {
    document.querySelectorAll('#scheduler-interval-pills button').forEach(pill => {
      const m = parseInt(pill.dataset.minutes, 10);
      if (m === minutes) {
        pill.classList.add('active');
      } else {
        pill.classList.remove('active');
      }
    });

    const badge = document.getElementById('scheduler-current-interval-badge');
    if (badge) badge.textContent = `Every ${minutes}m`;

    const intervalText = document.getElementById('header-poll-interval-text');
    if (intervalText) intervalText.textContent = `Every ${minutes}m`;

    try {
      const res = await fetch('/api/scraper/schedule', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ interval_minutes: minutes })
      });

      if (!res.ok) throw new Error('Failed to update schedule interval');
      this.schedulerStatus = await res.json();
      this.updateSchedulerModalControls();
      this.renderSchedulerCountdownUI();
      this.showToast(`Auto-polling regularity set to every ${minutes} minutes! Countdown reset.`, 'success');
    } catch (err) {
      this.showToast(err.message, 'error');
    }
  }

  toggleConstantScrapeCard() {
    const checkbox = document.getElementById('toggle-constant-scrape');
    if (!checkbox) return;
    checkbox.checked = !checkbox.checked;
    this.toggleConstantScrape(checkbox.checked);
  }

  async toggleConstantScrape(checked) {
    const checkbox = document.getElementById('toggle-constant-scrape');
    if (checkbox) checkbox.checked = Boolean(checked);

    try {
      const res = await fetch('/api/scraper/schedule', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ constant_scrape: Boolean(checked) })
      });

      if (!res.ok) throw new Error('Failed to toggle constant scrape');
      this.schedulerStatus = await res.json();

      const constantBadge = document.getElementById('header-constant-badge');
      if (constantBadge) {
        if (checked) {
          constantBadge.classList.remove('hidden');
        } else {
          constantBadge.classList.add('hidden');
        }
      }

      this.updateSchedulerModalControls();
      this.renderSchedulerCountdownUI();

      if (checked) {
        setTimeout(() => this.loadSchedulerStatus(), 800);
      }

      this.showToast(
        checked 
          ? 'Constant Continuous Scrape activated (24/7 background loop with polite pauses).' 
          : 'Constant continuous scrape deactivated.',
        checked ? 'success' : 'info'
      );
    } catch (err) {
      this.showToast(err.message, 'error');
    }
  }

  // ── Database Controls & Month Purging ──────────────────────────────────

  openDatabaseModal() {
    const modal = document.getElementById('database-modal');
    if (modal) {
      modal.classList.remove('hidden');
      this.loadDatabaseSummary();
      if (window.lucide) lucide.createIcons();
    }
  }

  closeDatabaseModal() {
    const modal = document.getElementById('database-modal');
    if (modal) modal.classList.add('hidden');
  }

  async loadDatabaseSummary() {
    const listContainer = document.getElementById('db-monthly-list');
    if (listContainer) {
      listContainer.innerHTML = '<div class="text-xs text-slate-500 py-4 text-center">Loading database health & monthly records…</div>';
    }

    try {
      const res = await fetch('/api/database/summary');
      if (!res.ok) throw new Error('Failed to load database summary');
      const data = await res.json();

      const totalItemsEl = document.getElementById('db-stat-total-items');
      const sizeEl = document.getElementById('db-stat-size');
      const searchesEl = document.getElementById('db-stat-searches');

      if (totalItemsEl) totalItemsEl.textContent = (data.total_items || 0).toLocaleString();
      if (sizeEl) sizeEl.textContent = `${data.db_size_mb || 0} MB`;
      if (searchesEl) searchesEl.textContent = data.total_searches || 0;

      if (!listContainer) return;

      if (!data.months || data.months.length === 0) {
        listContainer.innerHTML = '<div class="text-xs text-slate-500 py-4 text-center">No monthly historical sales data found.</div>';
        return;
      }

      listContainer.innerHTML = data.months.map(m => {
        return `
          <div class="flex items-center justify-between p-3 rounded-xl bg-slate-900 border border-slate-800 hover:border-slate-700 transition">
            <div>
              <div class="flex items-center space-x-2">
                <span class="text-xs font-bold text-white">${m.label}</span>
                <span class="px-2 py-0.5 rounded text-[10px] font-mono bg-slate-800 text-teal-400 border border-slate-700">${m.count} sales</span>
              </div>
              <div class="text-[11px] text-slate-400 mt-0.5">
                Average Sold: <strong class="text-emerald-400 font-mono">£${m.avg_price.toFixed(2)}</strong> across ${m.searches_count} tracked items
              </div>
            </div>

            <button type="button" 
                    onclick="app.deleteMonthData('${m.month_key}', '${m.label}', ${m.count})"
                    class="inline-flex items-center px-3 py-1.5 rounded-lg text-xs font-semibold bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/30 transition cursor-pointer"
                    title="Delete all ${m.count} records from ${m.label}">
              <i data-lucide="trash-2" class="w-3.5 h-3.5 mr-1.5"></i>
              Delete Month
            </button>
          </div>
        `;
      }).join('');

      if (window.lucide) lucide.createIcons();
    } catch (err) {
      console.error('Failed to load database summary:', err);
      if (listContainer) {
        listContainer.innerHTML = `<div class="text-xs text-rose-400 py-4 text-center">${err.message}</div>`;
      }
    }
  }

  async deleteMonthData(monthKey, label, count) {
    if (!confirm(`Are you sure you want to permanently delete ALL ${count} recorded sales from ${label}?\n\nThis cannot be undone and will remove these items from your analytics.`)) {
      return;
    }

    try {
      const res = await fetch('/api/database/delete-month', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ month: monthKey })
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to delete month data');
      }

      const result = await res.json();
      this.showToast(result.message || `Deleted month ${label}`, 'success');

      await this.loadDatabaseSummary();
      await this.loadSearches();
      await this.loadSniperTargetsInfo();
      if (this.currentSearchId) {
        await this.loadItemDetail();
      }
    } catch (err) {
      this.showToast(err.message, 'error');
    }
  }

  async pruneOlderData(days) {
    if (!confirm(`Are you sure you want to delete all recorded listings older than ${days} days across ALL tracked items?\n\nThis action is permanent.`)) {
      return;
    }

    try {
      const res = await fetch('/api/database/prune-older-than', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ older_than_days: days })
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to prune older records');
      }

      const result = await res.json();
      this.showToast(result.message || `Pruned records older than ${days} days`, 'success');

      await this.loadDatabaseSummary();
      await this.loadSearches();
      await this.loadSniperTargetsInfo();
      if (this.currentSearchId) {
        await this.loadItemDetail();
      }
    } catch (err) {
      this.showToast(err.message, 'error');
    }
  }

  // ── Database Explorer & Archive Controller ───────────────────────────────

  async loadDatabaseSummaryBadges() {
    try {
      const res = await fetch('/api/database/analytics');
      if (!res.ok) return;
      const data = await res.json();
      const headerBadge = document.getElementById('header-db-count-badge');
      const sidebarCount = document.getElementById('sidebar-db-count-badge');
      const sidebarReplica = document.getElementById('sidebar-db-replica-badge');
      const totalFormatted = (data.total_items || 0).toLocaleString();

      if (headerBadge) headerBadge.textContent = totalFormatted;
      if (sidebarCount) sidebarCount.textContent = totalFormatted;
      if (sidebarReplica) {
        if (data.replica_items > 0) {
          sidebarReplica.textContent = `${data.replica_items} REP`;
          sidebarReplica.classList.remove('hidden');
        } else {
          sidebarReplica.classList.add('hidden');
        }
      }
    } catch (err) {
      console.debug('Failed to load database summary badges:', err);
    }
  }

  async loadDatabaseExplorerData() {
    try {
      const [analyticsRes, monthlyRes] = await Promise.all([
        fetch('/api/database/analytics'),
        fetch('/api/database/replicas/monthly')
      ]);

      if (analyticsRes.ok) {
        this.dbAnalyticsData = await analyticsRes.json();
        this.renderExplorerAnalytics(this.dbAnalyticsData);
        this.populateExplorerDropdowns(this.dbAnalyticsData);
      }

      if (monthlyRes.ok) {
        const monthlyData = await monthlyRes.json();
        this.renderMonthlyReplicaManagement(monthlyData);
      }

      await this.loadExplorerItems();
      await this.loadDatabaseSummaryBadges();
    } catch (err) {
      console.error('Failed to load database explorer data:', err);
      this.showToast('Failed to load database explorer data', 'error');
    }
  }

  populateExplorerDropdowns(data) {
    // 1. Tracked Searches dropdown
    const searchSelect = document.getElementById('db-filter-search-id');
    if (searchSelect && data.searches) {
      const currentVal = this.dbExplorerFilters.search_id;
      searchSelect.innerHTML = '<option value="">All Tracked Searches</option>' +
        data.searches.map(s => {
          const repInfo = s.replicas > 0 ? ` (${s.replicas} rep)` : '';
          return `<option value="${s.id}" ${currentVal == s.id ? 'selected' : ''}>${s.search_text} [${s.total}${repInfo}]</option>`;
        }).join('');
    }

    // 2. Months dropdown
    const monthSelect = document.getElementById('db-filter-month');
    if (monthSelect && data.monthly_trend) {
      const currentMonth = this.dbExplorerFilters.month;
      monthSelect.innerHTML = '<option value="">All Months</option>' +
        data.monthly_trend.map(m => {
          const repInfo = m.replicas > 0 ? ` (${m.replicas} rep)` : '';
          return `<option value="${m.month_key}" ${currentMonth === m.month_key ? 'selected' : ''}>${m.label} [${m.total}${repInfo}]</option>`;
        }).join('');
    }
  }

  renderExplorerAnalytics(data) {
    const totalEl = document.getElementById('db-kpi-total');
    const badgeEl = document.getElementById('db-view-total-badge');
    const soldCountEl = document.getElementById('db-kpi-sold-count');
    const activeCountEl = document.getElementById('db-kpi-active-count');
    const cleanEl = document.getElementById('db-kpi-clean');
    const cleanAvgEl = document.getElementById('db-kpi-clean-avg');
    const replicasEl = document.getElementById('db-kpi-replicas');
    const replicaRateEl = document.getElementById('db-kpi-replica-rate');
    const replicaAvgEl = document.getElementById('db-kpi-replica-avg');
    const volumeEl = document.getElementById('db-kpi-volume');
    const childCountEl = document.getElementById('db-kpi-child-count');

    const total = data.total_items || 0;
    if (totalEl) totalEl.textContent = total.toLocaleString();
    if (badgeEl) badgeEl.textContent = `${total.toLocaleString()} records`;
    if (soldCountEl) soldCountEl.textContent = `${(data.sold_count || 0).toLocaleString()} Sold`;
    if (activeCountEl) activeCountEl.textContent = `${(data.active_count || 0).toLocaleString()} Active`;
    if (cleanEl) cleanEl.textContent = (data.clean_items || 0).toLocaleString();
    if (cleanAvgEl) cleanAvgEl.textContent = `£${(data.clean_avg_price || 0).toFixed(2)}`;
    if (childCountEl) childCountEl.textContent = `${(data.child_items_count || 0).toLocaleString()} Child`;
    if (replicasEl) replicasEl.textContent = (data.replica_items || 0).toLocaleString();
    if (replicaRateEl) replicaRateEl.textContent = `${data.replica_rate_pct || 0}% FLAG`;
    if (replicaAvgEl) replicaAvgEl.textContent = `£${(data.replica_avg_price || 0).toFixed(2)}`;
    if (volumeEl) volumeEl.textContent = `£${Math.round(data.total_volume_gbp || 0).toLocaleString()}`;

    // Render interactive Flag Reason Diagnostics chips
    const chipsContainer = document.getElementById('db-reasons-chips-container');
    const activeReasonLabel = document.getElementById('db-active-reason-label');
    if (!chipsContainer) return;

    const currentReason = this.dbExplorerFilters.replica_reason;
    const currentReplicaFilter = this.dbExplorerFilters.replica_filter;

    // All Items Chip
    const isAllActive = currentReplicaFilter === 'all';
    let chipsHtml = `
      <button type="button" 
              onclick="app.setDbReasonFilter('all_items')" 
              class="db-reason-chip inline-flex items-center px-3 py-1.5 rounded-lg border text-xs font-medium cursor-pointer transition ${isAllActive ? 'bg-indigo-500/20 text-indigo-300 border-indigo-500 active' : 'bg-slate-950 text-slate-300 border-slate-800 hover:border-slate-700'}">
        <i data-lucide="layers" class="w-3.5 h-3.5 mr-1.5 text-indigo-400"></i>
        <span>All Items</span>
        <span class="ml-1.5 px-1.5 py-0.2 rounded-full text-[10px] font-mono bg-slate-900 border border-slate-750 text-slate-400">${total}</span>
      </button>
    `;

    // All Replicas Chip
    const isAllReplicasActive = currentReplicaFilter === 'replica' && !currentReason;
    chipsHtml += `
      <button type="button" 
              onclick="app.setDbReasonFilter('all_replicas')" 
              class="db-reason-chip inline-flex items-center px-3 py-1.5 rounded-lg border text-xs font-medium cursor-pointer transition ${isAllReplicasActive ? 'bg-rose-500/20 text-rose-300 border-rose-500 active' : 'bg-slate-950 text-rose-400 border-rose-500/30 hover:border-rose-500/60'}">
        <i data-lucide="shield-alert" class="w-3.5 h-3.5 mr-1.5 text-rose-400"></i>
        <span>All Caught Replicas</span>
        <span class="ml-1.5 px-1.5 py-0.2 rounded-full text-[10px] font-mono bg-rose-500/20 border border-rose-500/40 text-rose-300">${data.replica_items || 0}</span>
      </button>
    `;

    // Individual Reason Chips
    if (data.reasons_breakdown && data.reasons_breakdown.length > 0) {
      data.reasons_breakdown.forEach(r => {
        const isChipActive = currentReplicaFilter === 'replica' && currentReason === r.key;
        chipsHtml += `
          <button type="button" 
                  onclick="app.setDbReasonFilter('${r.key}')" 
                  class="db-reason-chip inline-flex items-center px-3 py-1.5 rounded-lg border text-xs font-medium cursor-pointer transition ${isChipActive ? 'bg-teal-500/20 text-teal-300 border-teal-500 active' : 'bg-slate-950 text-slate-300 border-slate-800 hover:border-slate-700'}">
            <i data-lucide="${r.icon || 'tag'}" class="w-3.5 h-3.5 mr-1.5 text-teal-400"></i>
            <span>${r.label}</span>
            <span class="ml-1.5 px-1.5 py-0.2 rounded-full text-[10px] font-mono bg-slate-900 border border-slate-750 text-teal-300">${r.count}</span>
          </button>
        `;
      });
    }

    chipsContainer.innerHTML = chipsHtml;

    if (activeReasonLabel) {
      if (currentReplicaFilter === 'all') {
        activeReasonLabel.textContent = 'All Items (Unfiltered)';
      } else if (currentReplicaFilter === 'clean') {
        activeReasonLabel.textContent = 'Clean Market Listings Only';
      } else if (!currentReason) {
        activeReasonLabel.textContent = 'All Caught Replicas';
      } else {
        const found = data.reasons_breakdown?.find(r => r.key === currentReason);
        activeReasonLabel.textContent = found ? found.label : currentReason.toUpperCase();
      }
    }

    if (window.lucide) lucide.createIcons();
  }

  setDbReasonFilter(key) {
    if (key === 'all_items') {
      this.dbExplorerFilters.replica_filter = 'all';
      this.dbExplorerFilters.replica_reason = '';
    } else if (key === 'all_replicas') {
      if (this.dbExplorerFilters.replica_filter === 'replica' && !this.dbExplorerFilters.replica_reason) {
        // Already active -> toggle back to all items
        this.dbExplorerFilters.replica_filter = 'all';
        this.dbExplorerFilters.replica_reason = '';
      } else {
        this.dbExplorerFilters.replica_filter = 'replica';
        this.dbExplorerFilters.replica_reason = '';
      }
    } else {
      if (this.dbExplorerFilters.replica_filter === 'replica' && this.dbExplorerFilters.replica_reason === key) {
        // Already active -> toggle off back to all items
        this.dbExplorerFilters.replica_filter = 'all';
        this.dbExplorerFilters.replica_reason = '';
      } else {
        this.dbExplorerFilters.replica_filter = 'replica';
        this.dbExplorerFilters.replica_reason = key;
      }
    }

    this.updateReplicaFilterPillsUI();
    this.dbExplorerFilters.page = 1;
    if (this.dbAnalyticsData) {
      this.renderExplorerAnalytics(this.dbAnalyticsData);
    }
    this.loadExplorerItems();
  }

  renderMonthlyReplicaManagement(data) {
    const grid = document.getElementById('db-monthly-replicas-grid');
    if (!grid) return;

    if (!data.months || data.months.length === 0) {
      grid.innerHTML = `
        <div class="col-span-full p-4 rounded-xl bg-slate-950/80 border border-slate-800 text-center text-xs text-slate-400">
          <i data-lucide="check-circle" class="w-5 h-5 mx-auto mb-1 text-emerald-400"></i>
          <span>No replica listings recorded across any monthly slices. Database is clean!</span>
        </div>
      `;
      if (window.lucide) lucide.createIcons();
      return;
    }

    grid.innerHTML = data.months.map(m => {
      const hasReplicas = m.replica_count > 0;
      return `
        <div class="p-3.5 rounded-xl bg-slate-950 border ${hasReplicas ? 'border-rose-500/30' : 'border-slate-800'} space-y-2.5 flex flex-col justify-between">
          <div>
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-white">${m.label}</span>
              <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold ${hasReplicas ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30' : 'bg-slate-800 text-slate-400'}">
                ${m.replica_count} Replicas
              </span>
            </div>
            <div class="text-[11px] text-slate-400 mt-1 flex items-center justify-between">
              <span>Total Listings: <strong class="text-white font-mono">${m.total_items}</strong></span>
              <span class="text-emerald-400 font-medium">${m.clean_count} Clean</span>
            </div>
          </div>

          <div class="pt-2 border-t border-slate-900 flex items-center justify-between">
            <span class="text-[10px] text-slate-500 font-mono">${m.month_key}</span>
            <button type="button" 
                    onclick="app.deleteMonthlyReplicas('${m.month_key}', '${m.label}', ${m.replica_count})" 
                    ${!hasReplicas ? 'disabled' : ''}
                    class="inline-flex items-center px-2.5 py-1 rounded-md text-[11px] font-semibold transition cursor-pointer ${hasReplicas ? 'bg-rose-500/10 hover:bg-rose-500/20 text-rose-300 border border-rose-500/30' : 'bg-slate-900 text-slate-600 border border-slate-800 cursor-not-allowed'}">
              <i data-lucide="trash-2" class="w-3 h-3 mr-1 text-rose-400"></i>
              <span>Purge Month Replicas</span>
            </button>
          </div>
        </div>
      `;
    }).join('');

    if (window.lucide) lucide.createIcons();
  }

  toggleReplicaManagementPanel() {
    const panel = document.getElementById('db-replica-management-panel');
    const btnText = document.getElementById('btn-db-toggle-mgmt-text');
    if (!panel) return;

    this.isReplicaMgmtOpen = !this.isReplicaMgmtOpen;
    if (this.isReplicaMgmtOpen) {
      panel.classList.remove('hidden');
      if (btnText) btnText.textContent = 'Hide Cleanup';
    } else {
      panel.classList.add('hidden');
      if (btnText) btnText.textContent = 'Monthly Cleanup';
    }
  }

  async loadExplorerItems() {
    const tableBody = document.getElementById('db-items-table-body');
    const tableLabel = document.getElementById('db-table-count-label');
    if (tableLabel) tableLabel.textContent = 'Loading records…';

    try {
      const params = new URLSearchParams();
      if (this.dbExplorerFilters.q) params.set('q', this.dbExplorerFilters.q);
      if (this.dbExplorerFilters.search_id) params.set('search_id', this.dbExplorerFilters.search_id);
      if (this.dbExplorerFilters.status_filter && this.dbExplorerFilters.status_filter !== 'all') {
        params.set('status_filter', this.dbExplorerFilters.status_filter);
      }
      if (this.dbExplorerFilters.replica_filter && this.dbExplorerFilters.replica_filter !== 'all') {
        params.set('replica_filter', this.dbExplorerFilters.replica_filter);
      }
      if (this.dbExplorerFilters.replica_reason) {
        params.set('replica_reason', this.dbExplorerFilters.replica_reason);
      }
      if (this.dbExplorerFilters.photo_key) {
        params.set('photo_key', this.dbExplorerFilters.photo_key);
      }
      if (this.dbExplorerFilters.child_filter && this.dbExplorerFilters.child_filter !== 'all') {
        params.set('child_filter', this.dbExplorerFilters.child_filter);
      }
      if (this.dbExplorerFilters.month) params.set('month', this.dbExplorerFilters.month);
      if (this.dbExplorerFilters.min_price) params.set('min_price', this.dbExplorerFilters.min_price);
      if (this.dbExplorerFilters.max_price) params.set('max_price', this.dbExplorerFilters.max_price);
      if (this.dbExplorerFilters.sort_by) params.set('sort_by', this.dbExplorerFilters.sort_by);
      params.set('page', String(this.dbExplorerFilters.page));
      params.set('page_size', String(this.dbExplorerFilters.page_size));

      this.updatePhotoKeyBannerUI();

      const res = await fetch(`/api/database/items?${params.toString()}`);
      if (!res.ok) throw new Error('Failed to fetch database items');
      const data = await res.json();

      this.renderExplorerItems(data);
    } catch (err) {
      console.error('Failed to load explorer items:', err);
      if (tableBody) {
        tableBody.innerHTML = `
          <tr>
            <td colspan="7" class="py-12 text-center text-rose-400 text-xs">
              ${err.message}
            </td>
          </tr>
        `;
      }
    }
  }

  renderExplorerItems(data) {
    const tableBody = document.getElementById('db-items-table-body');
    const countLabel = document.getElementById('db-table-count-label');
    const summaryEl = document.getElementById('db-pagination-summary');
    const paginationButtons = document.getElementById('db-pagination-buttons');
    if (!tableBody) return;

    const items = data.items || [];
    const totalCount = data.total_count || 0;
    const page = data.page || 1;
    const pageSize = data.page_size || 50;
    const totalPages = data.total_pages || 1;

    if (countLabel) {
      countLabel.textContent = `${items.length} of ${totalCount.toLocaleString()} items`;
    }

    if (items.length === 0) {
      tableBody.innerHTML = `
        <tr>
          <td colspan="7" class="py-16 text-center text-slate-500">
            <div class="flex flex-col items-center justify-center space-y-2.5">
              <div class="w-10 h-10 rounded-full bg-slate-900 border border-slate-800 flex items-center justify-center text-slate-500">
                <i data-lucide="filter-x" class="w-5 h-5"></i>
              </div>
              <p class="text-sm font-semibold text-slate-300">No database items matched your filters</p>
              <p class="text-xs text-slate-500">Try adjusting your search terms, price thresholds, or status toggles.</p>
              <button type="button" onclick="app.resetDbFilters()" class="mt-2 px-3 py-1.5 rounded-lg text-xs font-semibold bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 transition cursor-pointer">
                Reset All Filters
              </button>
            </div>
          </td>
        </tr>
      `;
      if (summaryEl) summaryEl.textContent = 'Showing 0 to 0 of 0 items';
      if (paginationButtons) paginationButtons.innerHTML = '';
      if (window.lucide) lucide.createIcons();
      return;
    }

    tableBody.innerHTML = items.map(item => {
      const isReplica = Boolean(item.is_replica);
      const isSold = Boolean(item.is_sold);
      const priceText = `£${(item.price || 0).toFixed(2)}`;
      const safeTitle = (item.title || 'Untitled Listing').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
      const safeVintedId = item.vinted_item_id || '';
      const fallbackSvg = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0OCIgaGVpZ2h0PSI0OCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9IiM0NzU1NjkiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cmVjdCB3aWR0aD0iMTgiIGhlaWdodD0iMTgiIHg9IjMiIHk9IjMiIHJ4PSIyIiByeT0iMiIvPjxjaXJjbGUgY3g9IjkiIGN5PSI5IiByPSIyIi8+PHBhdGggZD0ibTIxIDE1LTMuMDg2LTMuMDg2YTIgMiAwIDAgMC0yLjgyOCAwbC02IDYiLz48L3N2Zz4=";

      const reasonLower = (item.replica_reason || '').toLowerCase();
      const hasDupPhotoFlag = Boolean(
        item.photo_key && (
          item.is_duplicate_photo ||
          (isReplica && (reasonLower.includes('photo') || reasonLower.includes('image') || reasonLower.includes('stock')))
        )
      );
      const dupCountBadge = (item.duplicate_photo_count && item.duplicate_photo_count > 1) ? ` (${item.duplicate_photo_count})` : '';
      const safePhotoKey = (item.photo_key || '').replace(/'/g, "\\'");
      const isFilteringThisPhoto = Boolean(this.dbExplorerFilters.photo_key && this.dbExplorerFilters.photo_key === item.photo_key);

      // Format capture date
      let dateFormatted = 'Unknown date';
      let relativeTime = '';
      if (item.captured_at) {
        try {
          const dt = new Date(item.captured_at);
          dateFormatted = dt.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
          const diffMinutes = Math.floor((Date.now() - dt.getTime()) / 60000);
          if (diffMinutes < 60) {
            relativeTime = `${diffMinutes}m ago`;
          } else if (diffMinutes < 1440) {
            relativeTime = `${Math.floor(diffMinutes / 60)}h ago`;
          } else {
            relativeTime = `${Math.floor(diffMinutes / 1440)}d ago`;
          }
        } catch (e) {
          dateFormatted = item.captured_at;
        }
      }

      return `
        <tr class="db-table-row border-b border-slate-850 hover:bg-slate-900/60 transition group">
          
          <!-- Image Thumbnail -->
          <td class="py-3 px-4">
            <div class="db-thumb-wrapper w-12 h-12 rounded-lg bg-slate-900 border ${hasDupPhotoFlag ? 'border-amber-500/50 ring-1 ring-amber-500/20' : 'border-slate-800'} overflow-hidden flex items-center justify-center shrink-0">
              <img src="${item.photo_url || fallbackSvg}" 
                   alt="" 
                   loading="lazy"
                   onerror="this.onerror=null;this.src='${fallbackSvg}';" 
                   class="w-full h-full object-cover">
            </div>
          </td>

          <!-- Item Title & Details -->
          <td class="py-3 px-4">
            <div class="space-y-1">
              <a href="${item.url || '#'}" 
                 target="_blank" 
                 rel="noopener noreferrer" 
                 class="font-semibold text-white hover:text-teal-300 transition line-clamp-1 inline-flex items-center space-x-1.5"
                 title="${safeTitle}">
                <span class="truncate">${safeTitle}</span>
                <i data-lucide="external-link" class="w-3 h-3 text-slate-500 group-hover:text-teal-400 shrink-0"></i>
              </a>

              <div class="flex flex-wrap items-center gap-1.5 text-[10px]">
                ${item.brand ? `<span class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700/60 font-medium">${item.brand}</span>` : ''}
                ${item.size ? `<span class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700/60">${item.size}</span>` : ''}
                ${item.is_child_size ? `<span class="px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30 font-semibold" title="${(item.child_size_reason || 'Child / Youth Size').replace(/"/g, '&quot;')}">👶 Child Size</span>` : ''}
                ${item.condition ? `<span class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700/60">${item.condition}</span>` : ''}
                ${item.seller_username ? `<span class="text-slate-500">@${item.seller_username}</span>` : ''}
                <span class="text-slate-600 font-mono">#${safeVintedId}</span>
              </div>
            </div>
          </td>

          <!-- Tracked Search Term -->
          <td class="py-3 px-4">
            <span class="inline-flex items-center px-2 py-1 rounded-md text-[11px] font-medium bg-slate-900 text-slate-300 border border-slate-800">
              <i data-lucide="search" class="w-3 h-3 mr-1 text-slate-500"></i>
              <span class="truncate max-w-[130px]" title="${item.search_text}">${item.search_text}</span>
            </span>
          </td>

          <!-- Price & Status -->
          <td class="py-3 px-4">
            <div class="space-y-1">
              <div class="flex items-center space-x-1.5">
                <span class="text-sm font-bold font-mono text-white tabular-nums tracking-tight">${priceText}</span>
                <button type="button" 
                        onclick="app.editItemPrice(${item.id})" 
                        title="Edit / mark down exact price" 
                        class="text-slate-500 hover:text-cyan-400 p-0.5 rounded transition">
                  <i data-lucide="edit-3" class="w-3 h-3"></i>
                </button>
              </div>
              <div class="flex items-center space-x-1.5">
                <button type="button" 
                        onclick="app.toggleItemSold(${item.id})" 
                        class="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold cursor-pointer transition hover:opacity-80 ${isSold ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 hover:bg-emerald-500/20' : 'bg-cyan-500/10 text-cyan-300 border border-cyan-500/20 hover:bg-cyan-500/20'}"
                        title="Click to toggle between SOLD and ACTIVE (auto-syncs live sold price)">
                  <span class="w-1.5 h-1.5 rounded-full mr-1.5 ${isSold ? 'bg-emerald-400' : 'bg-cyan-400'}"></span>
                  ${isSold ? 'SOLD' : 'ACTIVE'}
                </button>
                <button type="button" 
                        onclick="app.verifyItemStatus(${item.id})" 
                        id="btn-verify-status-${item.id}"
                        class="p-0.5 rounded hover:bg-slate-800 text-slate-500 hover:text-cyan-300 transition cursor-pointer"
                        title="Live verify on Vinted UK (checks if listing is active with Buy Now or has sold)">
                  <i data-lucide="check-check" class="w-3.5 h-3.5"></i>
                </button>
              </div>
            </div>
          </td>

          <!-- Anti-Rep Verification / Diagnostics -->
          <td class="py-3 px-4">
            ${isReplica ? `
              <div class="space-y-1.5">
                <div class="flex flex-wrap items-center gap-1.5">
                  <span class="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold bg-rose-500/20 text-rose-300 border border-rose-500/30">
                    <i data-lucide="shield-alert" class="w-3 h-3 mr-1 text-rose-400"></i>
                    REPLICA FLAGGED
                  </span>
                  <button type="button" 
                          onclick="app.toggleItemReplica(${item.id})" 
                          class="text-[10px] text-slate-400 hover:text-emerald-300 underline cursor-pointer"
                          title="Unflag item: mark as authentic">
                    Unflag
                  </button>
                </div>
                <p class="text-[11px] text-rose-300/90 leading-tight font-medium bg-rose-950/40 p-1.5 rounded-md border border-rose-900/50 line-clamp-2" title="${(item.replica_reason || '').replace(/"/g, '&quot;')}">
                  ${item.replica_reason || 'Failed heuristic verification'}
                </p>
                ${hasDupPhotoFlag ? `
                  <div class="pt-0.5">
                    ${isFilteringThisPhoto ? `
                      <button type="button"
                              onclick="app.clearDuplicatePhotoFilter()"
                              class="inline-flex items-center px-2.5 py-1 rounded-md text-[10px] font-bold bg-amber-500/25 hover:bg-amber-500/35 text-amber-200 border border-amber-500/50 transition cursor-pointer shadow-sm"
                              title="Clear active repeating image filter">
                        <i data-lucide="x-circle" class="w-3 h-3 mr-1 text-amber-300"></i>
                        <span>Clear Image Filter${dupCountBadge}</span>
                      </button>
                    ` : `
                      <button type="button"
                              onclick="app.filterByDuplicatePhoto('${safePhotoKey}', '${safeVintedId}')"
                              class="inline-flex items-center px-2.5 py-1 rounded-md text-[10px] font-bold bg-amber-500/15 hover:bg-amber-500/25 text-amber-300 border border-amber-500/35 transition cursor-pointer shadow-sm"
                              title="Filter the entire database for all items sharing this repeating image">
                        <i data-lucide="images" class="w-3 h-3 mr-1 text-amber-400"></i>
                        <span>Find All With This Image${dupCountBadge}</span>
                      </button>
                    `}
                  </div>
                ` : ''}
              </div>
            ` : `
              <div class="space-y-1.5">
                <div class="flex items-center space-x-1.5">
                  <span class="inline-flex items-center px-2 py-1 rounded text-[11px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                    <i data-lucide="check" class="w-3 h-3 mr-1 text-emerald-400"></i>
                    Verified Clean
                  </span>
                  <button type="button" 
                          onclick="app.toggleItemReplica(${item.id})" 
                          class="text-[10px] text-slate-500 hover:text-rose-400 underline cursor-pointer opacity-0 group-hover:opacity-100 transition"
                          title="Flag this item as a replica">
                    Flag
                  </button>
                </div>
                ${hasDupPhotoFlag ? `
                  <div>
                    <button type="button"
                            onclick="app.filterByDuplicatePhoto('${safePhotoKey}', '${safeVintedId}')"
                            class="inline-flex items-center px-2.5 py-1 rounded-md text-[10px] font-bold bg-amber-500/15 hover:bg-amber-500/25 text-amber-300 border border-amber-500/35 transition cursor-pointer shadow-sm"
                            title="Filter the entire database for all items sharing this repeating image">
                      <i data-lucide="images" class="w-3 h-3 mr-1 text-amber-400"></i>
                      <span>Find All With This Image${dupCountBadge}</span>
                    </button>
                  </div>
                ` : ''}
              </div>
            `}
          </td>

          <!-- Captured At -->
          <td class="py-3 px-4">
            <div class="text-[11px] text-slate-300 font-mono">${dateFormatted}</div>
            ${relativeTime ? `<div class="text-[10px] text-slate-500">${relativeTime}</div>` : ''}
          </td>

          <!-- Actions -->
          <td class="py-3 px-4 text-right">
            <button type="button" 
                    onclick="app.deleteExplorerItem(${item.id})" 
                    class="p-1.5 rounded-lg text-slate-400 hover:text-rose-400 hover:bg-rose-500/10 transition cursor-pointer" 
                    title="Delete record from database">
              <i data-lucide="trash-2" class="w-4 h-4"></i>
            </button>
          </td>

        </tr>
      `;
    }).join('');

    // Pagination summary
    const startIdx = (page - 1) * pageSize + 1;
    const endIdx = Math.min(page * pageSize, totalCount);
    if (summaryEl) {
      summaryEl.textContent = `Showing ${startIdx.toLocaleString()} to ${endIdx.toLocaleString()} of ${totalCount.toLocaleString()} items`;
    }

    // Pagination buttons
    if (paginationButtons) {
      let btnsHtml = `
        <button type="button" 
                onclick="app.changeDbPage(${page - 1})" 
                ${page <= 1 ? 'disabled' : ''} 
                class="px-2.5 py-1 rounded-md text-xs font-semibold bg-slate-900 border border-slate-800 text-slate-300 hover:bg-slate-800 disabled:opacity-40 disabled:cursor-not-allowed transition cursor-pointer">
          Previous
        </button>
      `;

      // Render page numbers
      const maxButtons = 5;
      let startPage = Math.max(1, page - 2);
      let endPage = Math.min(totalPages, startPage + maxButtons - 1);
      if (endPage - startPage < maxButtons - 1) {
        startPage = Math.max(1, endPage - maxButtons + 1);
      }

      for (let p = startPage; p <= endPage; p++) {
        const isCurrent = p === page;
        btnsHtml += `
          <button type="button" 
                  onclick="app.changeDbPage(${p})" 
                  class="w-7 h-7 rounded-md text-xs font-mono font-semibold transition cursor-pointer ${isCurrent ? 'bg-indigo-600 text-white shadow-sm' : 'bg-slate-900 border border-slate-800 text-slate-400 hover:text-white hover:bg-slate-800'}">
            ${p}
          </button>
        `;
      }

      btnsHtml += `
        <button type="button" 
                onclick="app.changeDbPage(${page + 1})" 
                ${page >= totalPages ? 'disabled' : ''} 
                class="px-2.5 py-1 rounded-md text-xs font-semibold bg-slate-900 border border-slate-800 text-slate-300 hover:bg-slate-800 disabled:opacity-40 disabled:cursor-not-allowed transition cursor-pointer">
          Next
        </button>
      `;

      paginationButtons.innerHTML = btnsHtml;
    }

    if (window.lucide) lucide.createIcons();
  }

  // ── Database Explorer Filter Handlers ────────────────────────────────────

  updatePhotoKeyBannerUI() {
    const banner = document.getElementById('db-photo-key-banner');
    const label = document.getElementById('db-photo-key-label');
    if (!banner) return;
    if (this.dbExplorerFilters.photo_key) {
      banner.classList.remove('hidden');
      if (label) label.textContent = this.dbExplorerFilters.photo_key;
    } else {
      banner.classList.add('hidden');
      if (label) label.textContent = '';
    }
    if (window.lucide) lucide.createIcons();
  }

  async filterByDuplicatePhoto(photoKey, sourceVintedId = '') {
    if (!photoKey) return;

    // Reset all other filters so we search the entire database across all searches & statuses
    this.dbExplorerFilters = {
      q: '',
      search_id: '',
      status_filter: 'all',
      replica_filter: 'all',
      replica_reason: '',
      photo_key: photoKey,
      child_filter: 'all',
      month: '',
      min_price: '',
      max_price: '',
      sort_by: 'newest',
      page: 1,
      page_size: this.dbExplorerFilters.page_size || 50,
    };

    const qInput = document.getElementById('db-filter-query');
    const searchSelect = document.getElementById('db-filter-search-id');
    const monthSelect = document.getElementById('db-filter-month');
    const minPriceInput = document.getElementById('db-filter-min-price');
    const maxPriceInput = document.getElementById('db-filter-max-price');
    const sortSelect = document.getElementById('db-filter-sort');
    const clearBtn = document.getElementById('db-query-clear-btn');

    if (qInput) qInput.value = '';
    if (searchSelect) searchSelect.value = '';
    if (monthSelect) monthSelect.value = '';
    if (minPriceInput) minPriceInput.value = '';
    if (maxPriceInput) maxPriceInput.value = '';
    if (sortSelect) sortSelect.value = 'newest';
    if (clearBtn) clearBtn.classList.add('hidden');

    this.updateReplicaFilterPillsUI();
    this.updateChildFilterPillsUI();
    this.updatePhotoKeyBannerUI();

    const allStatusBtn = document.getElementById('db-status-btn-all');
    const soldStatusBtn = document.getElementById('db-status-btn-sold');
    const activeStatusBtn = document.getElementById('db-status-btn-active');
    if (allStatusBtn) allStatusBtn.className = 'px-2.5 py-1 rounded-md text-[11px] font-semibold text-white bg-slate-800 transition';
    if (soldStatusBtn) soldStatusBtn.className = 'px-2.5 py-1 rounded-md text-[11px] font-semibold text-slate-400 hover:text-white transition';
    if (activeStatusBtn) activeStatusBtn.className = 'px-2.5 py-1 rounded-md text-[11px] font-semibold text-slate-400 hover:text-white transition';

    if (!this.isDbExplorerActive) {
      await this.showDatabaseExplorerView();
    } else {
      if (this.dbAnalyticsData) {
        this.renderExplorerAnalytics(this.dbAnalyticsData);
      }
      await this.loadExplorerItems();
    }

    const bannerEl = document.getElementById('db-photo-key-banner');
    if (bannerEl) {
      bannerEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    this.showToast(
      `Filtering entire database for all listings sharing repeating image (${photoKey})`,
      'info'
    );
  }

  clearDuplicatePhotoFilter() {
    this.dbExplorerFilters.photo_key = '';
    this.dbExplorerFilters.page = 1;
    this.updatePhotoKeyBannerUI();
    this.loadExplorerItems();
    this.showToast('Cleared repeating image filter', 'info');
  }

  handleDbSearchInput(val) {
    const clearBtn = document.getElementById('db-query-clear-btn');
    if (clearBtn) {
      if (val && val.trim()) {
        clearBtn.classList.remove('hidden');
      } else {
        clearBtn.classList.add('hidden');
      }
    }

    clearTimeout(this.dbExplorerDebounceTimer);
    this.dbExplorerDebounceTimer = setTimeout(() => {
      this.dbExplorerFilters.q = (val || '').trim();
      this.dbExplorerFilters.page = 1;
      this.loadExplorerItems();
    }, 250);
  }

  clearDbQueryInput() {
    const input = document.getElementById('db-filter-query');
    if (input) input.value = '';
    const clearBtn = document.getElementById('db-query-clear-btn');
    if (clearBtn) clearBtn.classList.add('hidden');

    this.dbExplorerFilters.q = '';
    this.dbExplorerFilters.page = 1;
    this.loadExplorerItems();
  }

  handleDbFilterChange() {
    const qInput = document.getElementById('db-filter-query');
    const searchSelect = document.getElementById('db-filter-search-id');
    const monthSelect = document.getElementById('db-filter-month');
    const minPriceInput = document.getElementById('db-filter-min-price');
    const maxPriceInput = document.getElementById('db-filter-max-price');
    const sortSelect = document.getElementById('db-filter-sort');

    if (qInput) this.dbExplorerFilters.q = (qInput.value || '').trim();
    if (searchSelect) this.dbExplorerFilters.search_id = searchSelect.value;
    if (monthSelect) this.dbExplorerFilters.month = monthSelect.value;
    if (minPriceInput) this.dbExplorerFilters.min_price = minPriceInput.value;
    if (maxPriceInput) this.dbExplorerFilters.max_price = maxPriceInput.value;
    if (sortSelect) this.dbExplorerFilters.sort_by = sortSelect.value;

    this.dbExplorerFilters.page = 1;
    this.loadExplorerItems();
  }

  setDbStatusFilter(status) {
    this.dbExplorerFilters.status_filter = status;
    const allBtn = document.getElementById('db-status-btn-all');
    const soldBtn = document.getElementById('db-status-btn-sold');
    const activeBtn = document.getElementById('db-status-btn-active');

    const setActive = (btn, isActive) => {
      if (!btn) return;
      if (isActive) {
        btn.className = 'px-2.5 py-1 rounded-md text-[11px] font-semibold text-white bg-slate-800 transition';
      } else {
        btn.className = 'px-2.5 py-1 rounded-md text-[11px] font-semibold text-slate-400 hover:text-white transition';
      }
    };

    setActive(allBtn, status === 'all');
    setActive(soldBtn, status === 'sold');
    setActive(activeBtn, status === 'active');

    this.dbExplorerFilters.page = 1;
    this.loadExplorerItems();
  }

  setDbReplicaFilter(replica) {
    this.dbExplorerFilters.replica_filter = replica;
    this.dbExplorerFilters.replica_reason = '';
    this.updateReplicaFilterPillsUI();

    if (this.dbAnalyticsData) {
      this.renderExplorerAnalytics(this.dbAnalyticsData);
    }

    this.dbExplorerFilters.page = 1;
    this.loadExplorerItems();
  }

  updateReplicaFilterPillsUI() {
    const allBtn = document.getElementById('db-replica-btn-all');
    const cleanBtn = document.getElementById('db-replica-btn-clean');
    const repBtn = document.getElementById('db-replica-btn-replica');
    const replica = this.dbExplorerFilters.replica_filter;

    const setActive = (btn, isActive, activeColor = 'text-white') => {
      if (!btn) return;
      if (isActive) {
        btn.className = `px-2.5 py-1 rounded-md text-[11px] font-semibold ${activeColor} bg-slate-800 transition`;
      } else {
        btn.className = 'px-2.5 py-1 rounded-md text-[11px] font-semibold text-slate-400 hover:text-white transition';
      }
    };

    setActive(allBtn, replica === 'all');
    setActive(cleanBtn, replica === 'clean', 'text-emerald-300');
    setActive(repBtn, replica === 'replica', 'text-rose-300');
  }

  setDbChildFilter(childFilter) {
    this.dbExplorerFilters.child_filter = childFilter;
    this.updateChildFilterPillsUI();
    this.dbExplorerFilters.page = 1;
    this.loadExplorerItems();
  }

  updateChildFilterPillsUI() {
    const allBtn = document.getElementById('db-child-btn-all');
    const adultBtn = document.getElementById('db-child-btn-adult');
    const childBtn = document.getElementById('db-child-btn-child');
    const childFilter = this.dbExplorerFilters.child_filter;

    const setActive = (btn, isActive, activeColor = 'text-white') => {
      if (!btn) return;
      if (isActive) {
        btn.className = `px-2.5 py-1 rounded-md text-[11px] font-semibold ${activeColor} bg-slate-800 transition`;
      } else {
        btn.className = `px-2.5 py-1 rounded-md text-[11px] font-semibold text-slate-400 hover:${activeColor} transition`;
      }
    };

    setActive(allBtn, childFilter === 'all');
    setActive(adultBtn, childFilter === 'adult', 'text-cyan-300');
    setActive(childBtn, childFilter === 'child', 'text-amber-300');
  }

  resetDbFilters() {
    this.dbExplorerFilters = {
      q: '',
      search_id: '',
      status_filter: 'all',
      replica_filter: 'all',
      replica_reason: '',
      photo_key: '',
      child_filter: 'all',
      month: '',
      min_price: '',
      max_price: '',
      sort_by: 'newest',
      page: 1,
      page_size: this.dbExplorerFilters.page_size || 50,
    };

    const qInput = document.getElementById('db-filter-query');
    const searchSelect = document.getElementById('db-filter-search-id');
    const monthSelect = document.getElementById('db-filter-month');
    const minPriceInput = document.getElementById('db-filter-min-price');
    const maxPriceInput = document.getElementById('db-filter-max-price');
    const sortSelect = document.getElementById('db-filter-sort');
    const clearBtn = document.getElementById('db-query-clear-btn');

    if (qInput) qInput.value = '';
    if (searchSelect) searchSelect.value = '';
    if (monthSelect) monthSelect.value = '';
    if (minPriceInput) minPriceInput.value = '';
    if (maxPriceInput) maxPriceInput.value = '';
    if (sortSelect) sortSelect.value = 'newest';
    if (clearBtn) clearBtn.classList.add('hidden');

    this.setDbStatusFilter('all');
    this.updateReplicaFilterPillsUI();
    this.updateChildFilterPillsUI();
    this.updatePhotoKeyBannerUI();

    if (this.dbAnalyticsData) {
      this.renderExplorerAnalytics(this.dbAnalyticsData);
    }
    this.loadExplorerItems();
  }

  handleDbPageSizeChange(val) {
    this.dbExplorerFilters.page_size = parseInt(val, 10) || 50;
    this.dbExplorerFilters.page = 1;
    this.loadExplorerItems();
  }

  changeDbPage(page) {
    if (page < 1) return;
    this.dbExplorerFilters.page = page;
    this.loadExplorerItems();
  }

  // ── Pruning & Maintenance Handlers ───────────────────────────────────────

  async deleteMonthlyReplicas(monthKey, label, replicaCount) {
    if (!confirm(`Are you sure you want to permanently delete all ${replicaCount} replica records from ${label}?\n\nAuthentic market sales and historical pricing curves will NOT be affected.`)) {
      return;
    }

    try {
      const res = await fetch('/api/database/replicas/delete-month', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ month: monthKey })
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to delete replicas for month');
      }

      const result = await res.json();
      this.showToast(result.message || `Purged replicas from ${label}`, 'success');

      await this.loadDatabaseExplorerData();
      await this.loadSearches();
    } catch (err) {
      this.showToast(err.message, 'error');
    }
  }

  async purgeAllReplicas() {
    if (!confirm(`Are you sure you want to permanently PURGE ALL caught replicas from the entire database?\n\nThis will only delete counterfeit listings, leaving clean market data intact.`)) {
      return;
    }

    try {
      const res = await fetch('/api/database/replicas/purge-all', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to purge replicas');
      }

      const result = await res.json();
      this.showToast(result.message || 'Purged all caught replicas', 'success');

      await this.loadDatabaseExplorerData();
      await this.loadSearches();
    } catch (err) {
      this.showToast(err.message, 'error');
    }
  }

  async rescanDatabaseReplicas() {
    const icon = document.getElementById('db-rescan-icon');
    const textEl = document.getElementById('db-rescan-text');
    if (icon) icon.classList.add('animate-spin');
    if (textEl) textEl.textContent = 'Auditing…';

    try {
      this.showToast('Auditing entire database against Anti-Rep heuristics…', 'info');
      const res = await fetch('/api/database/rescan-replicas', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to rescan database');
      }

      const result = await res.json();
      this.showToast(result.message || 'Database audit complete', 'success');

      await this.loadDatabaseExplorerData();
      await this.loadSearches();
    } catch (err) {
      this.showToast(err.message, 'error');
    } finally {
      if (icon) icon.classList.remove('animate-spin');
      if (textEl) textEl.textContent = 'Audit Anti-Rep';
      if (window.lucide) lucide.createIcons();
    }
  }

  async deleteExplorerItem(itemId, title) {
    const promptMsg = title 
      ? `Delete item '${title}' (ID: ${itemId}) from the database?\n\nThis cannot be undone.`
      : `Delete item ID #${itemId} from the database?\n\nThis cannot be undone.`;
    if (!confirm(promptMsg)) {
      return;
    }

    try {
      const res = await fetch(`/api/database/items/${itemId}`, {
        method: 'DELETE'
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to delete item');
      }

      const result = await res.json();
      this.showToast(result.message || 'Item deleted', 'info');

      await this.loadExplorerItems();
      await this.loadDatabaseSummaryBadges();
      // Refresh analytics in background
      fetch('/api/database/analytics').then(r => r.json()).then(d => {
        this.dbAnalyticsData = d;
        this.renderExplorerAnalytics(d);
      }).catch(() => {});
    } catch (err) {
      this.showToast(err.message, 'error');
    }
  }

  async toggleItemSold(itemId) {
    try {
      const item = (this.dbExplorerItems || []).find(it => it.id === itemId);
      let queryParam = '';
      if (item && !item.is_sold) {
        const input = prompt(
          `Mark item #${itemId} as SOLD.\nEnter final sale price (£) [Leave blank to auto-detect from Vinted]:`,
          item.price ? item.price.toFixed(2) : ''
        );
        if (input === null) return; // user cancelled
        const parsed = parseFloat(input.trim());
        if (!isNaN(parsed) && parsed > 0) {
          queryParam = `?sold_price=${parsed.toFixed(2)}`;
        }
      }

      const res = await fetch(`/api/database/items/${itemId}/toggle-sold${queryParam}`, {
        method: 'POST'
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to toggle sold status');
      }

      const result = await res.json();
      this.showToast(result.message || `Item status updated to ${result.status}`, 'success');

      await this.loadExplorerItems();
      await this.loadDatabaseSummaryBadges();
      // Refresh analytics in background
      fetch('/api/database/analytics').then(r => r.json()).then(d => {
        this.dbAnalyticsData = d;
        this.renderExplorerAnalytics(d);
      }).catch(() => {});
    } catch (err) {
      this.showToast(err.message, 'error');
    }
  }

  async editItemPrice(itemId) {
    try {
      const item = (this.dbExplorerItems || []).find(it => it.id === itemId);
      const curPrice = item ? item.price : 0;
      const input = prompt(
        `Update / mark down price for item #${itemId}:\nEnter price in GBP (£):`,
        curPrice ? curPrice.toFixed(2) : ''
      );
      if (input === null) return;
      const parsed = parseFloat(input.trim());
      if (isNaN(parsed) || parsed <= 0) {
        this.showToast('Please enter a valid price greater than 0', 'warning');
        return;
      }
      const res = await fetch(`/api/database/items/${itemId}/update-price?price=${parsed.toFixed(2)}`, {
        method: 'POST'
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to update price');
      }
      const data = await res.json();
      this.showToast(data.message, 'success');
      await this.loadExplorerItems();
      await this.loadDatabaseSummaryBadges();
    } catch (err) {
      this.showToast(err.message, 'error');
    }
  }

  async verifyItemStatus(itemId) {
    const btn = document.getElementById(`btn-verify-status-${itemId}`);
    if (btn) btn.innerHTML = '<i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin text-cyan-400"></i>';
    if (window.lucide) lucide.createIcons();

    try {
      const res = await fetch(`/api/database/items/${itemId}/verify-status`, { method: 'POST' });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to verify item status');
      }
      const data = await res.json();
      this.showToast(data.message || 'Status verified', data.is_sold ? 'info' : 'success');
      await this.loadExplorerItems();
      await this.loadDatabaseSummaryBadges();
      fetch('/api/database/analytics').then(r => r.json()).then(d => {
        this.dbAnalyticsData = d;
        this.renderExplorerAnalytics(d);
      }).catch(() => {});
    } catch (err) {
      this.showToast(err.message, 'error');
    } finally {
      if (btn) {
        btn.innerHTML = '<i data-lucide="check-check" class="w-3.5 h-3.5"></i>';
        if (window.lucide) lucide.createIcons();
      }
    }
  }

  async auditDatabaseSoldStatuses() {
    const icon = document.getElementById('db-verify-statuses-icon');
    const textEl = document.getElementById('db-verify-statuses-text');
    if (icon) icon.classList.add('animate-spin');
    if (textEl) textEl.textContent = 'Syncing All Statuses & Prices…';

    try {
      const searchFilter = this.dbExplorerFilters && this.dbExplorerFilters.search_id ? `&search_id=${encodeURIComponent(this.dbExplorerFilters.search_id)}` : '';
      this.showToast('Auditing live sold/active statuses and exact prices across Vinted UK…', 'info');
      const res = await fetch(`/api/database/verify-sold-statuses?batch_size=0&only_active=false${searchFilter}`, { method: 'POST' });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to verify sold statuses');
      }
      const data = await res.json();
      this.showToast(data.message || 'Live status & price audit complete', 'success');
      await this.loadDatabaseExplorerData();
      if (this.currentSearchId) {
        this.loadSummary();
        this.loadRecentSales();
      }
    } catch (err) {
      this.showToast(err.message, 'error');
    } finally {
      if (icon) icon.classList.remove('animate-spin');
      if (textEl) textEl.textContent = 'Verify Live Status';
      if (window.lucide) lucide.createIcons();
    }
  }

  async toggleItemReplica(itemId) {
    try {
      const res = await fetch(`/api/database/items/${itemId}/toggle-replica`, {
        method: 'POST'
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to toggle replica verification');
      }

      const result = await res.json();
      this.showToast(result.message || 'Item replica status updated', 'success');

      await this.loadExplorerItems();
      await this.loadDatabaseSummaryBadges();
      // Refresh analytics in background
      fetch('/api/database/analytics').then(r => r.json()).then(d => {
        this.dbAnalyticsData = d;
        this.renderExplorerAnalytics(d);
      }).catch(() => {});
    } catch (err) {
      this.showToast(err.message, 'error');
    }
  }

  // ── Anti-Ban Shield & Cloudflare Management ──────────────────────────────

  openAntiBanModal() {
    const modal = document.getElementById('antiban-modal');
    if (modal) {
      modal.classList.remove('hidden');
      this.loadAntiBanStatus(true);
      if (window.lucide) lucide.createIcons();
    }
  }

  closeAntiBanModal() {
    const modal = document.getElementById('antiban-modal');
    if (modal) modal.classList.add('hidden');
  }

  async loadAntiBanStatus(populateInputs = false) {
    try {
      const res = await fetch('/api/antiban/status');
      if (!res.ok) throw new Error('Failed to load anti-ban status');
      const data = await res.json();
      this.applyAntiBanStatusUI(data, populateInputs);
      return data;
    } catch (err) {
      console.error('Failed to load anti-ban status:', err);
    }
  }

  applyAntiBanStatusUI(status, populateInputs = false) {
    if (!status) return;

    // Header badge
    const headerBadge = document.getElementById('header-antiban-badge');
    if (headerBadge) {
      if (status.circuit_breaker_active) {
        headerBadge.className = 'ml-1.5 px-1.5 py-0.5 rounded text-[10px] font-bold bg-rose-500/20 text-rose-300 border border-rose-500/30 pulse-live';
        headerBadge.textContent = `PAUSED (${status.cooldown_remaining_seconds}s)`;
      } else if (status.is_proxied) {
        headerBadge.className = 'ml-1.5 px-1.5 py-0.5 rounded text-[10px] font-bold bg-indigo-500/20 text-indigo-300 border border-indigo-500/30';
        headerBadge.textContent = 'PROXIED';
      } else {
        headerBadge.className = 'ml-1.5 px-1.5 py-0.5 rounded text-[10px] font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-500/30';
        headerBadge.textContent = 'TLS OK';
      }
    }

    // Sidebar pill
    const sidebarPill = document.getElementById('sidebar-antiban-pill');
    if (sidebarPill) {
      if (status.circuit_breaker_active) {
        sidebarPill.className = 'text-[10px] px-1.5 py-0.5 rounded font-mono font-semibold bg-rose-500/20 text-rose-300 border border-rose-500/30';
        sidebarPill.textContent = `${status.cooldown_remaining_seconds}s`;
      } else if (status.is_proxied) {
        sidebarPill.className = 'text-[10px] px-1.5 py-0.5 rounded font-mono font-semibold bg-indigo-500/20 text-indigo-300 border border-indigo-500/30';
        sidebarPill.textContent = 'PROXY';
      } else {
        sidebarPill.className = 'text-[10px] px-1.5 py-0.5 rounded font-mono font-semibold bg-slate-800 text-cyan-400 border border-slate-700';
        sidebarPill.textContent = 'SHIELD';
      }
    }

    // Sniper Shield Badge
    const sniperBadge = document.getElementById('sniper-antiban-badge');
    const sniperText = document.getElementById('sniper-antiban-text');
    const sniperIcon = document.getElementById('sniper-shield-icon');
    if (sniperBadge && sniperText) {
      if (status.circuit_breaker_active) {
        sniperBadge.className = 'inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-rose-500/20 text-rose-300 border border-rose-500/40 cursor-pointer hover:bg-rose-500/30 transition animate-pulse';
        sniperText.textContent = `Cooldown (${status.cooldown_remaining_seconds}s)`;
        if (sniperIcon) sniperIcon.setAttribute('data-lucide', 'alert-triangle');
      } else if (status.is_proxied) {
        sniperBadge.className = 'inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-indigo-500/20 text-indigo-300 border border-indigo-500/40 cursor-pointer hover:bg-indigo-500/30 transition';
        sniperText.textContent = `Proxied (${status.total_proxies_count})`;
        if (sniperIcon) sniperIcon.setAttribute('data-lucide', 'shield-check');
      } else {
        sniperBadge.className = 'inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-cyan-500/10 text-cyan-300 border border-cyan-500/30 cursor-pointer hover:bg-cyan-500/20 transition';
        sniperText.textContent = 'Chrome TLS';
        if (sniperIcon) sniperIcon.setAttribute('data-lucide', 'shield-check');
      }
    }

    // Modal Cooldown Banner
    const cooldownBanner = document.getElementById('antiban-cooldown-banner');
    const cooldownMsg = document.getElementById('antiban-cooldown-msg');
    if (cooldownBanner && cooldownMsg) {
      if (status.circuit_breaker_active) {
        cooldownBanner.classList.remove('hidden');
        cooldownMsg.textContent = `${status.cooldown_reason || 'Rate limit / challenge encountered.'} (${status.cooldown_remaining_seconds}s remaining)`;
      } else {
        cooldownBanner.classList.add('hidden');
      }
    }

    // Modal Metric Cards
    const statProxies = document.getElementById('antiban-stat-proxies');
    const statProxiesSub = document.getElementById('antiban-stat-proxies-sub');
    if (statProxies) {
      statProxies.textContent = status.is_proxied ? status.active_proxy : 'Direct IP';
      statProxies.title = status.active_proxy || 'Direct IP';
    }
    if (statProxiesSub) {
      statProxiesSub.textContent = `${status.total_proxies_count} ${status.total_proxies_count === 1 ? 'proxy' : 'proxies'} loaded`;
    }

    const statCookies = document.getElementById('antiban-stat-cookies');
    const statCookiesSub = document.getElementById('antiban-stat-cookies-sub');
    if (statCookies) {
      statCookies.textContent = status.has_custom_cookies ? 'Cleared Token' : 'Direct Guest';
    }
    if (statCookiesSub) {
      statCookiesSub.textContent = status.has_cf_clearance
        ? 'cf_clearance active'
        : (status.has_custom_cookies ? 'Session token injected' : 'Standard web session');
    }

    // Input fields population when requested (e.g. on modal open)
    if (populateInputs) {
      const proxiesInput = document.getElementById('antiban-proxies-input');
      if (proxiesInput && status.raw_proxies !== undefined) {
        proxiesInput.value = status.raw_proxies;
      }
      const cookiesInput = document.getElementById('antiban-cookies-input');
      if (cookiesInput && status.raw_cookies !== undefined) {
        cookiesInput.value = status.raw_cookies;
      }
    }

    if (window.lucide) lucide.createIcons();
  }

  async saveAntiBanConfig() {
    const saveBtn = document.getElementById('btn-save-antiban');
    const proxiesInput = document.getElementById('antiban-proxies-input');
    const cookiesInput = document.getElementById('antiban-cookies-input');

    const proxies = proxiesInput ? proxiesInput.value.trim() : '';
    const cookies = cookiesInput ? cookiesInput.value.trim() : '';

    if (saveBtn) saveBtn.disabled = true;

    try {
      const res = await fetch('/api/antiban/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ proxies, cookies })
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to update anti-ban config');
      }

      const result = await res.json();
      this.showToast('Anti-Ban settings updated and applied!', 'success');
      this.applyAntiBanStatusUI(result.status, true);
      this.closeAntiBanModal();
    } catch (err) {
      this.showToast(err.message, 'error');
    } finally {
      if (saveBtn) saveBtn.disabled = false;
    }
  }

  async testAntiBanConnection() {
    const testBtn = document.getElementById('btn-test-antiban');
    const testIcon = document.getElementById('antiban-test-icon');
    const statusEl = document.getElementById('antiban-test-status');
    const detailEl = document.getElementById('antiban-test-detail');

    if (testBtn) testBtn.disabled = true;
    if (testIcon) testIcon.classList.add('animate-spin');
    if (statusEl) {
      statusEl.className = 'font-mono font-semibold text-cyan-400';
      statusEl.textContent = 'Probing vinted.co.uk...';
    }

    try {
      const res = await fetch('/api/antiban/test', { method: 'POST' });
      const result = await res.json();

      if (result.ok) {
        if (statusEl) {
          statusEl.className = 'font-mono font-semibold text-emerald-400 flex items-center';
          statusEl.innerHTML = `<span class="w-2 h-2 rounded-full bg-emerald-400 mr-1.5"></span> HTTP ${result.status_code} OK (${result.latency_ms}ms)`;
        }
        if (detailEl) {
          detailEl.className = 'text-[11px] text-emerald-300/90';
          detailEl.textContent = `Connection healthy via ${result.proxy_used}. Vinted is reachable without Cloudflare challenges.`;
        }
        this.showToast('Vinted connection probe succeeded! Scraping path is clear.', 'success');
      } else {
        if (statusEl) {
          statusEl.className = 'font-mono font-semibold text-rose-400 flex items-center';
          statusEl.innerHTML = `<span class="w-2 h-2 rounded-full bg-rose-400 mr-1.5"></span> HTTP ${result.status_code || 'ERR'} (${result.latency_ms}ms)`;
        }
        if (detailEl) {
          detailEl.className = 'text-[11px] text-rose-300';
          detailEl.innerHTML = `<strong>${result.message}</strong><br><span class="text-slate-400 mt-1 block">${result.resolution}</span>`;
        }
        this.showToast(result.message, 'warning', result.resolution);
      }

      await this.loadAntiBanStatus(false);
    } catch (err) {
      if (statusEl) {
        statusEl.className = 'font-mono font-semibold text-rose-400';
        statusEl.textContent = 'Probe Failed';
      }
      if (detailEl) {
        detailEl.className = 'text-[11px] text-rose-300';
        detailEl.textContent = err.message;
      }
      this.showToast('Connection probe failed: ' + err.message, 'error');
    } finally {
      if (testBtn) testBtn.disabled = false;
      if (testIcon) testIcon.classList.remove('animate-spin');
      if (window.lucide) lucide.createIcons();
    }
  }

  async resetAntiBanCooldown() {
    try {
      const res = await fetch('/api/antiban/reset-cooldown', { method: 'POST' });
      if (!res.ok) throw new Error('Failed to reset cooldown');
      const result = await res.json();
      this.showToast('Circuit breaker cooldown cleared. Resuming immediate requests.', 'success');
      this.applyAntiBanStatusUI(result.status);
    } catch (err) {
      this.showToast(err.message, 'error');
    }
  }

  async autoHarvestSession() {
    const harvestBtn = document.getElementById('btn-auto-harvest');
    const harvestIcon = document.getElementById('auto-harvest-icon');
    const harvestText = document.getElementById('auto-harvest-text');

    if (harvestBtn) harvestBtn.disabled = true;
    if (harvestIcon) harvestIcon.classList.add('animate-spin');
    if (harvestText) harvestText.textContent = 'Solving Cloudflare...';

    this.showToast('Launching background Chromium engine to solve Cloudflare challenge & harvest tokens...', 'info');

    try {
      const res = await fetch('/api/antiban/harvest', { method: 'POST' });
      const result = await res.json();

      if (result.ok) {
        this.showToast(`✨ Success! Harvested ${result.cookies_count} fresh session cookies from Vinted.`, 'success');
        this.applyAntiBanStatusUI(result.status, true);

        // Update diagnostic probe status
        const statusEl = document.getElementById('antiban-test-status');
        const detailEl = document.getElementById('antiban-test-detail');
        if (statusEl && result.probe) {
          statusEl.className = 'font-mono font-semibold text-emerald-400 flex items-center';
          statusEl.innerHTML = `<span class="w-2 h-2 rounded-full bg-emerald-400 mr-1.5"></span> HTTP ${result.probe.status_code} OK (${result.probe.latency_ms}ms)`;
        }
        if (detailEl && result.probe) {
          detailEl.className = 'text-[11px] text-emerald-300/90';
          detailEl.textContent = 'Automated session active. Connection verified without Cloudflare challenges.';
        }
      } else {
        this.showToast(result.message || 'Auto-harvest failed to complete challenge.', 'warning');
      }

      await this.loadAntiBanStatus(true);
    } catch (err) {
      this.showToast('Automated session harvest failed: ' + err.message, 'error');
    } finally {
      if (harvestBtn) harvestBtn.disabled = false;
      if (harvestIcon) harvestIcon.classList.remove('animate-spin');
      if (harvestText) harvestText.textContent = 'Auto-Harvest Now';
      if (window.lucide) lucide.createIcons();
    }
  }

  // ── Scraper Live Activity Terminal ──────────────────────────────────────────

  toggleScraperTerminal() {
    if (this.terminalState.isOpen) {
      this.closeScraperTerminal();
    } else {
      this.openScraperTerminal();
    }
  }

  openScraperTerminal() {
    this.terminalState.isOpen = true;
    const drawer = document.getElementById('scraper-terminal-drawer');
    if (drawer) {
      drawer.classList.remove('hidden');
    }
    const navBtn = document.getElementById('nav-scraper-terminal-btn');
    if (navBtn) navBtn.classList.add('bg-slate-800', 'text-white');

    // Instantly poll to refresh latest logs
    this.pollTerminalFeed(true);

    if (this.terminalState.autoScroll) {
      this.scrollTerminalToBottom();
    }
    if (window.lucide) lucide.createIcons();
  }

  closeScraperTerminal() {
    this.terminalState.isOpen = false;
    const drawer = document.getElementById('scraper-terminal-drawer');
    if (drawer) {
      drawer.classList.add('hidden');
    }
    const navBtn = document.getElementById('nav-scraper-terminal-btn');
    if (navBtn) navBtn.classList.remove('bg-slate-800', 'text-white');
  }

  toggleTerminalMaximize() {
    this.terminalState.isMaximized = !this.terminalState.isMaximized;
    const container = document.getElementById('scraper-terminal-container');
    const backdrop = document.getElementById('scraper-terminal-backdrop');
    const maxIcon = document.getElementById('terminal-max-icon');

    if (!container) return;

    if (this.terminalState.isMaximized) {
      // Maximized modal layout
      container.classList.remove('h-[380px]', 'md:ml-72');
      container.classList.add('fixed', 'inset-4', 'md:inset-10', 'rounded-2xl', 'border', 'border-slate-700', 'z-50', 'max-w-6xl', 'mx-auto', 'shadow-2xl');
      if (backdrop) backdrop.classList.remove('hidden');
      if (maxIcon) maxIcon.setAttribute('data-lucide', 'minimize-2');
    } else {
      // Docked bottom layout
      container.classList.remove('fixed', 'inset-4', 'md:inset-10', 'rounded-2xl', 'max-w-6xl', 'mx-auto', 'z-50');
      container.classList.add('h-[380px]', 'md:ml-72');
      if (backdrop) backdrop.classList.add('hidden');
      if (maxIcon) maxIcon.setAttribute('data-lucide', 'maximize-2');
    }
    if (window.lucide) lucide.createIcons();
    if (this.terminalState.autoScroll) {
      this.scrollTerminalToBottom();
    }
  }

  toggleTerminalAutoScroll() {
    this.terminalState.autoScroll = !this.terminalState.autoScroll;
    const btn = document.getElementById('btn-terminal-autoscroll');
    if (btn) {
      if (this.terminalState.autoScroll) {
        btn.textContent = 'Auto-Scroll: ON';
        btn.className = 'px-2 py-1 rounded text-[11px] font-mono font-semibold text-emerald-400 bg-slate-800 hover:bg-slate-700 border border-slate-700 transition cursor-pointer';
        this.scrollTerminalToBottom();
      } else {
        btn.textContent = 'Auto-Scroll: OFF';
        btn.className = 'px-2 py-1 rounded text-[11px] font-mono font-semibold text-slate-400 bg-slate-900 hover:bg-slate-800 border border-slate-800 transition cursor-pointer';
      }
    }
  }

  setTerminalFilter(filterType) {
    this.terminalState.activeFilter = filterType;
    ['all', 'db_added', 'scans'].forEach(t => {
      const btn = document.getElementById(`terminal-filter-${t}`);
      if (!btn) return;
      if (t === filterType) {
        btn.className = 'px-2.5 py-1 rounded-md text-[11px] font-semibold text-white bg-slate-800 shadow-sm transition cursor-pointer flex items-center';
      } else {
        btn.className = 'px-2.5 py-1 rounded-md text-[11px] font-semibold text-slate-400 hover:text-slate-200 transition cursor-pointer flex items-center';
      }
    });
    this.renderAllTerminalEvents();
  }

  async clearTerminalFeed() {
    try {
      await fetch('/api/scraper/clear-feed', { method: 'POST' });
      this.terminalState.events = [];
      this.terminalState.lastEventId = 0;
      this.terminalState.totalAddedSession = 0;
      this.terminalState.totalInspectedSession = 0;
      this.terminalState.totalPriceDropsSession = 0;
      const output = document.getElementById('scraper-terminal-output');
      if (output) {
        output.innerHTML = `
          <div id="terminal-empty-placeholder" class="py-12 text-center text-slate-500 space-y-2">
            <i data-lucide="terminal" class="w-8 h-8 mx-auto text-slate-700 mb-2"></i>
            <p class="font-semibold text-slate-400 text-xs">Live Scraper Terminal Ready</p>
            <p class="text-[11px] text-slate-600 max-w-sm mx-auto">
              Terminal buffer cleared. Newly scraped items added to the database will appear highlighted here in real-time.
            </p>
          </div>
        `;
        if (window.lucide) lucide.createIcons();
      }
      this.updateTerminalBadges({ total_added_to_db: 0, total_inspected: 0, total_price_updates: 0 });
    } catch (err) {
      console.error('Error clearing terminal feed:', err);
    }
  }

  startTerminalPolling() {
    if (this.terminalState.pollTimer) clearInterval(this.terminalState.pollTimer);
    this.terminalState.pollTimer = setInterval(() => {
      this.pollTerminalFeed();
    }, 2500);
    this.pollTerminalFeed();
  }

  async pollTerminalFeed(forceRender = false) {
    try {
      const since = forceRender ? 0 : this.terminalState.lastEventId;
      const url = `/api/scraper/live-feed?since_id=${since}&limit=150`;
      const res = await fetch(url);
      if (!res.ok) return;
      const data = await res.json();

      const newEvents = data.events || [];
      const stats = data.stats || {};
      const scheduler = data.scheduler || {};

      this.updateTerminalBadges(stats, scheduler);

      if (newEvents.length > 0) {
        if (forceRender) {
          this.terminalState.events = newEvents;
        } else {
          const existingIds = new Set(this.terminalState.events.map(e => e.id));
          for (const ev of newEvents) {
            if (!existingIds.has(ev.id)) {
              this.terminalState.events.push(ev);
            }
          }
        }
        if (data.latest_event_id) {
          this.terminalState.lastEventId = data.latest_event_id;
        }

        this.renderAllTerminalEvents();

        const hasDbAdded = newEvents.some(e => e.type === 'db_added');
        if (hasDbAdded) {
          this.loadDatabaseSummaryBadges();
          if (this.isDbExplorerActive) {
            this.loadDatabaseExplorerData();
          }
        }
      } else if (forceRender) {
        this.renderAllTerminalEvents();
      }
    } catch (err) {
      // Quiet background poller
    }
  }

  updateTerminalBadges(stats, scheduler) {
    const totalAdded = stats?.total_added_to_db ?? 0;
    const totalInspected = stats?.total_inspected ?? 0;
    const totalPriceDrops = stats?.total_price_updates ?? 0;

    // Header badge
    const headerBadge = document.getElementById('header-terminal-db-badge');
    if (headerBadge) {
      headerBadge.textContent = totalAdded;
      if (totalAdded > 0) {
        headerBadge.className = 'ml-1.5 px-1.5 py-0.2 rounded text-[10px] font-mono font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 animate-pulse';
      }
    }

    // Sidebar badge
    const sidebarBadge = document.getElementById('sidebar-terminal-db-badge');
    if (sidebarBadge) {
      sidebarBadge.textContent = `${totalAdded} Added`;
      if (totalAdded > 0) {
        sidebarBadge.className = 'px-2 py-0.5 text-[11px] font-mono font-bold rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/40';
      }
    }

    // Terminal internal badges
    const statAdded = document.getElementById('terminal-stat-added-badge');
    if (statAdded) statAdded.textContent = `${totalAdded} Added to DB`;

    const statInspected = document.getElementById('terminal-stat-inspected-badge');
    if (statInspected) statInspected.textContent = `${totalInspected} Checked`;

    const statPrice = document.getElementById('terminal-stat-price-badge');
    if (statPrice) statPrice.textContent = `${totalPriceDrops} Drops`;

    // Terminal live status pill
    const statusPill = document.getElementById('terminal-live-status-pill');
    if (statusPill && scheduler) {
      if (scheduler.is_cycle_running && scheduler.current_search_text) {
        statusPill.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-emerald-500/10 text-emerald-300 border border-emerald-500/30 truncate max-w-[240px] flex items-center';
        statusPill.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-emerald-400 mr-1.5 pulse-live"></span> ${scheduler.current_search_text}`;
      } else if (scheduler.constant_scrape_active) {
        statusPill.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-amber-500/10 text-amber-300 border border-amber-500/30 truncate max-w-[240px]';
        statusPill.textContent = '24/7 Loop Standby';
      } else {
        statusPill.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-slate-800 text-teal-300 border border-slate-700 truncate max-w-[240px]';
        statusPill.textContent = 'Standby (Auto-Poll)';
      }
    }
  }

  renderAllTerminalEvents() {
    const output = document.getElementById('scraper-terminal-output');
    if (!output) return;

    let eventsToRender = this.terminalState.events || [];
    const filter = this.terminalState.activeFilter;

    if (filter === 'db_added') {
      eventsToRender = eventsToRender.filter(e => e.type === 'db_added');
    } else if (filter === 'scans') {
      eventsToRender = eventsToRender.filter(e => ['scan_start', 'scan_summary', 'cycle_complete', 'status_audit'].includes(e.type));
    }

    if (eventsToRender.length === 0) {
      if (filter === 'db_added') {
        output.innerHTML = `
          <div class="py-12 text-center text-slate-500 space-y-2">
            <i data-lucide="database" class="w-8 h-8 mx-auto text-slate-700 mb-2"></i>
            <p class="font-semibold text-slate-400 text-xs">No Items Added To DB Yet</p>
            <p class="text-[11px] text-slate-600 max-w-sm mx-auto">
              Whenever the scraper finds and inserts a new listing into the database, it will appear highlighted right here.
            </p>
          </div>
        `;
      } else {
        output.innerHTML = `
          <div id="terminal-empty-placeholder" class="py-12 text-center text-slate-500 space-y-2">
            <i data-lucide="terminal" class="w-8 h-8 mx-auto text-slate-700 mb-2"></i>
            <p class="font-semibold text-slate-400 text-xs">Live Scraper Terminal Ready</p>
            <p class="text-[11px] text-slate-600 max-w-sm mx-auto">
              Listening for scrape events from Auto-Poll, Constant Scrape, and Deal Sniper. Newly scraped items added to the database will appear highlighted here in real-time.
            </p>
          </div>
        `;
      }
      if (window.lucide) lucide.createIcons();
      return;
    }

    const html = eventsToRender.map(ev => this.renderTerminalEventHTML(ev)).join('');
    output.innerHTML = html;

    if (window.lucide) lucide.createIcons();

    if (this.terminalState.autoScroll) {
      this.scrollTerminalToBottom();
    }
  }

  renderTerminalEventHTML(ev) {
    if (ev.type === 'db_added') {
      // PROMINENTLY HIGHLIGHTED CARD
      const priceFormatted = Number(ev.price || 0).toFixed(2);
      const discountPill = ev.discount_pct && ev.discount_pct > 0
        ? `<span class="text-[10px] font-bold text-emerald-300 bg-emerald-500/20 px-1.5 py-0.5 rounded border border-emerald-500/30">-${ev.discount_pct}%</span>`
        : '';
      const photoHtml = ev.photo_url
        ? `<img src="${ev.photo_url}" class="w-12 h-12 object-cover rounded-lg border border-emerald-500/40 shrink-0 bg-slate-900" alt="Item photo" loading="lazy">`
        : `<div class="w-12 h-12 rounded-lg border border-emerald-500/30 bg-slate-900 flex items-center justify-center text-emerald-400 shrink-0"><i data-lucide="tag" class="w-5 h-5"></i></div>`;

      return `
        <div class="terminal-item-card p-3 rounded-xl border border-emerald-500/50 bg-gradient-to-r from-emerald-950/30 via-slate-900/90 to-slate-900/80 shadow-md shadow-emerald-950/20 hover:border-emerald-400 transition space-y-2">
          <div class="flex items-center justify-between text-[11px]">
            <div class="flex items-center space-x-2">
              <span class="px-2 py-0.5 rounded font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 flex items-center">
                <i data-lucide="database" class="w-3 h-3 mr-1 text-emerald-400"></i>
                ADDED TO DB
              </span>
              <span class="px-1.5 py-0.5 rounded font-semibold bg-slate-800 text-teal-300 border border-slate-700">
                ${ev.search_text || 'General Scrape'}
              </span>
              <span class="text-slate-500 font-mono">[${ev.time || ''}]</span>
            </div>
            <div class="flex items-center space-x-1.5 font-mono">
              ${discountPill}
              <span class="text-sm font-bold text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/30">
                £${priceFormatted}
              </span>
            </div>
          </div>

          <div class="flex items-center space-x-3">
            ${photoHtml}
            <div class="flex-1 min-w-0">
              <a href="${ev.url || '#'}" target="_blank" rel="noopener noreferrer" class="text-sm font-semibold text-white hover:text-emerald-300 truncate block transition flex items-center">
                <span class="truncate">${ev.title || 'Untitled Listing'}</span>
                <i data-lucide="external-link" class="w-3.5 h-3.5 ml-1.5 text-slate-400 shrink-0"></i>
              </a>
              <div class="flex items-center space-x-2 text-[11px] text-slate-400 mt-1 font-sans">
                ${ev.brand ? `<span class="text-slate-300 font-medium">${ev.brand}</span>` : ''}
                ${ev.brand && ev.size ? `<span>•</span>` : ''}
                ${ev.size ? `<span class="bg-slate-800 px-1.5 py-0.2 rounded text-[10px] text-slate-300">Size: ${ev.size}</span>` : ''}
                ${ev.source ? `<span class="text-[10px] text-slate-500">via ${ev.source}</span>` : ''}
              </div>
            </div>
            <a href="${ev.url || '#'}" target="_blank" rel="noopener noreferrer" class="px-3 py-1.5 rounded-lg text-xs font-semibold bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300 border border-emerald-500/30 transition shrink-0 flex items-center">
              <span>View</span>
              <i data-lucide="arrow-up-right" class="w-3.5 h-3.5 ml-1"></i>
            </a>
          </div>
        </div>
      `;
    }

    if (ev.type === 'price_updated') {
      const oldP = Number(ev.old_price || 0).toFixed(2);
      const newP = Number(ev.new_price || 0).toFixed(2);
      return `
        <div class="p-2.5 rounded-lg bg-amber-500/10 border-l-2 border-amber-400 text-xs font-mono flex items-center justify-between text-amber-200">
          <div class="flex items-center space-x-2 truncate">
            <span class="text-slate-500">[${ev.time || ''}]</span>
            <span class="px-1.5 py-0.2 rounded text-[10px] bg-amber-500/20 text-amber-300 border border-amber-500/30 font-bold">PRICE SYNC</span>
            <span class="text-slate-400">[${ev.search_text || ''}]</span>
            <span class="text-white font-medium truncate">${ev.title || ''}</span>
          </div>
          <div class="font-bold text-amber-300 shrink-0 ml-2">
            £${oldP} ➔ £${newP}
          </div>
        </div>
      `;
    }

    if (ev.type === 'scan_start') {
      return `
        <div class="py-1 px-2 text-[11px] font-mono text-slate-400 flex items-center space-x-2">
          <span class="text-slate-600">[${ev.time || ''}]</span>
          <span class="text-teal-400">🔍 Scanning '${ev.search_text || ''}' on Vinted UK...</span>
        </div>
      `;
    }

    if (ev.type === 'scan_summary') {
      const newBadge = ev.new_count > 0
        ? `<span class="text-emerald-400 font-bold">+${ev.new_count} to DB</span>`
        : `<span class="text-slate-500">0 new</span>`;
      return `
        <div class="py-1.5 px-2.5 rounded bg-slate-900/60 text-[11px] font-mono text-slate-300 flex items-center justify-between">
          <div class="flex items-center space-x-2">
            <span class="text-slate-600">[${ev.time || ''}]</span>
            <span class="text-indigo-400 font-semibold">[${ev.search_text || ''}]</span>
            <span>Found ${ev.total_found || 0} listings</span>
          </div>
          <div class="flex items-center space-x-2">
            ${newBadge}
            <span class="text-slate-500">(${(ev.spam_blocked || 0) + (ev.rep_blocked || 0)} filtered)</span>
          </div>
        </div>
      `;
    }

    if (ev.type === 'cycle_complete') {
      return `
        <div class="p-2 rounded-lg bg-slate-900 border border-slate-800 text-[11px] font-mono text-teal-300 flex items-center justify-between">
          <div class="flex items-center space-x-2">
            <span class="text-slate-500">[${ev.time || ''}]</span>
            <span class="font-bold">🏁 Scrape Cycle Finished</span>
            <span class="text-slate-400">• ${ev.total_searches || 0} searches</span>
          </div>
          <span class="text-emerald-400 font-bold">+${ev.total_new || 0} added to DB</span>
        </div>
      `;
    }

    if (ev.type === 'status_audit') {
      return `
        <div class="py-1 px-2 text-[11px] font-mono text-cyan-400 flex items-center space-x-2">
          <span class="text-slate-600">[${ev.time || ''}]</span>
          <span>🛡️ Status audit: ${ev.sold_count || 0} confirmed sold, ${ev.active_count || 0} active verified, ${ev.price_updates || 0} prices synced</span>
        </div>
      `;
    }

    // Generic fallback event
    return `
      <div class="py-1 px-2 text-[11px] font-mono text-slate-400 flex items-center space-x-2">
        <span class="text-slate-600">[${ev.time || ''}]</span>
        <span>${ev.message || ''}</span>
      </div>
    `;
  }

  scrollTerminalToBottom() {
    const output = document.getElementById('scraper-terminal-output');
    if (output) {
      output.scrollTop = output.scrollHeight;
    }
  }

  // ── Power Trader Keyboard Navigation & Unified Modes ──────────────────────

  navigateDealCards(direction) {
    const cards = Array.from(document.querySelectorAll('#sniper-deals-grid .deal-card'));
    if (cards.length === 0) return;

    if (this.focusedDealIndex === -1) {
      this.focusedDealIndex = direction > 0 ? 0 : cards.length - 1;
    } else {
      this.focusedDealIndex = Math.max(0, Math.min(cards.length - 1, this.focusedDealIndex + direction));
    }

    cards.forEach((c, idx) => {
      if (idx === this.focusedDealIndex) {
        c.classList.add('deal-card-focused');
        c.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      } else {
        c.classList.remove('deal-card-focused');
      }
    });
  }

  setFocusedDealById(id) {
    const cards = Array.from(document.querySelectorAll('#sniper-deals-grid .deal-card'));
    cards.forEach((c, idx) => {
      if (c.dataset.dealId === String(id)) {
        this.focusedDealIndex = idx;
        c.classList.add('deal-card-focused');
      } else {
        c.classList.remove('deal-card-focused');
      }
    });
  }

  snipeFocusedDeal() {
    const cards = Array.from(document.querySelectorAll('#sniper-deals-grid .deal-card'));
    if (cards.length === 0) {
      this.showToast('No deal cards in stream to snipe', 'info');
      return;
    }
    const targetIdx = this.focusedDealIndex >= 0 ? this.focusedDealIndex : 0;
    const card = cards[targetIdx];
    const link = card.querySelector('a[href]');
    if (link && link.href) {
      window.open(link.href, '_blank');
      this.showToast('Opening sniped deal on Vinted UK...', 'success');
    }
  }

  showKeyboardShortcutsModal() {
    const modal = document.getElementById('shortcuts-modal');
    if (modal) modal.classList.remove('hidden');
    if (window.lucide) lucide.createIcons();
  }

  closeShortcutsModal() {
    const modal = document.getElementById('shortcuts-modal');
    if (modal) modal.classList.add('hidden');
  }

  closeAllModals() {
    this.closeAddModal();
    this.closeEditModal();
    this.closeScraperModal();
    this.closeDatabaseModal();
    this.closeAntiBanModal();
    this.closeShortcutsModal();
    this.closeScraperTerminal();
    const mobDrawer = document.getElementById('mobile-items-drawer');
    if (mobDrawer) mobDrawer.classList.add('hidden');
  }

  toggleMobileItemsDrawer() {
    const drawer = document.getElementById('mobile-items-drawer');
    if (!drawer) return;
    const isHidden = drawer.classList.contains('hidden');
    if (isHidden) {
      const desktopList = document.getElementById('tracked-searches-list');
      const mobileList = document.getElementById('mobile-tracked-searches-list');
      if (desktopList && mobileList) {
        mobileList.innerHTML = desktopList.innerHTML;
        mobileList.querySelectorAll('button').forEach(btn => {
          btn.addEventListener('click', () => {
            drawer.classList.add('hidden');
          });
        });
      }
      drawer.classList.remove('hidden');
    } else {
      drawer.classList.add('hidden');
    }
    if (window.lucide) lucide.createIcons();
  }

  toggleCredentialMasking() {
    const proxyInput = document.getElementById('antiban-proxies-input');
    const cookieInput = document.getElementById('antiban-cookies-input');
    const textEl = document.getElementById('credential-mask-text');
    const iconEl = document.getElementById('credential-mask-icon');
    if (!proxyInput) return;

    const isMasked = proxyInput.classList.contains('secret-mask') && !proxyInput.classList.contains('unmasked');
    if (isMasked) {
      proxyInput.classList.add('unmasked');
      if (cookieInput) cookieInput.classList.add('unmasked');
      if (textEl) textEl.textContent = 'Hide Secrets';
      if (iconEl) iconEl.setAttribute('data-lucide', 'eye-off');
    } else {
      proxyInput.classList.remove('unmasked');
      if (cookieInput) cookieInput.classList.remove('unmasked');
      if (textEl) textEl.textContent = 'Show Secrets';
      if (iconEl) iconEl.setAttribute('data-lucide', 'eye');
    }
    if (window.lucide) lucide.createIcons();
  }

  async setScanningMode(mode) {
    if (mode === 'sniper') {
      this.showSniperView();
      if (!this.sniperActive) {
        await this.startSniper();
      }
    } else if (mode === 'poll') {
      if (this.sniperActive) {
        await this.stopSniper();
      }
      this.openScraperModal();
    } else if (mode === 'paused') {
      if (this.sniperActive) {
        await this.stopSniper();
      }
      this.showToast('Market Scanner Paused.', 'info');
    }
    if (window.lucide) lucide.createIcons();
  }
}

// Instantiate global app
window.app = new VintedApp();

