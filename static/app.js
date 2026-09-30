const names = {
  front_left: 'Frontal izquierdo', front_right: 'Frontal derecho',
  center: 'Central', rear_left: 'Trasero izquierdo',
  rear_right: 'Trasero derecho', lfe: 'Subwoofer'
};
const filesElement = document.getElementById('files');
const playbacksElement = document.getElementById('playbacks');
const messageElement = document.getElementById('message');
const selectedFileElement = document.getElementById('selected-file');
const fileCountElement = document.getElementById('file-count');
const activeCountElement = document.getElementById('active-count');
const stopAllButton = document.getElementById('stop-all');
let files = [];
let selectedId = null;
let lastAudioError = null;

function message(text, isError = false) {
  messageElement.textContent = text;
  messageElement.classList.toggle('error', isError);
}

async function request(url, options = {}) {
  const response = await fetch(url, options);
  let data;
  try { data = await response.json(); } catch (_) { data = {}; }
  if (!response.ok) throw new Error(data.error || `Error HTTP ${response.status}`);
  return data;
}

function renderFiles() {
  filesElement.replaceChildren();
  fileCountElement.textContent = `${files.length} ${files.length === 1 ? 'archivo' : 'archivos'}`;
  if (!files.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'Aún no hay archivos. Sube un MP3, OGG o WAV.';
    filesElement.appendChild(empty);
  }
  for (const file of files) {
    const row = document.createElement('label');
    row.className = 'file-row';
    row.classList.toggle('is-selected', file.id === selectedId);
    const input = document.createElement('input');
    input.type = 'radio';
    input.name = 'audio-file';
    input.value = file.id;
    input.checked = file.id === selectedId;
    input.addEventListener('change', () => {
      selectedId = file.id;
      for (const other of filesElement.querySelectorAll('.file-row')) {
        other.classList.toggle('is-selected', other === row);
      }
      updateButtons();
    });
    const name = document.createElement('span');
    name.className = 'file-name';
    name.textContent = file.name;
    const size = document.createElement('span');
    size.className = 'file-size';
    size.textContent = `${(file.size / 1024 / 1024).toFixed(1)} MB`;
    row.append(input, name, size);
    filesElement.appendChild(row);
  }
  updateButtons();
}

function updateButtons() {
  const selected = files.find(file => file.id === selectedId);
  selectedFileElement.textContent = selected ? selected.name : 'Ninguno todavía';
  selectedFileElement.title = selected ? selected.name : '';
  for (const button of document.querySelectorAll('[data-channel]')) {
    button.disabled = !selectedId;
  }
}

async function refreshFiles() {
  const result = await request('/api/files');
  files = result.files;
  if (!files.some(file => file.id === selectedId)) selectedId = files[0]?.id || null;
  renderFiles();
}

async function refreshPlaybacks() {
  const result = await request('/api/playbacks');
  activeCountElement.textContent = `${result.playbacks.length} / 4`;
  stopAllButton.disabled = result.playbacks.length === 0;
  for (const button of document.querySelectorAll('[data-channel]')) {
    button.classList.toggle('is-playing', result.playbacks.some(
      playback => playback.channels.includes(button.dataset.channel)
    ));
  }
  playbacksElement.replaceChildren();
  if (!result.playbacks.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'No hay sonidos en reproducción.';
    playbacksElement.appendChild(empty);
  }
  for (const playback of result.playbacks) {
    const row = document.createElement('div');
    row.className = 'playback-row';
    const copy = document.createElement('div');
    copy.className = 'playback-copy';
    const title = document.createElement('span');
    title.className = 'playback-name';
    const file = files.find(item => item.id === playback.file_id);
    title.textContent = file?.name || playback.file_id;
    const meta = document.createElement('span');
    meta.className = 'playback-meta';
    meta.textContent = playback.channels.map(channel => names[channel]).join(' + ');
    copy.append(title, meta);
    const stop = document.createElement('button');
    stop.type = 'button';
    stop.textContent = 'Detener';
    stop.addEventListener('click', async () => {
      try {
        await request(`/api/playbacks/${playback.id}`, {method: 'DELETE'});
        await refreshPlaybacks();
      } catch (error) { message(error.message, true); }
    });
    row.append(copy, stop);
    playbacksElement.appendChild(row);
  }
}

document.getElementById('upload').addEventListener('change', async event => {
  const file = event.target.files[0];
  if (!file) return;
  if (!/\.(mp3|ogg|wav)$/i.test(file.name)) {
    message('Selecciona un archivo MP3, OGG o WAV.', true);
    return;
  }
  if (file.size > 100 * 1024 * 1024) {
    message('El archivo supera el límite de 100 MB.', true);
    return;
  }
  try {
    message(`Subiendo ${file.name}…`);
    const uploaded = await request(`/upload?filename=${encodeURIComponent(file.name)}`,
                                   {method: 'POST', body: file});
    selectedId = uploaded.id;
    await refreshFiles();
    message(`${file.name} está listo.`);
  } catch (error) { message(error.message, true); }
  event.target.value = '';
});

for (const button of document.querySelectorAll('[data-channel]')) {
  button.addEventListener('click', async () => {
    if (!selectedId) return;
    try {
      await request('/api/play', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({file_id: selectedId, channel: button.dataset.channel})
      });
      message(`Reproduciendo en ${names[button.dataset.channel]}.`);
      await refreshPlaybacks();
    } catch (error) { message(error.message, true); }
  });
}

document.getElementById('stop-all').addEventListener('click', async () => {
  try {
    await request('/api/playbacks', {method: 'DELETE'});
    await refreshPlaybacks();
    message('Reproducción detenida.');
  } catch (error) { message(error.message, true); }
});

async function refresh() {
  try {
    await refreshPlaybacks();
    const health = await request('/api/health');
    if (health.last_error && health.last_error !== lastAudioError) {
      message(health.last_error, true);
    }
    lastAudioError = health.last_error;
  } catch (error) { message(error.message, true); }
}

refreshFiles().then(refresh).catch(error => message(error.message, true));
setInterval(refresh, 1500);
