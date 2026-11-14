const el = id => document.getElementById(id);
const api = async (path, data) => {
  const options = data === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-Panel-Request': '1'},
    body: JSON.stringify(data)
  };
  const response = await fetch(path, options);
  const content = await response.json();
  if (!response.ok || content.ok === false) throw Error(content.error || 'Yêu cầu thất bại');
  return content;
};
function notice(message) {
  const box = el('notice');
  box.style.display = 'block';
  box.textContent = message;
}
function listLines(id, rows) {
  const root = el(id);
  root.replaceChildren();
  for (const row of rows) {
    const li = document.createElement('li');
    const name = document.createElement('span');
    name.textContent = row.text;
    name.className = 'item';
    if (row.onclick) name.addEventListener('click', row.onclick);
    li.appendChild(name);
    if (row.detail) {
      const detail = document.createElement('span');
      detail.className = 'tiny';
      detail.textContent = row.detail;
      li.appendChild(detail);
    }
    root.appendChild(li);
  }
}
let running = false;
let editing = '';
async function status() {
  try {
    const data = await api('/api/status');
    const latest = data.active || data.runs[0];
    running = Boolean(data.active);
    el('status').textContent = running ? (data.console_connected ? 'Đang chạy' : 'Đang khởi động') : 'Đã tắt';
    el('status').className = 'stat' + (data.console_connected ? ' accent' : '');
    el('headerState').textContent = data.console_connected ? '● Online' : (running ? '◌ Starting' : '○ Offline');
    el('runDetail').textContent = latest ? ('Run #' + latest.databaseId + ' · ' + latest.status) : 'Chưa có phiên nào';
    el('bridgeState').textContent = data.console_connected ? 'Tunnel đang hoạt động' : 'Chưa kết nối';
    el('driveState').textContent = data.drive_connected ? 'Drive đã kết nối' : 'Chưa cấp OAuth cho rclone VPS';
    el('startBtn').disabled = running;
    el('stopBtn').disabled = !data.console_connected;
    el('sendCmd').disabled = !data.console_connected;
    listLines('runs', data.runs.map(r => ({
      text: 'Run #' + r.databaseId, detail: r.status + (r.conclusion ? ' · ' + r.conclusion : ''),
      onclick: () => window.open(r.url, '_blank', 'noopener')
    })));
    if (data.console_connected) {
      try {
        const result = await api('/api/logs');
        el('console').textContent = result.logs || '(Chưa có log)';
        el('console').scrollTop = el('console').scrollHeight;
      } catch (e) { el('console').textContent = e.message; }
    } else el('console').textContent = 'Chưa có kết nối console. Server chưa chạy hoặc đang khởi động.';
    if (data.drive_connected) {
      await Promise.all([browse(), backups()]);
    } else {
      listLines('fileList', [{text:'Cần cấu hình OAuth rclone trên VPS'}]);
      listLines('backups', [{text:'Chưa kết nối Drive'}]);
    }
  } catch (e) { notice(e.message); }
}
async function doAction(endpoint, payload = {}) {
  try {
    const data = await api(endpoint, payload);
    notice(data.message || 'Đã gửi yêu cầu.');
    await status();
  } catch (e) { notice(e.message); }
}
async function browse() {
  try {
    const path = el('filePath').value;
    const data = await api('/api/files?path=' + encodeURIComponent(path));
    const rows = (data.files || []).map(f => ({
      text: (f.IsDir ? '📁 ' : '📄 ') + f.Name,
      detail: f.IsDir ? 'Folder' : (Math.round((f.Size || 0)/1024) + ' KB'),
      onclick: () => f.IsDir ? goFolder(f.Name) : editFile(f.Name)
    }));
    if (path) rows.unshift({text:'↰ Thư mục cha',onclick:() => {el('filePath').value = path.split('/').slice(0,-1).join('/');browse();}});
    listLines('fileList', rows.length ? rows : [{text:'Thư mục trống'}]);
  } catch (e) { listLines('fileList', [{text: e.message}]); }
}
function goFolder(name) {
  el('filePath').value = [el('filePath').value, name].filter(Boolean).join('/');
  browse();
}
async function editFile(name) {
  const path = [el('filePath').value, name].filter(Boolean).join('/');
  try {
    const data = await api('/api/file?path=' + encodeURIComponent(path));
    editing = data.path;
    el('editPath').textContent = editing;
    el('fileText').value = data.content;
    el('editor').hidden = false;
    el('editor').scrollIntoView({behavior:'smooth',block:'nearest'});
  } catch (e) { notice(e.message); }
}
async function backups() {
  try {
    const data = await api('/api/backup');
    const files = (data.files || []).sort((a,b) => b.Name.localeCompare(a.Name)).slice(0,15);
    listLines('backups', files.length ? files.map(f => ({
      text:'↺ ' + f.Name, detail:Math.round((f.Size || 0)/1048576)+' MB',
      onclick: () => {
        if (confirm('Khôi phục backup '+f.Name+' và khởi động lại server? Chỉ thực hiện khi server đã tắt.')) {
          doAction('/api/restore',{backup:f.Name});
        }
      }
    })) : [{text:'Chưa có snapshot'}]);
  } catch (e) { listLines('backups', [{text:e.message}]); }
}
el('startBtn').onclick = () => doAction('/api/start');
el('stopBtn').onclick = () => {if(confirm('Dừng Paper an toàn và đồng bộ world lên Drive?')) doAction('/api/stop');};
el('refreshBtn').onclick = status;
el('browseBtn').onclick = browse;
el('sendCmd').onclick = () => {
  const command = el('command').value.trim();
  if (command) doAction('/api/command', {command});
  el('command').value = '';
};
el('command').addEventListener('keydown', e => {if(e.key === 'Enter') el('sendCmd').click();});
el('saveFile').onclick = () => {if(confirm('Ghi đè file '+editing+' trên Drive?')) doAction('/api/savefile',{path:editing,content:el('fileText').value});};
el('deleteFile').onclick = () => {if(confirm('XÓA file '+editing+' khỏi Drive?')) doAction('/api/deletefile',{path:editing});};
el('closeFile').onclick = () => {el('editor').hidden = true;editing = '';};
status();
setInterval(status, 15000);

el('createFile').onclick = () => {
  const path = el('newFilePath').value.trim();
  if (path && confirm('Tạo hoặc ghi đè file văn bản '+path+' trên Drive?')) {
    doAction('/api/savefile',{path,content:''});
  }
};
