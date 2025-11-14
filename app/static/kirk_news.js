const el = sel => document.querySelector(sel);
const feedsList = el('#feeds-list');
const feedInput = el('#feed-input');
const addFeedBtn = el('#add-feed');
const saveFeedsBtn = el('#save-feeds');
const refreshBtn = el('#refresh');
const bridgeMode = el('#bridge-mode');
const maxItems = el('#max-items');
const stories = el('#stories');

let feeds = [];

async function apiGetFeeds() {
  const r = await fetch('/dunsel/api/news/feeds');
  const j = await r.json();
  feeds = j.feeds || [];
  renderFeeds();
}

function renderFeeds() {
  feedsList.innerHTML = '';
  feeds.forEach((f,i) => {
    const li = document.createElement('li');
    const left = document.createElement('div');
    left.textContent = f;
    const rm = document.createElement('button');
    rm.textContent = 'REMOVE';
    rm.onclick = () => { feeds.splice(i,1); renderFeeds(); };
    li.appendChild(left); li.appendChild(rm);
    feedsList.appendChild(li);
  });
}

addFeedBtn.onclick = () => {
  const v = (feedInput.value || '').trim();
  if (!v) return;
  if (!feeds.includes(v)) feeds.push(v);
  feedInput.value = '';
  renderFeeds();
};

saveFeedsBtn.onclick = async () => {
  const r = await fetch('/dunsel/api/news/feeds', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({feeds})
  });
  const j = await r.json();
  if (!j.ok) {
    alert(j.error || 'Failed to save');
  } else {
    feeds = j.feeds || feeds;
    renderFeeds();
  }
};

refreshBtn.onclick = async () => {
  stories.innerHTML = '<div class="story"><div class="idx">…</div><div>Scanning…</div></div>';
  const body = {
    feeds,
    max_items: Math.max(1, Math.min(12, parseInt(maxItems.value || '6',10))),
    bridge_mode: !!bridgeMode.checked
  };
  const r = await fetch('/dunsel/api/news/opine', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body)
  });
  const j = await r.json();
  renderStories(j.items || []);
};

function renderStories(items){
  stories.innerHTML = '';
  if (!items.length){
    stories.innerHTML = '<div class="story"><div class="idx">–</div><div>No fresh headlines.</div></div>';
    return;
  }
  items.forEach(it=>{
    const row = document.createElement('div');
    row.className = 'story';
    const idx = document.createElement('div');
    idx.className = 'idx';
    idx.textContent = it.idx;
    const content = document.createElement('div');

    const h = document.createElement('div');
    h.className = 'headline';
    if (it.link){
      const a = document.createElement('a');
      a.href = it.link; a.target = '_blank'; a.rel = 'noopener';
      a.textContent = it.title;
      h.appendChild(a);
    } else {
      h.textContent = it.title;
    }

    const s = document.createElement('div');
    s.className = 'source';
    s.textContent = it.source;

    const o = document.createElement('div');
    o.className = 'opinion';
    o.textContent = it.opinion || '';

    content.appendChild(h);
    content.appendChild(s);
    content.appendChild(o);

    row.appendChild(idx);
    row.appendChild(content);
    stories.appendChild(row);
  });
}

// boot
apiGetFeeds().then(()=>refreshBtn.click());
