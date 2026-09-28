'use strict';
const $ = id => document.getElementById(id);
let token = '', selected = null, photo = null, fruitOffset = 0, scanOffset = 0;
let limit = 10 * 1024 * 1024, busy = false;
let previewURL = null;
let observationOffset = 0;
const urls = new Set();
const storageNames = {counter: 'On the counter', refrigerator: 'In the refrigerator', other: 'Stored elsewhere', unknown: 'Storage not recorded'};
function message(text = '') { $('message').textContent = text; }
function show(section) {
  for (const name of ['login', 'collection', 'detail']) $(name).hidden = name !== section;
}
function releaseImages() { for (const url of urls) URL.revokeObjectURL(url); urls.clear(); }
function lock(value) {
  busy = value;
  document.querySelectorAll('button, input, select, textarea').forEach(el => { el.disabled = value; });
  $('save-scan').disabled = value || !photo;
}
async function request(path, options = {}) {
  const headers = new Headers(options.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(path, {...options, headers, cache: 'no-store'});
  if (response.status === 401) {
    token = ''; selected = null; clearPhoto(); releaseImages(); $('history').replaceChildren(); $('fruit-list').replaceChildren();
    $('observations').replaceChildren(); $('observation-form').reset();
    $('disconnect').hidden = true; show('login');
    throw new Error('Enter a valid access token to connect.');
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status}). Please check your input.`);
  }
  return response;
}
async function run(action) {
  if (busy) return;
  lock(true); message();
  try { await action(); } catch (error) {
    message(error instanceof TypeError ? 'Connection lost. Refresh before trying to save again; your previous save may have succeeded.' : error.message);
  } finally { lock(false); }
}
function text(tag, value) { const el = document.createElement(tag); el.textContent = value; return el; }
async function loadFruit(reset = false) {
  const page = await (await request(`/fruit?limit=20&offset=${reset ? 0 : fruitOffset}`)).json();
  if (reset) { fruitOffset = 0; $('fruit-list').replaceChildren(); }
  for (const fruit of page.items) {
    const button = text('button', fruit.name || 'Banana'); button.className = 'fruit';
    button.append(text('small', `${fruit.scan_count} ${fruit.scan_count === 1 ? 'photo' : 'photos'} · ${storageNames[fruit.storage_method]}`));
    button.onclick = () => run(() => openFruit(fruit)); $('fruit-list').append(button);
  }
  fruitOffset += page.items.length; $('more-fruit').hidden = fruitOffset >= page.total;
  $('fruit-count').textContent = page.total ? String(page.total).padStart(2, '0') : '';
  if (!page.total) { const empty = text('p', 'A fresh start. Add your first banana.'); empty.className = 'empty'; $('fruit-list').append(empty); }
}
async function connect() {
  const capabilities = await (await request('/capabilities')).json(); limit = capabilities.max_upload_bytes;
  await loadFruit(true); show('collection'); $('disconnect').hidden = !token;
}
function clearPhoto() {
  clearPreview();
  photo = null; $('camera').value = ''; $('library').value = '';
  const now = new Date(); $('captured').value = new Date(now - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  $('chosen').textContent = `JPEG, PNG or WebP · up to ${Math.round(limit / 1024 / 1024)} MB. Convert HEIC first.`;
}
function clearPreview() {
  if (previewURL) URL.revokeObjectURL(previewURL);
  previewURL = null; $('preview-image').removeAttribute('src'); $('photo-preview').hidden = true;
}
async function openFruit(fruit) {
  selected = fruit; clearPhoto(); $('fruit-name').textContent = fruit.name || 'Banana';
  $('fruit-storage').textContent = storageNames[fruit.storage_method];
  $('observation-form').reset(); $('check-in').open = false; resetObservationTime();
  $('observations').replaceChildren(); $('more-observations').hidden = true;
  releaseImages(); $('history').replaceChildren(); show('detail'); await loadHistory(true); await loadObservations(true);
}
function resetObservationTime() {
  const now = new Date(); $('observed').value = new Date(now - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}
async function loadObservations(reset = false) {
  const page = await (await request(`/fruit/${selected.id}/observations?limit=10&offset=${reset ? 0 : observationOffset}`)).json();
  if (reset) { observationOffset = 0; $('observations').replaceChildren(); }
  const stages = {unripe:'Unripe', ripe:'Ripe', overripe:'Overripe', unsure:'Ripeness unsure'};
  const answers = {acceptable:'Would use', unacceptable:'Would not use', unsure:'Unsure about using'};
  for (const observation of page.items) {
    const row = document.createElement('article');
    row.append(text('h3', `Your check-in · ${new Date(observation.observed_at).toLocaleString()}`));
    row.append(text('p', `${stages[observation.ripeness]} · ${answers[observation.acceptability]} for ${observation.intended_use === 'eat_fresh' ? 'eating fresh' : 'cooking or baking'}`));
    if (observation.notes) row.append(text('p', observation.notes));
    $('observations').append(row);
  }
  observationOffset += page.items.length; $('more-observations').hidden = observationOffset >= page.total;
}
$('observation-form').onsubmit = event => {
  event.preventDefault(); run(async () => {
    await request(`/fruit/${selected.id}/observations`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
      observed_at:new Date($('observed').value).toISOString(), ripeness:$('ripeness').value,
      intended_use:$('intended-use').value, acceptability:$('acceptability').value,
      notes:$('observation-notes').value.trim() || null
    })});
    $('observation-form').reset(); resetObservationTime(); $('check-in').open = false;
    await loadObservations(true); message('Check-in saved.');
  });
};
$('more-observations').onclick = () => run(() => loadObservations());
async function loadHistory(reset = false) {
  const page = await (await request(`/fruit/${selected.id}/history?limit=10&offset=${reset ? 0 : scanOffset}`)).json();
  if (reset) { scanOffset = 0; releaseImages(); $('history').replaceChildren(); }
  for (const scan of page.items) {
    const card = document.createElement('article'); card.className = 'scan';
    card.append(text('h2', new Date(scan.captured_at).toLocaleString()));
    try {
      const blob = await (await request(scan.artifacts.image)).blob();
      const url = URL.createObjectURL(blob); urls.add(url);
      const img = document.createElement('img'); img.src = url; img.alt = 'Saved banana photo'; card.append(img);
    } catch (error) { if (!selected) throw error; card.append(text('p', 'Photo could not load. Reopen this banana to try again.')); }
    const needsNewPhoto = scan.analysis.status === 'insufficient_image';
    card.append(text('p', needsNewPhoto ? 'Could not isolate the banana. Try another photo with a plain background.' : 'Photo saved. This scan does not yet provide a ripeness or days-left estimate.'));
    if (scan.analysis.warnings.length && !needsNewPhoto) card.append(text('p', 'Image analysis flagged possible background or lighting issues. Try more even light and a plain background.'));
    $('history').append(card);
  }
  scanOffset += page.items.length; $('more-scans').hidden = scanOffset >= page.total;
  if (!page.total) $('history').append(text('p', 'Your first photo will appear here.'));
}
$('login-form').onsubmit = event => { event.preventDefault(); run(async () => { token = $('token').value.trim(); $('token').value = ''; await connect(); }); };
$('add-form').onsubmit = event => {
  event.preventDefault(); run(async () => {
    const fruit = await (await request('/fruit', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: $('name').value.trim(), storage_method: $('storage').value})})).json();
    $('name').value = ''; $('add-form').hidden = true; $('toggle-add').setAttribute('aria-expanded', 'false'); await openFruit(fruit);
  });
};
for (const id of ['camera', 'library']) $(id).onchange = () => {
  clearPreview();
  photo = $(id).files[0] || null; $(id === 'camera' ? 'library' : 'camera').value = ''; message();
  if (photo && photo.size > limit) { photo = null; message('That photo is too large. Choose a photo under the upload limit.'); }
  if (photo && !['image/jpeg', 'image/png', 'image/webp'].includes(photo.type)) { photo = null; message('Use JPEG, PNG or WebP. Convert HEIC photos before uploading.'); }
  $('chosen').textContent = photo ? photo.name : 'No photo selected.'; $('save-scan').disabled = !photo;
  if (photo) { previewURL = URL.createObjectURL(photo); $('preview-image').src = previewURL; $('photo-preview').hidden = false; }
};
$('remove-photo').onclick = () => { clearPhoto(); $('save-scan').disabled = true; };
$('toggle-add').onclick = () => {
  const open = $('add-form').hidden; $('add-form').hidden = !open;
  $('toggle-add').setAttribute('aria-expanded', String(open));
  if (open) $('name').focus();
};
$('scan-form').onsubmit = event => {
  event.preventDefault(); if (!photo) return;
  run(async () => {
    const form = new FormData(); form.append('file', photo);
    form.append('captured_at', new Date($('captured').value).toISOString());
    message('Saving your photo…');
    await request(`/fruit/${selected.id}/scan`, {method: 'POST', body: form});
    clearPhoto(); await loadHistory(true); message('Scan saved.');
  });
};
$('back').onclick = () => run(async () => { await loadFruit(true); selected = null; clearPhoto(); releaseImages(); $('history').replaceChildren(); show('collection'); });
$('more-fruit').onclick = () => run(() => loadFruit());
$('more-scans').onclick = () => run(() => loadHistory());
$('disconnect').onclick = () => { token = ''; selected = null; clearPhoto(); releaseImages(); $('history').replaceChildren(); $('fruit-list').replaceChildren(); $('observations').replaceChildren(); $('observation-form').reset(); $('disconnect').hidden = true; message(); show('login'); };
window.addEventListener('pagehide', () => { clearPreview(); releaseImages(); });
run(connect);
