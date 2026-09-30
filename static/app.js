const names = {
  front_left: 'Frontal izquierdo', front_right: 'Frontal derecho',
  center: 'Central', rear_left: 'Trasero izquierdo',
  rear_right: 'Trasero derecho', lfe: 'Subwoofer'
};
const filesElement = document.getElementById('files');
const playbacksElement = document.getElementById('playbacks');
const messageElement = document.getElementById('message');
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
  if (!files.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'Aún no hay archivos. Sube un MP3, OGG o WAV.';
    filesElement.appendChild(empty);
  }
  for (const file of files) {
    const row = document.createElement('label');
    row.className = 'file-row';
    const input = document.createElement('input');
    input.type = 'radio';
    input.name = 'audio-file';
    input.value = file.id;
    input.checked = file.id === selectedId;
    input.addEventListener('change', () => {
      selectedId = file.id;
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
    const title = document.createElement('span');
    const file = files.find(item => item.id === playback.file_id);
    title.textContent = `${file?.name || playback.file_id} · ${playback.channels.map(channel => names[channel]).join(' + ')}`;
    const stop = document.createElement('button');
    stop.type = 'button';
    stop.textContent = 'Detener';
    stop.addEventListener('click', async () => {
      try {
        await request(`/api/playbacks/${playback.id}`, {method: 'DELETE'});
        await refreshPlaybacks();
      } catch (error) { message(error.message, true); }
    });
    row.append(title, stop);
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
