'use strict';

// Trang setting của collector. Dữ liệu từ server chỉ được đưa vào DOM bằng
// textContent / createElement (không dùng innerHTML) vì tên folder có thể chứa ký tự HTML.

const $ = (id) => document.getElementById(id);

const state = {
  profilePath: '',     // profile đang hiển thị
  accounts: [],        // dữ liệu từ /api/folders
  selected: new Map(), // path -> bool, chỉ folder có mbox
  dirty: false,        // có thay đổi chưa lưu
  connected: false,    // đã kết nối server (token hợp lệ)
  sources: new Map(),  // folder_path -> số mail đã có trên server
  syncing: false,      // đang đồng bộ
};

const pageTitle = document.title;

const browse = { path: '', parent: '' };

// ---------- tiện ích ----------

function el(tag, props = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'class') e.className = v;
    else if (k === 'text') e.textContent = v;
    else e[k] = v;
  }
  for (const c of children) if (c != null) e.append(c);
  return e;
}

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  let data = null;
  try { data = await res.json(); } catch { /* phản hồi không phải JSON */ }
  if (!res.ok) throw new Error((data && data.error) || `Lỗi ${res.status}`);
  return data;
}

const nf = new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 1 });

function formatSize(b) {
  if (b >= 1024 ** 3) return nf.format(b / 1024 ** 3) + ' GB';
  if (b >= 1024 ** 2) return nf.format(b / 1024 ** 2) + ' MB';
  if (b >= 1024) return nf.format(b / 1024) + ' KB';
  return b + ' B';
}

const pad = (n) => String(n).padStart(2, '0');

function formatDate(iso) {
  const d = new Date(iso);
  if (isNaN(d) || d.getFullYear() < 1971) return '';
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const day = new Date(d); day.setHours(0, 0, 0, 0);
  const diff = Math.round((today - day) / 86400000);
  if (diff === 0) return 'hôm nay';
  if (diff === 1) return 'hôm qua';
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()}`;
}

function* walk(folders) {
  for (const f of folders) {
    yield f;
    yield* walk(f.children || []);
  }
}

const mboxFolders = (folders) => [...walk(folders)].filter((f) => f.has_mbox);

function showError(msg) { $('message').textContent = msg; $('message').hidden = false; }
function showWarning(msg) { $('warning').textContent = msg; $('warning').hidden = false; }
function showInfo(msg) { $('info').textContent = msg; $('info').hidden = false; }
function clearMessages() { $('message').hidden = true; $('warning').hidden = true; $('info').hidden = true; }

function markDirty() {
  state.dirty = true;
  $('saved-msg').textContent = '';
  updateSyncButton();
}

// Nút Đồng bộ chỉ bật khi: đã lưu cấu hình, đã kết nối server, không đang đồng bộ.
function updateSyncButton() {
  const btn = $('sync-btn');
  let reason = '';
  if (!state.profilePath) reason = 'Chọn profile trước';
  else if (state.dirty) reason = 'Bấm Lưu trước khi đồng bộ';
  else if (!state.connected) reason = 'Chưa kết nối server';
  else if (state.syncing) reason = 'Đang đồng bộ';
  btn.disabled = reason !== '';
  btn.title = reason;
}

// ---------- profile ----------

function renderDetected(profiles) {
  const box = $('detected');
  box.replaceChildren();
  if (!profiles.length) {
    box.append('Không tự tìm thấy profile nào. Hãy dán đường dẫn hoặc bấm "Chọn thư mục…".');
    return;
  }
  box.append(`Tự tìm thấy ${profiles.length} profile: `);
  profiles.forEach((p, i) => {
    if (i > 0) box.append(' · ');
    const b = el('button', { type: 'button', class: 'link', title: p.path, text: p.name || p.path });
    b.addEventListener('click', () => loadFolders(p.path));
    box.append(b);
    if (p.is_default) box.append(' (mặc định)');
  });
}

function resetTree() {
  state.profilePath = '';
  state.accounts = [];
  state.selected = new Map();
  state.dirty = false;
  $('tree').replaceChildren();
  $('footer').hidden = true;
  $('profiles-root').hidden = true;
}

async function loadFolders(path) {
  path = (path || '').trim();
  if (!path) return;
  if (state.dirty && !confirm('Có thay đổi chưa lưu. Bỏ qua và mở profile khác?')) return;
  clearMessages();
  $('profile-path').value = path;
  let data;
  try {
    data = await api('GET', '/api/folders?profile=' + encodeURIComponent(path));
  } catch (e) {
    resetTree();
    showError(e.message);
    return;
  }
  resetTree();
  if (data.kind === 'profiles_root') {
    showProfilesRoot(data.profiles);
    return;
  }
  state.profilePath = data.profile_path;
  state.accounts = data.accounts;
  $('profile-path').value = data.profile_path;
  for (const a of data.accounts) {
    for (const f of mboxFolders(a.folders)) state.selected.set(f.path, f.selected);
  }
  $('auto-include').checked = data.auto_include_new_folders;
  $('saved-msg').textContent = '';
  if (data.config_warning) showWarning(data.config_warning);
  $('footer').hidden = false;
  render();
  updateSyncButton();
}

function showProfilesRoot(profiles) {
  const list = $('profiles-list');
  list.replaceChildren();
  if (!profiles.length) list.append(el('li', { text: 'Không có profile nào trong profiles.ini.' }));
  for (const p of profiles) {
    const b = el('button', { type: 'button', class: 'link', text: p.name || p.path });
    b.addEventListener('click', () => loadFolders(p.path));
    list.append(el('li', {}, b, p.is_default ? ' (mặc định)' : null, el('span', { class: 'sub', text: p.path })));
  }
  $('profiles-root').hidden = false;
}

// ---------- cây folder ----------

function render() {
  const tree = $('tree');
  tree.replaceChildren();
  if (!state.accounts.length) {
    tree.append(el('div', { class: 'panel note', text: 'Profile này chưa có folder mail nào.' }));
  }
  for (const acct of state.accounts) tree.append(renderAccount(acct));
  const vanished = renderVanished();
  if (vanished) tree.append(vanished);
  updateSummary();
}

// Folder có mail trên server nhưng không còn trong profile (đổi tên, xóa trong Thunderbird):
// không có dòng nào trong cây để bỏ tick, nên liệt kê riêng để xóa được khỏi server.
function renderVanished() {
  const local = new Set(state.accounts.flatMap((a) => mboxFolders(a.folders)).map((f) => f.path));
  const gone = [...state.sources].filter(([p, n]) => n > 0 && !local.has(p));
  if (!gone.length) return null;
  const box = el('section', { class: 'account' });
  box.append(el('div', { class: 'grp' },
    el('span', { text: 'Có trên server, không còn trong Thunderbird' }),
    el('span', { class: 'meta', text: `${gone.length} folder` })));
  box.append(el('div', { class: 'note', text:
    'Folder đã đổi tên hoặc xóa trong Thunderbird (hoặc thuộc profile khác). ' +
    'Mail của các folder này vẫn nằm trên server cho tới khi bạn xóa.' }));
  for (const [path, n] of gone) {
    const del = el('button', { type: 'button', class: 'link danger', text: `Xóa ${n} mail khỏi server` });
    del.addEventListener('click', () => deleteFolderMail(path, path, n));
    box.append(el('div', { class: 'row parent' }, el('span', { class: 'cb-spacer' }),
      el('span', { class: 'name' }, el('span', { class: 'fname', text: path }), ' ', del)));
  }
  return box;
}

function setSelected(folders, value) {
  for (const f of folders) state.selected.set(f.path, value);
  markDirty();
  render();
}

function renderAccount(acct) {
  const box = el('section', { class: 'account' });
  const all = mboxFolders(acct.folders);
  const n = all.filter((f) => state.selected.get(f.path)).length;
  const total = all.reduce((s, f) => s + f.size, 0);

  const cb = el('input', { type: 'checkbox' });
  cb.checked = all.length > 0 && n === all.length;
  cb.indeterminate = n > 0 && n < all.length;
  cb.disabled = all.length === 0;
  cb.addEventListener('change', () => setSelected(all, cb.checked));

  const title = acct.kind === 'imap_cache' ? 'Server: ' + acct.name : acct.name;
  box.append(el('label', { class: 'grp' }, cb,
    el('span', { text: title }),
    el('span', { class: 'meta', text: `${all.length} folder · ${formatSize(total)}` })));

  if (acct.unsupported) {
    box.append(el('div', { class: 'note', text: 'Chưa hỗ trợ kiểu lưu maildir' }));
  } else if (!acct.folders.length) {
    box.append(el('div', { class: 'note', text: 'Không có folder' }));
  }
  for (const f of acct.folders) appendRows(box, f, 0);
  return box;
}

function appendRows(box, f, depth) {
  const on = f.has_mbox && state.selected.get(f.path);
  const row = el(f.has_mbox ? 'label' : 'div', {
    class: 'row' + (f.has_mbox ? (on ? '' : ' off') : ' parent'),
  });
  row.style.paddingLeft = 8 + depth * 22 + 'px';

  if (f.has_mbox) {
    const cb = el('input', { type: 'checkbox' });
    cb.checked = on;
    cb.addEventListener('change', () => setSelected(mboxFolders([f]), cb.checked));
    row.append(cb);
  } else {
    row.append(el('span', { class: 'cb-spacer' }));
  }

  const name = el('span', { class: 'name' }, el('span', { class: 'fname', text: f.name }));
  if (f.default_excluded) name.append(el('span', { class: 'tag tag-skip', text: 'mặc định bỏ qua' }));
  if (f.is_new) name.append(el('span', { class: 'tag tag-new', text: 'mới' }));
  const onServer = f.has_mbox ? state.sources.get(f.path) || 0 : 0;
  if (onServer > 0 && on) {
    name.append(el('span', { class: 'tag tag-server', text: `${onServer} mail trên server` }));
  } else if (onServer > 0) {
    // Folder bỏ tick nhưng server vẫn còn mail: cho chọn xóa khỏi server.
    const del = el('button', { type: 'button', class: 'link danger', text: `Xóa ${onServer} mail khỏi server` });
    del.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      deleteFolderMail(f.path, f.name, onServer);
    });
    name.append(' ', del);
  }
  row.append(name,
    el('span', { class: 'meta', text: f.has_mbox ? formatSize(f.size) : '' }),
    el('span', { class: 'meta date', text: f.has_mbox ? formatDate(f.mod_time) : '' }));
  box.append(row);

  for (const c of f.children || []) appendRows(box, c, depth + 1);
}

function updateSummary() {
  const all = state.accounts.flatMap((a) => mboxFolders(a.folders));
  const sel = all.filter((f) => state.selected.get(f.path));
  const size = sel.reduce((s, f) => s + f.size, 0);
  $('summary').textContent = `Đã chọn ${sel.length}/${all.length} folder · ${formatSize(size)}`;
}

// ---------- lưu / đóng ----------

async function save() {
  $('save-btn').disabled = true;
  try {
    // Gửi quyết định cho mọi folder đang hiển thị; folder chưa hiển thị vẫn là "mới".
    const r = await api('POST', '/api/config', {
      profile_path: state.profilePath,
      auto_include_new_folders: $('auto-include').checked,
      folders: Object.fromEntries(state.selected),
    });
    state.dirty = false;
    clearMessages();
    for (const a of state.accounts) for (const f of walk(a.folders)) f.is_new = false;
    render();
    const t = new Date(r.saved_at);
    $('saved-msg').textContent = `Đã lưu lúc ${pad(t.getHours())}:${pad(t.getMinutes())}`;
  } catch (e) {
    showError(e.message);
  } finally {
    $('save-btn').disabled = false;
    updateSyncButton();
  }
}

async function closeServer(ev) {
  ev.preventDefault();
  if (state.syncing && !confirm('Đang đồng bộ. Dừng đồng bộ và đóng?')) return;
  if (state.dirty && !confirm('Có thay đổi chưa lưu. Vẫn đóng?')) return;
  state.dirty = false;
  try { await api('POST', '/api/shutdown', {}); } catch { /* server có thể đã dừng */ }
  document.body.replaceChildren(el('main', { class: 'closed' },
    el('p', { text: 'Đã đóng. Bạn có thể đóng tab này.' })));
}

// ---------- kết nối server ----------

function renderServerStatus(st) {
  state.connected = !!st.connected;
  if (st.server_url) $('server-url').value = st.server_url;
  $('server-token').placeholder = st.has_token
    ? 'Đã lưu token (để trống nếu không đổi)'
    : 'Token do quản trị cấp';
  const box = $('server-status');
  box.className = 'hint ' + (st.connected ? 'ok' : st.error ? 'bad' : '');
  if (st.connected) {
    box.textContent = 'Đã kết nối: ' + (st.display_name ? `${st.display_name} <${st.email}>` : st.email);
  } else if (st.error) box.textContent = 'Chưa kết nối: ' + st.error;
  else if (!st.server_url || !st.has_token) box.textContent = 'Nhập URL server và token để đồng bộ.';
  else box.textContent = '';
  updateSyncButton();
}

async function refreshServer() {
  try {
    renderServerStatus(await api('GET', '/api/server/status'));
  } catch (e) {
    renderServerStatus({ error: e.message });
  }
  if (state.connected) await loadSources();
}

async function saveServer() {
  $('server-save').disabled = true;
  try {
    const st = await api('POST', '/api/server/settings', {
      server_url: $('server-url').value.trim(),
      token: $('server-token').value.trim(),
    });
    $('server-token').value = '';
    renderServerStatus(st);
    if (state.connected) await loadSources();
  } catch (e) {
    const box = $('server-status');
    box.className = 'hint bad';
    box.textContent = e.message;
  } finally {
    $('server-save').disabled = false;
  }
}

async function loadSources() {
  try {
    const list = await api('GET', '/api/server/sources');
    state.sources = new Map(list.map((s) => [s.folder_path, s.message_count]));
  } catch {
    state.sources = new Map();
  }
  if (state.accounts.length) render();
}

async function deleteFolderMail(path, name, count) {
  const ok = confirm(
    `Xóa vĩnh viễn ${count} mail của folder "${name}" khỏi server?\n\n` +
    'Mail vẫn còn trong Thunderbird. Mail cũng nằm ở folder khác đang đồng bộ sẽ được giữ.\n' +
    'Không khôi phục được.');
  if (!ok) return;
  clearMessages();
  try {
    const r = await api('POST', '/api/server/delete-folder', { path });
    showInfo(`Đã xóa ${r.deleted_messages} mail của folder "${name}" khỏi server` +
      (r.kept_messages ? ` (giữ ${r.kept_messages} mail còn ở folder khác).` : '.'));
  } catch (e) {
    showError(e.message);
  }
  await loadSources();
}

// ---------- đồng bộ ----------

let pollTimer = null;

async function startSync() {
  clearMessages();
  try {
    renderSync(await api('POST', '/api/sync/start', {}));
    pollSync();
  } catch (e) {
    showError(e.message);
  }
}

function pollSync() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    let st;
    try {
      st = await api('GET', '/api/sync/status');
    } catch {
      return; // server cục bộ đã dừng
    }
    renderSync(st);
    if (st.state === 'running') pollSync();
    else await loadSources();
  }, 1000);
}

function renderSync(st) {
  state.syncing = st.state === 'running';
  updateSyncButton();
  if (st.state === 'idle') return;
  $('sync-panel').hidden = false;
  $('sync-stop').hidden = !state.syncing;
  const p = st.progress;
  const pct = p && p.bytes_total > 0 ? Math.min(100, Math.round((p.bytes_done / p.bytes_total) * 100)) : 0;
  $('sync-bar').style.width = (state.syncing ? pct : 100) + '%';
  $('sync-title').textContent = state.syncing ? `Đang đồng bộ… ${pct}%` : 'Đồng bộ';
  $('sync-line').textContent = p && p.folder_count
    ? `Folder ${p.folder_index}/${p.folder_count}: ${p.folder_path} — đã đọc ${p.sent} mail, ${p.inserted} mail mới`
    : state.syncing ? 'Đang chuẩn bị…' : '';
  const box = $('sync-result');
  box.hidden = state.syncing;
  if (!state.syncing) {
    box.className = 'result ' + (st.state === 'done' ? 'ok' : st.state === 'stopped' ? 'warn' : 'bad');
    box.replaceChildren(
      el('strong', { text: st.state === 'done' ? '✓ Đồng bộ hoàn thành' : st.state === 'stopped' ? 'Đã dừng đồng bộ' : 'Đồng bộ lỗi' }),
      el('div', { text: (st.message || '').replace(/^Đồng bộ hoàn thành:\s*/, '') }));
    const errs = (st.summary && st.summary.folder_errors) || [];
    for (const fe of errs) box.append(el('div', { class: 'sub', text: `Lỗi folder ${fe.path}: ${fe.message}` }));
    document.title = st.state === 'done' ? '✓ Đồng bộ hoàn thành' : pageTitle;
  } else {
    document.title = `Đang đồng bộ ${pct}% — ${pageTitle}`;
  }
}

async function stopSync() {
  try { renderSync(await api('POST', '/api/sync/stop', {})); } catch { /* bỏ qua */ }
}

// ---------- duyệt thư mục ----------

async function browseTo(path) {
  let data;
  try {
    data = await api('GET', '/api/browse?path=' + encodeURIComponent(path || ''));
  } catch (e) {
    if (path) return browseTo(''); // đường dẫn không hợp lệ: quay về danh sách ổ đĩa
    showError(e.message);
    return;
  }
  browse.path = data.path;
  browse.parent = data.parent;
  $('browse-path').textContent = data.path || 'Máy tính';
  const list = $('browse-list');
  list.replaceChildren();
  if (!data.dirs.length) list.append(el('li', { class: 'empty', text: 'Không có thư mục con' }));
  for (const d of data.dirs) {
    const b = el('button', { type: 'button', text: d.name });
    b.addEventListener('click', () => browseTo(d.path));
    list.append(el('li', {}, b));
  }
  list.scrollTop = 0;
  $('browse-up').disabled = !data.path;
  $('browse-choose').disabled = !data.path;
}

async function openBrowse() {
  $('browse-dialog').showModal();
  await browseTo($('profile-path').value.trim() || state.profilePath);
}

// ---------- khởi động ----------

async function init() {
  $('open-btn').addEventListener('click', () => loadFolders($('profile-path').value));
  $('profile-path').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') loadFolders($('profile-path').value);
  });
  $('browse-btn').addEventListener('click', openBrowse);
  $('browse-up').addEventListener('click', () => browseTo(browse.parent));
  $('browse-cancel').addEventListener('click', () => $('browse-dialog').close());
  $('browse-choose').addEventListener('click', () => {
    $('browse-dialog').close();
    loadFolders(browse.path);
  });
  $('auto-include').addEventListener('change', markDirty);
  $('save-btn').addEventListener('click', save);
  $('sync-btn').addEventListener('click', startSync);
  $('sync-stop').addEventListener('click', stopSync);
  $('server-save').addEventListener('click', saveServer);
  $('close-link').addEventListener('click', closeServer);
  window.addEventListener('beforeunload', (e) => {
    if (state.dirty || state.syncing) e.preventDefault();
  });

  let data;
  try {
    data = await api('GET', '/api/profiles');
  } catch (e) {
    showError(e.message);
    return;
  }
  renderDetected(data.detected);
  const def = data.detected.find((p) => p.is_default);
  const initial = data.saved_profile_path || (def && def.path) || '';
  if (initial) await loadFolders(initial);
  await refreshServer();
  try {
    const st = await api('GET', '/api/sync/status');
    renderSync(st);
    if (st.state === 'running') pollSync();
  } catch { /* bỏ qua */ }
}

init();
