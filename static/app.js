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
const audioStatusElement = document.getElementById('audio-status');
const alsaLogElement = document.getElementById('alsa-log');
const ffmpegLogElement = document.getElementById('ffmpeg-log');
const audioMetricsElement = document.getElementById('audio-metrics');
let files = [];
let selectedId = null;
let lastAudioError = null;
let ost = null;

const ostPlaylistElement = document.getElementById('ost-playlist');
const ostLeftElement = document.getElementById('ost-left');
const ostRightElement = document.getElementById('ost-right');

for (const [channel, label] of Object.entries(names)) {
  for (const select of [ostLeftElement, ostRightElement]) {
    const option = document.createElement('option');
    option.value = channel;
    option.textContent = label;
    select.appendChild(option);
  }
}

for (const tab of document.querySelectorAll('.tab-button')) {
  tab.addEventListener('click', () => {
    for (const other of document.querySelectorAll('.tab-button')) {
      const active = other === tab;
      other.classList.toggle('is-active', active);
      other.setAttribute('aria-selected', String(active));
      document.getElementById(other.getAttribute('aria-controls')).hidden = !active;
    }
  });
}

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

function renderOST() {
  if (!ost) return;
  document.getElementById('ost-count').textContent = `${ost.playlist.length} ${ost.playlist.length === 1 ? 'pista' : 'pistas'}`;
  document.getElementById('ost-current').textContent = ost.current?.name || 'Selecciona una pista';
  document.getElementById('ost-state').textContent = {
    playing: 'Reproduciendo la banda sonora',
    paused: 'En pausa',
    stopped: 'La playlist está detenida.'
  }[ost.state] || ost.state;
  ostLeftElement.value = ost.channels[0];
  ostRightElement.value = ost.channels[1];
  const shuffleButton = document.getElementById('ost-shuffle');
  shuffleButton.classList.toggle('is-active', ost.shuffle);
  shuffleButton.setAttribute('aria-pressed', String(ost.shuffle));
  shuffleButton.setAttribute('aria-label', ost.shuffle ? 'Desactivar modo aleatorio' : 'Activar modo aleatorio');
  const hasTracks = ost.playlist.length > 0;
  for (const id of ['ost-play', 'ost-next', 'ost-previous']) {
    document.getElementById(id).disabled = !hasTracks;
  }
  document.getElementById('ost-pause').disabled = ost.state !== 'playing';
  ostPlaylistElement.replaceChildren();
  if (!hasTracks) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'Sube un MP3 para crear tu playlist.';
    ostPlaylistElement.appendChild(empty);
  }
  for (const [index, track] of ost.playlist.entries()) {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'ost-track';
    row.classList.toggle('is-current', ost.current?.id === track.id);
    const number = document.createElement('span');
    number.className = 'ost-track-number';
    number.textContent = String(index + 1).padStart(2, '0');
    const title = document.createElement('span');
    title.className = 'ost-track-name';
    title.textContent = track.name;
    const icon = document.createElement('span');
    icon.className = 'ost-track-icon';
    icon.textContent = ost.current?.id === track.id && ost.state === 'playing' ? '♫' : '▶';
    row.append(number, title, icon);
    row.addEventListener('click', () => postOST('/api/ost/play', {file_id: track.id}));
    ostPlaylistElement.appendChild(row);
  }
}

async function refreshOST() {
  ost = await request('/api/ost');
  renderOST();
}

async function postOST(path, payload) {
  try {
    ost = await request(path, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload || {})
    });
    renderOST();
  } catch (error) {
    message(error.message, true);
    await refreshOST();
  }
}

document.getElementById('ost-upload').addEventListener('change', async event => {
  const file = event.target.files[0];
  if (!file) return;
  if (!/\.mp3$/i.test(file.name) || file.size > 100 * 1024 * 1024) {
    message('Selecciona un MP3 de hasta 100 MB.', true);
    event.target.value = '';
    return;
  }
  try {
    message(`Subiendo ${file.name} a OST…`);
    await request(`/api/ost/upload?filename=${encodeURIComponent(file.name)}`,
                  {method: 'POST', body: file});
    await refreshOST();
    message(`${file.name} añadido a OST.`);
  } catch (error) { message(error.message, true); }
  event.target.value = '';
});

for (const [id, path] of Object.entries({
  'ost-previous': '/api/ost/previous', 'ost-play': '/api/ost/play',
  'ost-pause': '/api/ost/pause', 'ost-next': '/api/ost/next'
})) {
  document.getElementById(id).addEventListener('click', () => postOST(path));
}
document.getElementById('ost-shuffle').addEventListener('click', () => {
  if (ost) postOST('/api/ost/shuffle', {shuffle: !ost.shuffle});
});
for (const select of [ostLeftElement, ostRightElement]) {
  select.addEventListener('change', () => {
    const channels = [ostLeftElement.value, ostRightElement.value];
    if (channels[0] === channels[1]) {
      message('Elige dos altavoces distintos para el estéreo.', true);
      renderOST();
      return;
    }
    postOST('/api/ost/channels', {channels});
  });
}

async function refresh() {
  try {
    await refreshPlaybacks();
    await refreshOST();
    const health = await request('/api/health');
    alsaLogElement.textContent = health.alsa_log?.join('\n') || 'Sin actividad todavía.';
    ffmpegLogElement.textContent = health.ffmpeg_log?.join('\n') || 'Sin actividad todavía.';
    audioMetricsElement.textContent = `Retrasos: ${health.late_blocks ?? 0} · Sin datos de FFmpeg: ${health.decode_starvations ?? 0} · Cortes ALSA: ${health.alsa_underruns ?? 0} · Mezcla máx.: ${(health.max_mix_ms ?? 0).toFixed(1)} ms · Escritura máx.: ${(health.max_write_ms ?? 0).toFixed(1)} ms`;
    audioStatusElement.classList.toggle('error', Boolean(health.last_error));
    if (health.last_error) {
      audioStatusElement.textContent = health.last_error;
    } else if (health.last_signal_at) {
      audioStatusElement.textContent = `Señal enviada a ${health.device} a las ${new Date(health.last_signal_at * 1000).toLocaleTimeString()}.`;
    } else {
      audioStatusElement.textContent = `Esperando señal para ${health.device}.`;
    }
    if (health.last_error && health.last_error !== lastAudioError) {
      message(health.last_error, true);
    }
    lastAudioError = health.last_error;
  } catch (error) { message(error.message, true); }
}

refreshFiles().then(refresh).catch(error => message(error.message, true));
setInterval(refresh, 1500);
