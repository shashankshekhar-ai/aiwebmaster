// Shared app shell — sidebar nav + user card, identical across every page.
// renderShell(activePath) returns the outer flex container's HTML; caller
// injects it into #app-root and puts page content inside #page-content.
// `requires`: permission-type string this nav item needs (checked against
// ROLE_PERMISSIONS below, same slugs as auth/permissions.py's
// ROLE_PERMISSIONS), or null to always show. Filtered post-login in
// mountShell() — a ui_editor then only ever sees Chat + Browse, the two
// things they can actually use, instead of five links that just 403.
const NAV_ITEMS = [
  { path: '/browse', label: 'Browse & edit', icon: 'M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v7a2 2 0 01-2 2H5a2 2 0 01-2-2V7z', requires: null },
  { path: '/git', label: 'Git', icon: 'M6 3v12m0 0a3 3 0 103 3m-3-3a3 3 0 00-3 3m3-3a3 3 0 013-3m3-9a3 3 0 11-6 0 3 3 0 016 0zm6 0a3 3 0 11-6 0 3 3 0 016 0zm-3 3v6a3 3 0 01-3 3', requires: 'git' },
  { path: '/deploy', label: 'Deploy', icon: 'M5 13l4 4L19 7', requires: 'docker' },
  { path: '/agent', label: 'Agent Terminal', icon: 'M8 9l3 3-3 3m5 0h4M5 4h14a1 1 0 011 1v14a1 1 0 01-1 1H5a1 1 0 01-1-1V5a1 1 0 011-1z', requires: 'codegen_agent' },
  { path: '/users', label: 'Users', icon: 'M12 4.5a4 4 0 100 8 4 4 0 000-8zM4 20a8 8 0 0116 0', requires: 'user_management' },
  { path: '/system', label: 'System', icon: 'M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 7h10v10H7V7z', requires: 'docker' },
  { path: '/settings', label: 'Settings', icon: 'M10.325 4.317a1 1 0 011.35-.936l.379.15a1 1 0 001.298-.53l.198-.38a1 1 0 011.766.93l-.15.38a1 1 0 00.53 1.298l.38.198a1 1 0 01-.93 1.766l-.38-.15a1 1 0 00-1.298.53l-.198.38a1 1 0 01-1.766-.93l.15-.38a1 1 0 00-.53-1.298l-.38-.198a1 1 0 01-.319-.15zM12 15a3 3 0 100-6 3 3 0 000 6z', requires: 'user_management' },
];

function svgIcon(d) {
  return `<svg class="w-[18px] h-[18px] shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
    <path d="${d}"/>
  </svg>`;
}

function initials(email) {
  const name = (email || '?').split('@')[0];
  return name.slice(0, 2).toUpperCase();
}

function renderShell(activePath) {
  const agentExpanded = localStorage.getItem('aiwebmaster_agent_menu_expanded') !== '0';
  const navHtml = NAV_ITEMS.map((item, i) => {
    const active = item.path === activePath;
    const isAgent = item.path === '/agent';
    const link = `<a href="${item.path}" data-nav-item="${item.path}"
      class="group relative flex-1 flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-all duration-150
             ${active
               ? 'bg-brand-panel2 text-brand-ink border border-brand-border shadow-panel'
               : 'text-brand-muted hover:text-brand-ink hover:bg-brand-panel/70 border border-transparent'}">
      ${active ? '<span class="absolute left-0 top-1.5 bottom-1.5 w-0.5 rounded-full bg-brand-gold"></span>' : ''}
      <span class="${active ? 'text-brand-gold' : 'text-brand-muted group-hover:text-brand-gold'} transition-colors">${svgIcon(item.icon)}</span>
      <span class="flex-1">${item.label}</span>
      ${item.path === '/git' ? '<span id="nav-git-badge" class="hidden text-[10px] font-bold bg-brand-terracotta text-white rounded-full w-4 h-4 items-center justify-center"></span>' : ''}
    </a>`;
    const toggle = isAgent
      ? `<button id="agent-menu-toggle" title="Expand Agent Terminal sessions" class="p-1.5 text-brand-muted hover:text-brand-ink transition-transform shrink-0" style="transform: rotate(${agentExpanded ? '90' : '0'}deg)">
          ${svgIcon('M9 5l7 7-7 7').replace('w-[18px] h-[18px]', 'w-3.5 h-3.5')}
        </button>`
      : '';
    return `<div class="flex items-center gap-1 animate-fade-in-left" style="animation-delay:${i * 40}ms">${link}${toggle}</div>
    ${isAgent ? '<div id="agent-menu"></div>' : ''}`;
  }).join('');

  return `
  <aside class="relative z-10 w-64 shrink-0 bg-black/95 backdrop-blur border-r border-white/10 shadow-lift flex flex-col p-4 animate-fade-in-left">
    <div class="flex items-center gap-2.5 px-2 mb-1">
      <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-brand-gold to-amber-600 flex items-center justify-center text-brand-navy font-black text-sm shadow-glow">A</div>
      <div>
        <div class="font-bold text-[15px] tracking-tight leading-none">AI<span class="text-brand-gold">webmaster</span></div>
      </div>
    </div>
    <div class="text-[11px] text-brand-muted px-2 mb-6">The Bradbury Group</div>

    <div id="chat-menu" class="mb-1"></div>

    <nav class="flex flex-col gap-1">${navHtml}</nav>

    <div class="flex-1"></div>

    <div id="sidebar-who" class="border-t border-white/10 pt-4 px-1">
      <div class="flex items-center gap-2 animate-pulse">
        <div class="w-8 h-8 rounded-full bg-brand-panel"></div>
        <div class="h-3 w-24 bg-brand-panel rounded"></div>
      </div>
    </div>
  </aside>`;
}

async function mountShell(activePath) {
  const root = document.getElementById('app-root');
  const shellWrap = document.createElement('div');
  shellWrap.className = 'flex h-screen overflow-hidden bg-brand-navy text-brand-ink font-sans';
  shellWrap.innerHTML = renderShell(activePath);

  const main = document.createElement('div');
  main.id = 'shell-main';
  main.className = 'flex-1 flex flex-col min-w-0 overflow-hidden';
  const existingContent = document.getElementById('page-content');
  if (existingContent) main.appendChild(existingContent);
  shellWrap.appendChild(main);

  root.appendChild(shellWrap);

  const res = await fetch('/api/me');
  if (!res.ok) { window.location.href = '/login'; return null; }
  const me = await res.json();

  filterNav(me);
  await mountChatMenu(activePath);
  await mountAgentMenu(activePath);
  updateGitBadge();

  document.getElementById('sidebar-who').innerHTML = `
    <div class="flex items-center gap-2.5 animate-fade-in">
      <div class="w-8 h-8 rounded-full bg-brand-panel2 border border-brand-border flex items-center justify-center text-xs font-bold text-brand-gold">${initials(me.email)}</div>
      <div class="min-w-0 flex-1">
        <div class="text-xs font-semibold truncate">${me.email}</div>
        <div class="text-[10px] text-brand-muted capitalize">${me.role.replace('_', ' ')}</div>
      </div>
      <button id="logout-btn" title="Log out" class="text-brand-muted hover:text-brand-terracotta transition-colors p-1 rounded">
        ${svgIcon('M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1')}
      </button>
    </div>`;
  document.getElementById('logout-btn').onclick = async () => {
    await fetch('/api/logout', { method: 'POST' });
    window.location.href = '/login';
  };

  return me;
}

// Shared in-app dialog — replaces native prompt()/confirm()/alert() with a
// styled modal matching the rest of the app. Promise-based: dialogPrompt
// resolves to the entered string (or null if cancelled), dialogConfirm to
// a boolean, dialogAlert to undefined once dismissed.
function _dialogRoot() {
  let root = document.getElementById('aiwebmaster-dialog-root');
  if (!root) {
    root = document.createElement('div');
    root.id = 'aiwebmaster-dialog-root';
    document.body.appendChild(root);
  }
  return root;
}

function _showDialog({ message, input, inputValue, confirmText, cancelText, danger }) {
  return new Promise((resolve) => {
    const root = _dialogRoot();
    const isPrompt = !!input;
    const isAlert = cancelText === null;
    const iconWrap = danger
      ? '<div class="w-11 h-11 rounded-full bg-brand-terracotta/15 flex items-center justify-center shrink-0"><svg class="w-5 h-5 text-brand-terracotta" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m0 3.75h.008v.008H12v-.008zM21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg></div>'
      : '<div class="w-11 h-11 rounded-full bg-brand-gold/15 flex items-center justify-center shrink-0"><svg class="w-5 h-5 text-brand-gold" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg></div>';
    root.innerHTML = `
      <div id="dlg-overlay" class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm animate-fade-in p-4">
        <div class="w-full max-w-sm bg-brand-panel border border-brand-border rounded-2xl p-6 shadow-2xl animate-pop-in">
          <div class="flex items-start gap-3.5 mb-5">
            ${iconWrap}
            <div class="text-sm text-brand-ink leading-relaxed pt-2">${message || ''}</div>
          </div>
          ${isPrompt ? `<input id="dlg-input" type="text" value="${(inputValue || '').replace(/"/g, '&quot;')}" class="w-full px-3.5 py-2.5 rounded-lg bg-brand-navy2 border border-brand-border text-sm outline-none focus:border-brand-gold focus:shadow-glow transition-all mb-5" />` : ''}
          <div class="flex justify-end gap-2.5">
            ${!isAlert ? `<button id="dlg-cancel" class="px-4 py-2 rounded-lg text-xs font-semibold text-brand-muted hover:text-brand-ink hover:bg-brand-panel2 transition-all">${cancelText || 'Cancel'}</button>` : ''}
            <button id="dlg-ok" class="px-4 py-2 rounded-lg text-xs font-semibold transition-all active:scale-[.97] ${danger ? 'bg-brand-terracotta text-white hover:brightness-110' : 'bg-brand-gold text-brand-navy hover:brightness-105'}">${confirmText || 'OK'}</button>
          </div>
        </div>
      </div>`;
    const close = (val) => { root.innerHTML = ''; resolve(val); };
    document.getElementById('dlg-ok').onclick = () => close(isPrompt ? document.getElementById('dlg-input').value : true);
    const cancelBtn = document.getElementById('dlg-cancel');
    if (cancelBtn) cancelBtn.onclick = () => close(isPrompt ? null : false);
    document.getElementById('dlg-overlay').addEventListener('click', (e) => {
      if (e.target.id === 'dlg-overlay') close(isPrompt ? null : (isAlert ? undefined : false));
    });
    if (isPrompt) {
      const inp = document.getElementById('dlg-input');
      inp.focus(); inp.select();
      inp.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') close(inp.value);
        if (e.key === 'Escape') close(null);
      });
    } else {
      document.addEventListener('keydown', function onEsc(e) {
        if (e.key === 'Escape') { document.removeEventListener('keydown', onEsc); close(isAlert ? undefined : false); }
      });
    }
  });
}

function dialogPrompt(message, defaultValue) {
  return _showDialog({ message, input: true, inputValue: defaultValue || '', confirmText: 'Save' });
}
function dialogConfirm(message, opts) {
  opts = opts || {};
  return _showDialog({ message, confirmText: opts.confirmText || 'Confirm', cancelText: opts.cancelText || 'Cancel', danger: opts.danger });
}
function dialogAlert(message) {
  return _showDialog({ message, confirmText: 'OK', cancelText: null });
}

async function updateGitBadge() {
  const badge = document.getElementById('nav-git-badge');
  if (!badge) return;
  try {
    const res = await fetch('/api/git/status');
    if (!res.ok) return; // 403 for roles without git access — badge just stays hidden
    const { files } = await res.json();
    if (files && files.length) {
      badge.textContent = files.length > 99 ? '99+' : files.length;
      badge.classList.remove('hidden');
      badge.classList.add('flex');
      badge.title = `${files.length} uncommitted change${files.length === 1 ? '' : 's'}`;
    }
  } catch (e) { /* leave badge hidden */ }
}

function chatTimeAgo(iso) {
  // Postgres timestamptz already includes a +00:00 offset — appending 'Z' on
  // top of that produces an invalid/unparseable string (Date -> NaN).
  const s = Math.floor((Date.now() - new Date(iso.replace(' ', 'T'))) / 1000);
  if (isNaN(s)) return '';
  if (s < 60) return 'now';
  if (s < 3600) return Math.floor(s / 60) + 'm';
  if (s < 86400) return Math.floor(s / 3600) + 'h';
  return Math.floor(s / 86400) + 'd';
}

async function mountChatMenu(activePath) {
  const params = new URLSearchParams(window.location.search);
  const currentSessionId = activePath === '/' && params.get('session') ? parseInt(params.get('session')) : null;
  let expanded = localStorage.getItem('aiwebmaster_chat_menu_expanded') !== '0';
  const wrap = document.getElementById('chat-menu');

  async function render() {
    let sessionsHtml = '<div class="text-xs text-brand-muted px-3 py-2 animate-pulse">Loading…</div>';
    let sessions = [];
    try {
      const r = await fetch('/api/chat/sessions');
      if (r.ok) sessions = (await r.json()).sessions || [];
    } catch (e) { /* ignore, show empty */ }

    sessionsHtml = sessions.length
      ? sessions.map((s, i) => `
        <div class="group flex items-center gap-1 rounded-lg pl-8 pr-1.5 py-1.5 text-xs transition-all animate-fade-in-left
          ${s.id === currentSessionId ? 'bg-brand-panel2 text-brand-ink' : 'text-brand-muted hover:bg-brand-panel/70 hover:text-brand-ink'}"
          style="animation-delay:${Math.min(i*20,160)}ms">
          <a href="/?session=${s.id}" class="flex-1 truncate" title="${s.title.replace(/"/g,'&quot;')}">${s.title.replace(/</g,'&lt;')}</a>
          <span class="text-[9px] text-brand-muted shrink-0 group-hover:hidden">${chatTimeAgo(s.updated_at)}</span>
          <button data-rename="${s.id}" class="hidden group-hover:inline-flex text-brand-muted hover:text-brand-gold p-0.5 shrink-0">${svgIcon('M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z').replace('w-[18px] h-[18px]','w-3 h-3')}</button>
          <button data-delete="${s.id}" class="hidden group-hover:inline-flex text-brand-muted hover:text-brand-terracotta p-0.5 shrink-0">${svgIcon('M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16').replace('w-[18px] h-[18px]','w-3 h-3')}</button>
        </div>`).join('')
      : `<div class="text-[11px] text-brand-muted pl-8 py-1.5">No chats yet</div>`;

    wrap.innerHTML = `
      <div class="flex items-center gap-1 rounded-lg px-1 py-1 ${activePath === '/' && !currentSessionId ? '' : ''}">
        <a href="/" class="flex-1 flex items-center gap-3 rounded-lg px-2 py-1.5 text-sm font-medium transition-all
          ${activePath === '/' ? 'text-brand-ink' : 'text-brand-muted hover:text-brand-ink'}">
          <span class="${activePath === '/' ? 'text-brand-gold' : 'text-brand-muted'}">${svgIcon('M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-6l-4 4v-4z')}</span>
          Chat
        </a>
        <button id="chat-menu-toggle" class="p-1.5 text-brand-muted hover:text-brand-ink transition-transform" style="transform: rotate(${expanded ? '90' : '0'}deg)">
          ${svgIcon('M9 5l7 7-7 7').replace('w-[18px] h-[18px]','w-3.5 h-3.5')}
        </button>
      </div>
      <div id="chat-submenu" class="${expanded ? '' : 'hidden'} flex flex-col gap-0.5 mt-0.5 mb-1">
        <a href="/" class="flex items-center gap-2 rounded-lg pl-8 pr-2 py-1.5 text-xs font-medium text-brand-gold hover:bg-brand-panel/70 transition-all">
          + New chat
        </a>
        ${sessionsHtml}
      </div>`;

    document.getElementById('chat-menu-toggle').onclick = (e) => {
      e.preventDefault();
      expanded = !expanded;
      localStorage.setItem('aiwebmaster_chat_menu_expanded', expanded ? '1' : '0');
      document.getElementById('chat-submenu').classList.toggle('hidden', !expanded);
      document.getElementById('chat-menu-toggle').style.transform = `rotate(${expanded ? '90' : '0'}deg)`;
    };
    wrap.querySelectorAll('[data-rename]').forEach(btn => {
      btn.onclick = async (e) => {
        e.preventDefault(); e.stopPropagation();
        const id = btn.dataset.rename;
        const cur = sessions.find(s => String(s.id) === id)?.title || '';
        const title = await dialogPrompt('Rename chat', cur);
        if (!title || !title.trim()) return;
        await fetch(`/api/chat/sessions/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: title.trim() }) });
        render();
      };
    });
    wrap.querySelectorAll('[data-delete]').forEach(btn => {
      btn.onclick = async (e) => {
        e.preventDefault(); e.stopPropagation();
        const id = btn.dataset.delete;
        if (!(await dialogConfirm('Delete this chat? This cannot be undone.', { confirmText: 'Delete', danger: true }))) return;
        await fetch(`/api/chat/sessions/${id}`, { method: 'DELETE' });
        if (String(currentSessionId) === id) { window.location.href = '/'; return; }
        render();
      };
    });
  }

  await render();
}

// Agent Terminal's session list, in the shared left sidebar under the
// "Agent Terminal" nav item — mirrors mountChatMenu's pattern so Agent
// Terminal stops having its own separate side panel (was two side-by-side
// sidebars: this shared one, and agent.html's own <aside>). The slot only
// exists in the DOM for roles that pass Agent Terminal's nav filter
// (requires: 'codegen_agent'), so no extra 403 handling needed here.
async function mountAgentMenu(activePath) {
  const wrap = document.getElementById('agent-menu');
  if (!wrap) return; // nav item filtered out for this role — nothing to mount

  const params = new URLSearchParams(window.location.search);
  const currentSessionId = activePath === '/agent' && params.get('session') ? parseInt(params.get('session')) : null;
  let expanded = localStorage.getItem('aiwebmaster_agent_menu_expanded') !== '0';

  async function render() {
    let sessions = [];
    try {
      const r = await fetch('/api/agent/sessions');
      if (r.ok) sessions = (await r.json()).sessions || [];
    } catch (e) { /* ignore, show empty */ }

    const sessionsHtml = sessions.length
      ? sessions.map((s, i) => `
        <div class="group flex items-center gap-1 rounded-lg pl-8 pr-1.5 py-1.5 text-xs transition-all animate-fade-in-left
          ${s.id === currentSessionId ? 'bg-brand-panel2 text-brand-ink' : 'text-brand-muted hover:bg-brand-panel/70 hover:text-brand-ink'}"
          style="animation-delay:${Math.min(i*20,160)}ms">
          <a href="/agent?session=${s.id}" class="flex-1 truncate" title="${s.title.replace(/"/g,'&quot;')}">${s.title.replace(/</g,'&lt;')}</a>
          ${s.tool ? `<span class="text-[9px] px-1 py-0.5 rounded bg-brand-panel2 text-brand-muted shrink-0 group-hover:hidden">${s.tool}</span>` : ''}
          <button data-agent-delete="${s.id}" class="hidden group-hover:inline-flex text-brand-muted hover:text-brand-terracotta p-0.5 shrink-0">${svgIcon('M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16').replace('w-[18px] h-[18px]','w-3 h-3')}</button>
        </div>`).join('')
      : `<div class="text-[11px] text-brand-muted pl-8 py-1.5">No sessions yet</div>`;

    wrap.innerHTML = `
      <div id="agent-submenu" class="${expanded ? '' : 'hidden'} flex flex-col gap-0.5 mt-0.5 mb-1">
        <a href="/agent" class="flex items-center gap-2 rounded-lg pl-8 pr-2 py-1.5 text-xs font-medium text-brand-gold hover:bg-brand-panel/70 transition-all">
          + New session
        </a>
        ${sessionsHtml}
      </div>`;

    document.getElementById('agent-menu-toggle').onclick = (e) => {
      e.preventDefault();
      expanded = !expanded;
      localStorage.setItem('aiwebmaster_agent_menu_expanded', expanded ? '1' : '0');
      document.getElementById('agent-submenu').classList.toggle('hidden', !expanded);
      document.getElementById('agent-menu-toggle').style.transform = `rotate(${expanded ? '90' : '0'}deg)`;
    };
    wrap.querySelectorAll('[data-agent-delete]').forEach(btn => {
      btn.onclick = async (e) => {
        e.preventDefault(); e.stopPropagation();
        const id = btn.dataset.agentDelete;
        if (!(await dialogConfirm('Delete this agent session? This cannot be undone.', { confirmText: 'Delete', danger: true }))) return;
        await fetch(`/api/agent/sessions/${id}`, { method: 'DELETE' });
        if (String(currentSessionId) === id) { window.location.href = '/agent'; return; }
        render();
      };
    });
  }

  await render();
}

const ROLE_PERMISSIONS = {
  docker_ops: ['docker', 'git'],
  ui_editor: ['content', 'nav_link'],
  infra_admin: ['docker', 'git', 'sql', 'code_edit', 'codegen_agent'],
  super_admin: ['docker', 'git', 'sql', 'content', 'nav_link', 'user_management', 'publish', 'rollback', 'code_edit', 'codegen_agent'],
};
function canRun(me, actionType) {
  return me && (ROLE_PERMISSIONS[me.role] || []).includes(actionType);
}

// Removes nav links the current role can't use from the DOM (not just
// visually hidden — gone entirely) so a non-technical ui_editor/docker_ops
// only ever sees links that actually work for them, instead of five that
// 403. Called once, right after /api/me resolves in mountShell().
function filterNav(me) {
  NAV_ITEMS.forEach((item) => {
    if (item.requires && !canRun(me, item.requires)) {
      const el = document.querySelector(`[data-nav-item="${item.path}"]`);
      if (el) (el.parentElement || el).remove();
      if (item.path === '/agent') document.getElementById('agent-menu')?.remove();
    }
  });
}
