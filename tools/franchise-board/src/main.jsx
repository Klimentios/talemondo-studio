import { stageFranchisePacket } from '../../../preview/h3-director/franchise-handoff.mjs';
import { promptsFromIdea } from '../../../preview/h3-director/franchise-packet.mjs';
import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Drawnix } from '@drawnix/drawnix';
import { DrawTransforms, BasicShapes, createDefaultGeometry, insertElement } from '@plait/draw';
import { getSelectedElements, Transforms, BoardTransforms } from '@plait/core';
import ProductView from './product-view.jsx';
import './style.css';

let franchiseOrigin = '';
async function api(path, options = {}) {
  if (path === '/api/franchises' && location.port === '18187' && !franchiseOrigin) {
    const health = await (await fetch('/api/health')).json();
    if (!health.franchises_v1) franchiseOrigin = 'http://127.0.0.1:18208';
  }
  if (path.startsWith('/api/franchises') && franchiseOrigin) path = franchiseOrigin + path;
  let response;
  for (let attempt = 0; attempt < 3; attempt++) {
    try { response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...options.headers } }); break; }
    catch (error) {
      if ((options.method && options.method !== 'GET') || attempt === 2) throw error;
      await new Promise(resolve => setTimeout(resolve, 200 * (attempt + 1)));
    }
  }
  const body = await response.json();
  if (!response.ok) throw Object.assign(new Error(Array.isArray(body.detail) ? body.detail.map(item => item.msg).join('; ') : body.detail || `Ошибка ${response.status}`), { status: response.status });
  return body;
}
const TYPES = { character: 'Персонаж', location: 'Локация', rule: 'Правило мира', idea: 'Идея', scene: 'Сцена' };
const entityBody = meta => Object.fromEntries(['type','title','body','state','asset_ids'].map(key => [key, meta[key]]));
const entityMeta = record => ({ ...entityBody(record), entity_id: record.id, entity_revision: record.revision });
// The hero's name as the bible titles it («NAME · description»): the owner of its face and costume pictures.
const heroName = record => record.title.split(' ·')[0].trim().slice(0, 80);
const empty = { children: [], viewport: { zoom: 1, origination: [0, 0] } };
function BoardIcon({ kind }) {
  return <svg className="board-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {kind === 'episode' ? <><rect x="3" y="3" width="14" height="14" rx="2"/><path d="M7 3v14M10 7h4M10 10h4M10 13h3"/></> : kind === 'world' ? <><circle cx="10" cy="10" r="7"/><path d="M3 10h14M10 3c-3 3-3 11 0 14M10 3c3 3 3 11 0 14"/></> : <><path d="M10 2.5l1.7 5.8L17.5 10l-5.8 1.7L10 17.5l-1.7-5.8L2.5 10l5.8-1.7L10 2.5Z"/></>}
  </svg>;
}
function download(value, name) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function App() {
  const [franchises, setFranchises] = useState([]), [fid, setFid] = useState('');
  const [boards, setBoards] = useState([]), [doc, setDoc] = useState(null);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [form, setForm] = useState(null);
  const [cardBusy, setCardBusy] = useState(false), [showArchive, setShowArchive] = useState(false), [boardQuery, setBoardQuery] = useState('');
  // A fresh Studio has no franchises and a new franchise no boards: say so instead of «loading» forever.
  const [loadedFranchises, setLoadedFranchises] = useState(false), [loadedBoards, setLoadedBoards] = useState('');
  const locked = busy || cardBusy;
  const flush = useRef(async () => true);
  useEffect(() => { api('/api/franchises').then(rows => { setFranchises(rows); setLoadedFranchises(true); let previous; try { previous = localStorage.getItem('studio.franchise.current'); } catch {} setFid(rows.find(row => row.id === previous)?.id || rows[0]?.id || ''); }).catch(e => setError(e.message)); }, []);
  useEffect(() => {
    if (!fid) return;
    let alive = true; setDoc(null); setBoards([]); setBoardQuery(''); setShowArchive(false);
    api(`/api/franchises/${fid}/boards`).then(async rows => {
      if (!alive) return; setBoards(rows); setLoadedBoards(fid);
      let previous; try { previous = localStorage.getItem('studio.franchise.board.' + fid); } catch {}
      const chosen = rows.find(row => row.id === previous && !row.archived) || rows.find(row => !row.archived) || rows[0];
      if (chosen) { const loaded = await api(`/api/franchises/${fid}/boards/${chosen.id}`); if (alive) { setDoc(loaded); setShowArchive(Boolean(loaded.archived)); } }
    }).catch(e => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, [fid]);
  useEffect(() => { if (fid && doc && doc.franchise_id === fid) { try { localStorage.setItem('studio.franchise.current', fid); localStorage.setItem('studio.franchise.board.' + fid, doc.id); } catch {} } }, [fid, doc?.id]);
  async function navigate(action) {
    setBusy(true); setError('');
    try { if (await flush.current()) await action(); } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  async function submit(event) {
    event.preventDefault(); const data = new FormData(event.target);
    await navigate(async () => {
      if (form === 'rename-franchise') {
        await api(`/api/franchises/${fid}`, { method: 'PATCH', body: JSON.stringify({name:data.get('name')}) });
        setFranchises(await api('/api/franchises'));
      } else if (form === 'franchise') {
        let locale = 'ru'; try { locale = localStorage.getItem('h3-studio-locale') || (document.documentElement.lang === 'en' ? 'en' : 'ru'); } catch {}
        const row = await api('/api/franchises', { method: 'POST', body: JSON.stringify({ name: data.get('name'), locale: locale === 'en' ? 'en' : 'ru' }) });
        setFranchises(await api('/api/franchises')); setFid(row.id);
      } else {
        const row = await api(`/api/franchises/${fid}/boards`, { method: 'POST', body: JSON.stringify({ name: data.get('name'), kind: data.get('kind') }) });
        setShowArchive(false); setBoardQuery(''); setBoards(await api(`/api/franchises/${fid}/boards`)); setDoc(await api(`/api/franchises/${fid}/boards/${row.id}`));
      }
      setForm(null);
    });
  }
  async function boardAction(action) {
    if (!doc) return;
    await navigate(async () => {
      const path = `/api/franchises/${fid}/boards/${doc.id}`;
      if (action === 'duplicate') {
        const row = await api(path + '/duplicate', {method:'POST'});
        setShowArchive(false); setBoardQuery(''); setBoards(await api(`/api/franchises/${fid}/boards`)); setDoc(await api(`/api/franchises/${fid}/boards/${row.id}`));
      } else if (action === 'archive') {
        const archived = !doc.archived;
        await api(path + '/state', {method:'PATCH',body:JSON.stringify({archived})});
        const rows = await api(`/api/franchises/${fid}/boards`); setBoards(rows);
        const next = archived ? rows.find(row => !row.archived) : rows.find(row => row.id === doc.id);
        setDoc(next ? await api(`/api/franchises/${fid}/boards/${next.id}`) : null);
        setShowArchive(!next); setBoardQuery('');
      } else if (action === 'previous') {
        const result = await api(path + '/previous');
        download({version:1,name:doc.name,franchise_id:fid,content:result.content}, 'studio-board-previous.json');
      }
    });
  }
  return <div className="franchise-app">
    <header className="franchise-header"><div><span className="eyebrow">TALEMONDO · ТВОРЧЕСКОЕ ПРОСТРАНСТВО</span><h1>Франшизы</h1></div><a href="/">Открыть Studio ↗</a></header>
    <div className="franchise-tabs">{franchises.map(f => <button disabled={locked} className={fid === f.id ? 'active' : ''} key={f.id} onClick={() => navigate(() => setFid(f.id))}>{f.name}</button>)}<button disabled={locked} onClick={() => setForm('franchise')}>＋ Франшиза</button><button disabled={locked || !fid} aria-label="Переименовать франшизу" onClick={() => setForm('rename-franchise')}>✎</button></div>
    {error && <p role="alert" className="error">{error}</p>}
    <main className="franchise-workspace"><nav className="board-list" aria-label="Доски франшизы"><span className="eyebrow">ДОСКИ</span><input aria-label="Поиск досок" placeholder="Найти доску…" value={boardQuery} onChange={e => setBoardQuery(e.target.value)} />{boards.filter(b => Boolean(b.archived) === showArchive && b.name.toLowerCase().includes(boardQuery.toLowerCase())).map(b => <button key={b.id} title={b.name} disabled={locked} className={doc?.id === b.id ? 'active' : ''} onClick={() => navigate(async () => setDoc(await api(`/api/franchises/${fid}/boards/${b.id}`)))}><BoardIcon kind={b.kind}/><span className="board-list-name">{b.name}</span></button>)}<button disabled={locked || !fid} onClick={() => setForm('board')}>＋ Добавить доску</button><button aria-pressed={showArchive} onClick={() => setShowArchive(value => !value)}>{showArchive ? 'Активные доски' : 'Архив досок'}</button><p>Мир, персонажи и идеи каждой франшизы. Эпизоды — на собственных досках.</p></nav>
      {doc && doc.franchise_id === fid ? <BoardEditor key={doc.id} doc={doc} franchiseName={franchises.find(f => f.id === fid)?.name || ''} flush={flush} busy={locked} onBoardAction={boardAction} onEntityBusy={setCardBusy} onRename={name => setBoards(rows => rows.map(b => b.id === doc.id ? { ...b, name } : b))} /> : <div className="loading">{error ? 'Не удалось открыть доску'
        : loadedFranchises && !franchises.length ? <div className="board-empty"><p>Франшиз пока нет. Создай первую: мир, персонажи и правила будут жить на её досках.</p><button disabled={locked} onClick={() => setForm('franchise')}>＋ Франшиза</button></div>
        : loadedBoards === fid && !boards.length ? <div className="board-empty"><p>У этой франшизы ещё нет досок.</p><button disabled={locked} onClick={() => setForm('board')}>＋ Добавить доску</button></div>
        : boards.length && boards.every(b => b.archived) ? 'Все доски в архиве. Открой архив и верни нужную доску.' : 'Загрузка досок…'}</div>}
    </main>
    {form && <div className="modal-backdrop"><form className="modal" onSubmit={submit}><h2>{form === 'franchise' ? 'Новая франшиза' : form === 'rename-franchise' ? 'Название франшизы' : 'Новая доска'}</h2>{error && <p className="error" role="alert">{error}</p>}<label>Название<input name="name" required maxLength={160} defaultValue={form === 'rename-franchise' ? franchises.find(f => f.id === fid)?.name : ''} autoFocus /></label>{form === 'board' && <label>Тип<select name="kind"><option value="episode">Эпизод</option><option value="ideas">Идеи</option><option value="world">Мир и персонажи</option></select></label>}<div><button type="button" disabled={locked} onClick={() => setForm(null)}>Отмена</button><button disabled={locked} className="accent">{form === 'rename-franchise' ? 'Сохранить' : 'Создать'}</button></div></form></div>}
  </div>;
}
function BoardEditor({ doc, franchiseName, flush, busy, onRename, onEntityBusy, onBoardAction }) {
  const [value, setValue] = useState(doc.content), [name, setName] = useState(doc.name);
  const [mode, setMode] = useState('overview');
  const [status, setStatus] = useState('Сохранено на Mac'), [fault, setFault] = useState('');
  const [selected, setLocalSelected] = useState(null), [draft, setLocalDraft] = useState(null);
  const inspector = useRef({ nodeId: null, draft: null, dirty: false }), entityRequest = useRef(null), importing = useRef(false);
  function setSelected(nodeId) { inspector.current.nodeId = nodeId; setLocalSelected(nodeId); }
  function setDraft(meta) { inspector.current.draft = meta; inspector.current.dirty = false; setLocalDraft(meta); }
  function editDraft(meta) { inspector.current.draft = meta; inspector.current.dirty = true; setLocalDraft(meta); setStatus('Карточка не сохранена'); }

  const [handoff, setHandoff] = useState(null), [contextIds, setContextIds] = useState([]), [handoffError, setHandoffError] = useState('');
  const [records, setRecords] = useState(null), [recordQuery, setRecordQuery] = useState(''), [entityBusy, setLocalEntityBusy] = useState(false);
  function setEntityBusy(value) { const next = value || importing.current || librarySaving.current; setLocalEntityBusy(next); onEntityBusy(next); }
  const [expanded, setExpanded] = useState(false); const fullHost = useRef(null); const importInput = useRef(null);
  const selectionToken = useRef(0);
  const [assets, setAssets] = useState(null), [query, setQuery] = useState(''), [assetLimit, setAssetLimit] = useState(80);
  const [catalog, setCatalog] = useState([]), [assetKind, setAssetKind] = useState('all');
  const [picked, setPicked] = useState([]), [libraryError, setLibraryError] = useState(''), [libraryLoading, setLibraryLoading] = useState(false);
  const libraryTarget = useRef(null), libraryDialog = useRef(null), librarySession = useRef(0), librarySaving = useRef(false);
  function closeLibrary() { if (librarySaving.current || entityRequest.current) return; ++librarySession.current; setAssets(null); }
  useEffect(() => { api('/api/assets').then(setCatalog).catch(() => {}); }, []);
  useEffect(() => {
    if (assets === null) return;
    const previous = document.activeElement;
    libraryDialog.current?.querySelector('input')?.focus();
    const keys = event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeLibrary(); }
      if (event.key === 'Tab') {
        const items = [...(libraryDialog.current?.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),a[href]') || [])].filter(item => item.getClientRects().length);
        const first = items[0], last = items.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener('keydown', keys, true);
    return () => { document.removeEventListener('keydown', keys, true); previous?.focus(); };
  }, [assets === null]);
  const board = useRef(null), latest = useRef(doc.content), revision = useRef(doc.revision), title = useRef(doc.name);
  const timer = useRef(null), dirty = useRef(false), saving = useRef(null), stopped = useRef(false);
  const base = `/api/franchises/${doc.franchise_id}/boards/${doc.id}`;
  async function save() {
    clearTimeout(timer.current);
    if (!(await commitDraft())) return false;
    if (stopped.current) return false;
    if (saving.current) { try { await saving.current; } catch { return false; } return save(); }
    if (!dirty.current) return true;
    const snapshot = latest.current, nextName = title.current; dirty.current = false; setStatus('Сохраняется…');
    const operation = api(base, { method: 'PUT', body: JSON.stringify({ content: snapshot, revision: revision.current, name: nextName }) });
    saving.current = operation;
    try { const result = await operation; revision.current = result.revision; setStatus('Сохранено на Mac'); setFault(''); }
    catch (e) { dirty.current = true; stopped.current = e.status === 409; setStatus('Не сохранено'); setFault(e.message); return false; }
    finally { saving.current = null; }
    return dirty.current ? save() : true;
  }
  function change(data) { const next = { children: data.children, viewport: data.viewport, theme: data.theme }; latest.current = next; dirty.current = true; setValue(next); setStatus('Есть изменения'); clearTimeout(timer.current); timer.current = setTimeout(save, 800); }
  useEffect(() => {
    flush.current = save;
    const warn = e => { if (dirty.current || saving.current || inspector.current.dirty || entityRequest.current || importing.current || librarySaving.current) { e.preventDefault(); e.returnValue = ''; } };
    window.addEventListener('beforeunload', warn);
    return () => { board.current = null; ++selectionToken.current; clearTimeout(timer.current); window.removeEventListener('beforeunload', warn); flush.current = async () => true; };
  }, []);
  useEffect(() => {
    const changed = () => setExpanded(document.fullscreenElement === fullHost.current);
    const escape = event => { if (event.key === 'Escape' && !document.fullscreenElement) setExpanded(false); };
    document.addEventListener('fullscreenchange', changed); window.addEventListener('keydown', escape);
    return () => { document.removeEventListener('fullscreenchange', changed); window.removeEventListener('keydown', escape); };
  }, []);
  useEffect(() => { requestAnimationFrame(() => { if (board.current) BoardTransforms.fitViewport(board.current); }); }, [expanded]);
  async function toggleFullscreen() {
    if (expanded) {
      if (document.fullscreenElement === fullHost.current) await document.exitFullscreen();
      else setExpanded(false);
    } else {
      try { if (!fullHost.current.requestFullscreen) throw new Error(); await fullHost.current.requestFullscreen(); }
      catch { setExpanded(true); }
    }
  }
  async function select(override = null) {
    const token = ++selectionToken.current;
    const target = board.current;
    const node = override?.studio ? override : target && getSelectedElements(target)[0];
    if (!(await commitDraft()) || token !== selectionToken.current || board.current !== target) return;
    const meta = node?.studio;
    setSelected(meta ? node.id : null);
    if (!meta) { setDraft(null); return; }
    if (meta.entity_id) {
      setDraft(null);
      try {
        const record = await api(`/api/franchises/${doc.franchise_id}/entities/${meta.entity_id}`);
        if (token === selectionToken.current && board.current === target) setDraft(entityMeta(record));
      } catch (e) { if (token === selectionToken.current) setFault(e.message); }
    } else {
      const nativeTitle = node?.text?.children?.map(child => child.text || '').join('');
      setDraft({ ...meta, title: nativeTitle || meta.title });
    }
  }
  function clickedCard(event) {
    const id = event.target.closest?.('[plait-data-id]')?.getAttribute('plait-data-id');
    return board.current?.children.find(node => node.id === id && node.studio);
  }
  function pointerSelection(event) {
    if (event.target.closest?.('.app-toolbar,.draw-toolbar,.zoom-toolbar,.theme-toolbar,.pencil-mode-toolbar,[role=dialog]')) return;
    const hit = clickedCard(event);
    requestAnimationFrame(() => {
      if (!board.current) return;
      const node = hit || getSelectedElements(board.current)[0];
      if ((node?.studio ? node.id : null) !== inspector.current.nodeId) select(node);
    });
  }
  async function openRecords() {
    try { setRecords(await api(`/api/franchises/${doc.franchise_id}/entities`)); } catch (e) { setFault(e.message); }
  }
  async function addCard(type, record = null) {
    if (!(await commitDraft())) return;
    const b = board.current; if (!b || entityBusy) return;
    setEntityBusy(true);
    try {
      if (!record) record = await api(`/api/franchises/${doc.franchise_id}/entities`, { method: 'POST', body: JSON.stringify({ type, title: TYPES[type] }) });
      if (board.current !== b) return;
    const origin = b.viewport?.origination || [0, 0];
    let x = origin[0] + 120, y = origin[1] + 120;
    for (let attempt = 0; attempt < 3000; attempt++) {
      const overlaps = b.children.some(n => {
        if (!n.points?.length) return false;
        const xs = n.points.map(p => p[0]), ys = n.points.map(p => p[1]);
        return x < Math.max(...xs) + 20 && x + 280 > Math.min(...xs) - 20 && y < Math.max(...ys) + 20 && y + 160 > Math.min(...ys) - 20;
      });
      if (!overlaps) break;
      x = origin[0] + 120 + ((attempt + 1) % 3) * 320;
      y = origin[1] + 120 + Math.floor((attempt + 1) / 3) * 200;
    }
    const node = createDefaultGeometry(b, [[x, y], [x + 280, y + 160]], BasicShapes.rectangle);
    const meta = entityMeta(record);
    Object.assign(node, { studio: meta, text: { ...node.text, children: [{ text: meta.title }] }, fill: '#f0ddc5', strokeColor: '#9b714e' });
    insertElement(b, node);
    ++selectionToken.current; setSelected(node.id); setDraft(meta); setRecords(null);
    } catch (e) { setFault(e.message); } finally { setEntityBusy(false); }
  }
  async function persistEntity(meta) {
    if (meta.entity_id) return entityMeta(await api(`/api/franchises/${doc.franchise_id}/entities/${meta.entity_id}`, {
      method: 'PUT', body: JSON.stringify({ ...entityBody(meta), revision: meta.entity_revision })
    }));
    return entityMeta(await api(`/api/franchises/${doc.franchise_id}/entities`, { method: 'POST', body: JSON.stringify(entityBody(meta)) }));
  }
  function applyMeta(meta, nodeId) {
    const b = board.current;
    b.children.forEach((node, index) => {
      if (node.id === nodeId || (meta.entity_id && node.studio?.entity_id === meta.entity_id))
        Transforms.setNode(b, { studio: meta, text: { ...node.text, children: [{ text: meta.title }] } }, [index]);
    });
  }
  async function commitDraft() {
    if (entityRequest.current) { try { await entityRequest.current; } catch { return false; } }
    const current = inspector.current;
    if (!current.dirty || !current.draft || !current.nodeId) return true;
    const target = board.current, nodeId = current.nodeId, snapshot = current.draft, token = selectionToken.current;
    if (!target) return false; // The record can be saved even when its board instance was deleted.
    setEntityBusy(true);
    const request = persistEntity(snapshot); entityRequest.current = request;
    try {
      const meta = await request;
      if (target !== board.current) return false;
      applyMeta(meta, nodeId); setFault('');
      if (inspector.current.nodeId === nodeId && inspector.current.draft === snapshot) {
        inspector.current.draft = meta; inspector.current.dirty = false;
        if (token === selectionToken.current) setLocalDraft(meta);
      }
      return true;
    } catch (e) { setFault(e.message); return false; }
    finally { entityRequest.current = null; setEntityBusy(false); }
  }
  async function updateCard(event) { event.preventDefault(); if (await commitDraft()) await save(); }
  async function openLibrary(mode = 'canvas') {
    const session = ++librarySession.current, targetBoard = board.current;
    libraryTarget.current = mode === 'attach' ? { board: board.current, nodeId: inspector.current.nodeId, token: selectionToken.current } : { board: board.current };
    setPicked(mode === 'attach' ? [...(inspector.current.draft?.asset_ids || [])] : []);
    setQuery(''); setAssetKind(mode === 'canvas' ? 'image' : 'all'); setAssetLimit(80); setLibraryError(''); setAssets([]); setLibraryLoading(true);
    try { const rows = await api('/api/assets'); if (session === librarySession.current && targetBoard === board.current) { setCatalog(rows); setAssets(rows); } }
    catch (e) { if (session === librarySession.current && targetBoard === board.current) setLibraryError(e.message); }
    finally { if (session === librarySession.current && targetBoard === board.current) setLibraryLoading(false); }
  }
  async function applyMaterials() {
    if (entityBusy || libraryLoading || librarySaving.current) return;
    const target = libraryTarget.current, session = librarySession.current;
    setLibraryError('');
    if (target.nodeId && !inspector.current.draft?.title.trim()) { setLibraryError('Укажи название карточки перед сохранением материалов.'); return; }
    if (target.nodeId && picked.length > 100) { setLibraryError('К карточке можно прикрепить до 100 материалов. Сними лишние отметки.'); return; }
    librarySaving.current = true;
    if (target.nodeId && !(await commitDraft())) { librarySaving.current = false; setEntityBusy(false); setLibraryError('Не удалось сохранить карточку. Проверь название и сообщение об ошибке на доске; выбор материалов сохранён в этом окне.'); return; }
    setEntityBusy(true);
    try {
      if (target.board !== board.current) throw new Error('Доска изменилась. Открой библиотеку заново.');
      if (target.nodeId) {
        const current = inspector.current;
        if (current.nodeId !== target.nodeId || selectionToken.current !== target.token) throw new Error('Карточка изменилась. Открой её материалы заново.');
        if (!current.draft.title.trim()) throw new Error('Укажи название карточки перед сохранением материалов.');
        const meta = await persistEntity({ ...current.draft, asset_ids: picked });
        if (session !== librarySession.current || target.board !== board.current) return;
        if (inspector.current.nodeId !== target.nodeId || selectionToken.current !== target.token) { setLibraryError('Материалы сохранены в карточке. Открой её заново, чтобы увидеть результат.'); return; }
        applyMeta(meta, target.nodeId); setDraft(meta);
      } else {
        for (const id of picked) {
          const asset = assets.find(a => a.asset_id === id);
          if (!asset || asset.kind !== 'image') continue;
          const url = `/api/assets/${encodeURIComponent(id)}/file`;
          const img = new Image(); img.src = url;
          await img.decode();
          if (session !== librarySession.current || target.board !== board.current) return;
          DrawTransforms.insertImage(target.board, { url, width: 260, height: 260 * img.naturalHeight / img.naturalWidth });
          // Successfully inserted images are removed from the pending selection, so a retry cannot duplicate them.
          setPicked(ids => ids.filter(value => value !== id));
        }
      }
      setAssets(null); setFault('');
    } catch (e) { setLibraryError(e.message); }
    finally { librarySaving.current = false; setEntityBusy(false); }
  }
  const visibleAssets = (assets || []).filter(a => (assetKind === 'all' || a.kind === assetKind) && (a.original_name || '').toLowerCase().includes(query.toLowerCase()));
  async function openHandoff() {
    if (!(await save())) return;
    const scene = inspector.current.draft;
    if (!scene || !['scene','idea'].includes(scene.type) || !scene.body.trim()) { setFault('Сначала заполни описание действия в карточке.'); return; }
    if (scene.type === 'idea') { try { promptsFromIdea(scene.body); } catch(e) { setFault(e.message); return; } }
    const unique = new Map();
    for (const node of board.current.children) if (node.studio && node.studio.type !== 'scene') unique.set(node.studio.entity_id || node.id, node.studio);
    setHandoff({scene: structuredClone(scene), context: [...unique].map(([id,meta])=>({id,...meta}))});
    setContextIds([]); setHandoffError('');
  }
  async function transferHandoff() {
    setEntityBusy(true); setHandoffError('');
    try {
      const rows = await api('/api/assets');
      if (handoff.scene.type === 'idea') {
        const shots = promptsFromIdea(handoff.scene.body);
        const entities = await api(`/api/franchises/${doc.franchise_id}/entities`);
        const characters = entities.filter(row => row.type === 'character' && row.asset_ids.length >= 2);
        if (!characters.length) throw new Error('В библии франшизы нет героя с отдельными фото лица и костюма.');
        const references = characters.flatMap((character, index) => character.asset_ids.slice(0,2).map((id,position) => {
          const asset = rows.find(row => row.asset_id === id);
          if (!asset || asset.kind !== 'image') throw new Error('Фото героя отсутствует в библиотеке: '+id);
          return {id,kind:'image',number:index*2+position+1,name:asset.original_name,
            role:position ? 'Костюм' : 'Лицо героя',owner:heroName(character),ownerCandidates:[heroName(character)],manualOwner:true};
        }));
        const packet={version:1,id:crypto.randomUUID(),franchise_id:doc.franchise_id,board_id:doc.id,
          scene_id:handoff.scene.entity_id,title:handoff.scene.title,prompt:shots[0].prompt,references,shots};
        location.assign(stageFranchisePacket(packet)); return;
      }
      const materials = new Map();
      const context = handoff.context.filter(meta=>contextIds.includes(meta.id));
      for (const meta of [handoff.scene, ...context]) for (const id of meta.asset_ids) {
        const asset = rows.find(asset=>asset.asset_id===id);
        if (!asset) throw new Error('Материал отсутствует в библиотеке. Отвяжи его или восстанови файл: '+id);
        if (!materials.has(id)) materials.set(id,{id,kind:asset.kind,name:asset.original_name,role:'',owner:'',ownerCandidates:[],manualOwner:true});
        const ref=materials.get(id);if(meta.type==='character'&&!ref.ownerCandidates.includes(meta.title))ref.ownerCandidates.push(meta.title);
      }
      const counts={image:0,video:0,audio:0};
      const references=[...materials.values()].map(ref=>({...ref,number:++counts[ref.kind],owner:ref.ownerCandidates.length===1?ref.ownerCandidates[0].slice(0,80):''}));
      const prompt=[handoff.scene.body,...context.map(meta=>`${TYPES[meta.type]} «${meta.title}»: ${meta.body}`), references.length ? 'Материалы:\n'+references.map(ref=>`<${ref.kind==='image'?'Picture':ref.kind==='video'?'Video':'Audio'} ${ref.number}> — ${ref.name}${ref.owner?' · '+ref.owner:''}`).join('\n'): ''].filter(Boolean).join('\n\n');
      const packet={version:1,id:crypto.randomUUID(),franchise_id:doc.franchise_id,board_id:doc.id,scene_id:handoff.scene.entity_id,title:handoff.scene.title,prompt,references};
      const url=stageFranchisePacket(packet);
      location.assign(url);
    } catch(e) { setHandoffError(e.message); }
    finally { setEntityBusy(false); }
  }
  function exportBoard() {
    const current = inspector.current;
    const content = current.dirty && current.draft ? { ...latest.current, children: latest.current.children.map(node =>
      node.id === current.nodeId ? { ...node, studio: current.draft, text: { ...node.text, children: [{ text: current.draft.title }] } } : node)
    } : latest.current;
    download({ version: 1, name, franchise_id: doc.franchise_id, content }, 'studio-board.json');
  }
  async function importFile(event) {
    const file = event.target.files[0]; event.target.value = ''; if (!file) return;
    const target = board.current; importing.current = true; setEntityBusy(true);
    try {
      const imported = JSON.parse(await file.text());
      if (imported.version !== 1 || !Array.isArray(imported.content?.children) || file.size > 8 * 1024 * 1024) throw new Error('Неверный экспорт доски STUDIO');
      // Imported cards become independent copies, so another franchise's IDs cannot alter its canon.
      imported.content.children = imported.content.children.map(node => {
        if (!node.studio?.entity_id) return node;
        const { entity_id, entity_revision, ...meta } = node.studio; return { ...node, studio: entityBody(meta) };
      });
      if (!confirm('Импорт создаёт независимые копии карточек и заменит содержимое текущей доски. Сначала экспортируй её, если нужна отдельная копия. Продолжить?')) return;
      if (!(await save())) return;
      const next = await api(base, { method: 'PUT', body: JSON.stringify({ content: imported.content, revision: revision.current, name: title.current }) });
      if (board.current !== target) return;
      revision.current = next.revision; latest.current = imported.content; dirty.current = false; location.reload();
    } catch (e) { setFault(e.message); } finally { importing.current = false; setEntityBusy(false); }
  }
  async function openOverview() {
    if (!(await save())) return;
    board.current = null;
    setSelected(null); setDraft(null); setMode('overview');
  }
  return <section ref={fullHost} className={`board-content${expanded ? ' is-expanded' : ''}`}>
    <div className="board-heading"><input disabled={busy || entityBusy} aria-label="Название доски" value={name} maxLength={160} onChange={e => { setName(e.target.value); title.current = e.target.value; onRename(e.target.value); change(latest.current); }} /><span role="status">{status}</span><div className="board-mode" role="group" aria-label="Режим доски"><button className={mode === 'overview' ? 'active' : ''} aria-pressed={mode === 'overview'} onClick={openOverview}>Обзор</button><button className={mode === 'canvas' ? 'active' : ''} aria-pressed={mode === 'canvas'} onClick={() => setMode('canvas')}>Холст</button></div>{mode === 'canvas' && <button aria-pressed={expanded} title="Развернуть доску; Esc — свернуть" onClick={toggleFullscreen}>{expanded ? 'Свернуть' : 'На весь экран'}</button>}<button disabled={busy || entityBusy} onClick={save}>Сохранить</button><button onClick={exportBoard}>Экспорт</button><button disabled={busy || entityBusy} type="button" onClick={() => importInput.current?.click()}>Импорт</button><select aria-label="Действия с доской" value="" disabled={busy} onChange={e => { if(e.target.value) onBoardAction(e.target.value); }}><option value="">Ещё…</option><option value="duplicate">Копировать доску</option><option value="archive">{doc.archived ? 'Вернуть из архива' : 'В архив'}</option><option value="previous">Скачать предыдущее сохранение</option></select><input ref={importInput} type="file" accept=".json" hidden onChange={importFile} /></div>
    {fault && <div role="alert" className="error">{fault}{inspector.current.dirty && <button onClick={async () => { download({ draft: inspector.current.draft }, 'studio-card-draft.json'); inspector.current.dirty = false; setFault(''); await select(); }}>Сохранить текст и перечитать</button>}{stopped.current && <button onClick={() => location.reload()}>Перезагрузить после экспорта</button>}</div>}
    {mode === 'overview' ? <ProductView content={value} catalog={catalog} franchise={franchiseName} boardName={name} onEdit={() => setMode('canvas')} /> : <><div className="card-tools">{Object.entries(TYPES).map(([type, label]) => <button disabled={busy || entityBusy || stopped.current} key={type} onClick={() => addCard(type)}>＋ {label}</button>)}<button disabled={busy || entityBusy || stopped.current} onClick={openRecords}>▤ Картотека</button><button disabled={busy || entityBusy || stopped.current} onClick={() => openLibrary('canvas')}>◩ Изображения на доску</button></div>
    <div className="canvas-layout" inert={importing.current || busy}><div className="canvas-wrap" onPointerUp={pointerSelection} onDoubleClickCapture={event => { const node = clickedCard(event); if (node) { event.preventDefault(); event.stopPropagation(); select(node); } }}><Drawnix value={value.children || empty.children} viewport={value.viewport} theme={value.theme} initialLanguage="ru" onChange={change} afterInit={b => { board.current = b; }} onSelectionChange={() => { const node = board.current && getSelectedElements(board.current)[0]; if (node?.studio && node.id !== inspector.current.nodeId) select(node); }} /></div>
    {selected && draft && <aside className="card-inspector"><form onSubmit={updateCard}><div className="inspector-heading"><strong>{TYPES[draft.type]}</strong><button type="button" onClick={async () => { if (await commitDraft()) setSelected(null); }} aria-label="Закрыть карточку">×</button></div><label>Название<input disabled={entityBusy} value={draft.title} maxLength={160} required onChange={e => editDraft({ ...draft, title: e.target.value })} /></label><label>Описание<textarea disabled={entityBusy} value={draft.body} rows={8} onChange={e => editDraft({ ...draft, body: e.target.value })} /></label><label>Статус<select disabled={entityBusy} value={draft.state} onChange={e => editDraft({ ...draft, state: e.target.value })}><option value="draft">Черновик</option><option value="approved">Утверждено</option></select></label><button className="accent" disabled={entityBusy || stopped.current}>{draft.entity_id ? 'Применить ко всем доскам' : 'Сохранить в картотеку'}</button></form>{['scene','idea'].includes(draft.type) && <button disabled={busy || entityBusy} onClick={openHandoff}>Подготовить в Studio ↗</button>}<h3>Материалы</h3><div className="attached-materials">{draft.asset_ids.map(id => { const asset = catalog.find(a => a.asset_id === id); return <div className="attached-material" key={id}><a target="_blank" rel="noreferrer" href={`/api/assets/${encodeURIComponent(id)}/file`}>{asset?.kind === 'image' && <img src={`/api/assets/${encodeURIComponent(id)}/thumbnail`} alt="" loading="lazy" />}<span>{asset?.original_name || 'Материал недоступен в библиотеке'} ↗</span></a><button type="button" disabled={entityBusy} aria-label={`Отвязать ${asset?.original_name || 'материал'}`} onClick={() => editDraft({ ...draft, asset_ids: draft.asset_ids.filter(value => value !== id) })}>×</button></div>; })}</div><button disabled={entityBusy} onClick={() => openLibrary('attach')}>Выбрать материалы ({draft.asset_ids.length})</button><p>Запись картотеки общая для досок этой франшизы. Удаление карточки с холста сохраняет запись. Двойной клик открывает описание.</p></aside>}
    </div></>}
    {records && <div className="modal-backdrop"><section className="modal library"><div className="inspector-heading"><h2>Картотека франшизы</h2><button onClick={() => setRecords(null)}>Закрыть</button></div><input aria-label="Поиск в картотеке" placeholder="Персонаж, локация, идея…" value={recordQuery} onChange={e => setRecordQuery(e.target.value)} /><p>Выбери запись, чтобы разместить её на текущей доске.</p><div className="record-list">{records.filter(r => (r.title + ' ' + r.body).toLowerCase().includes(recordQuery.toLowerCase())).map(r => <button key={r.id} disabled={entityBusy} onClick={() => addCard(r.type, r)}><strong>{r.title}</strong><span>{TYPES[r.type]} · {r.state === 'approved' ? 'Утверждено' : 'Черновик'}</span><small>{r.body.slice(0,140)}</small></button>)}</div>{!records.length && <p>Добавь персонажа или идею кнопкой над доской — запись появится здесь.</p>}</section></div>}
    {handoff && <div className="modal-backdrop"><section role="dialog" aria-modal="true" aria-label="Подготовка сцены" className="modal library"><div className="inspector-heading"><h2>{handoff.scene.title}</h2><button disabled={entityBusy} onClick={()=>setHandoff(null)}>Закрыть</button></div><p>Выбери персонажей, локации и правила для этой сцены. В Studio проверишь описание и роли материалов перед добавлением.</p><div className="handoff-context">{handoff.context.map(meta=><label key={meta.id}><input type="checkbox" disabled={entityBusy} checked={contextIds.includes(meta.id)} onChange={()=>setContextIds(ids=>ids.includes(meta.id)?ids.filter(id=>id!==meta.id):[...ids,meta.id])} /><span><strong>{meta.title}</strong><small>{TYPES[meta.type]} · {meta.asset_ids.length} материалов</small></span></label>)}</div>{handoffError && <p role="alert" className="error">{handoffError}</p>}<button disabled={entityBusy} className="accent" onClick={transferHandoff}>{entityBusy ? 'Подготавливается…' : 'Перейти к проверке в Studio'}</button></section></div>}
    {assets !== null && <div className="modal-backdrop"><section ref={libraryDialog} role="dialog" aria-modal="true" aria-labelledby="library-title" className="modal library material-picker"><div className="inspector-heading"><h2 id="library-title">{libraryTarget.current?.nodeId ? 'Материалы карточки' : 'Изображения на доску'}</h2><button disabled={entityBusy} onClick={closeLibrary}>Закрыть</button></div><p>{libraryTarget.current?.nodeId ? 'Отметь материалы и примени выбор. Оригиналы останутся в библиотеке.' : 'Отметь изображения для размещения на холсте.'}</p><div className="library-filters"><input aria-label="Поиск материалов" placeholder="Поиск по названию…" value={query} onChange={e => { setQuery(e.target.value); setAssetLimit(80); }} /><select aria-label="Тип материалов" value={assetKind} disabled={!libraryTarget.current?.nodeId} onChange={e => { setAssetKind(e.target.value); setAssetLimit(80); }}><option value="all">Все типы</option><option value="image">Изображения</option><option value="video">Видео</option><option value="audio">Аудио</option></select></div>{libraryError && <p className="error" role="alert">{libraryError}</p>}{libraryLoading ? <p role="status">Загрузка библиотеки…</p> : <><div className="asset-grid">{visibleAssets.slice(0, assetLimit).map(a => <button key={a.asset_id} disabled={entityBusy} aria-pressed={picked.includes(a.asset_id)} onClick={() => setPicked(ids => ids.includes(a.asset_id) ? ids.filter(id => id !== a.asset_id) : [...ids, a.asset_id])}>{a.kind === 'image' ? <img loading="lazy" decoding="async" src={`/api/assets/${encodeURIComponent(a.asset_id)}/thumbnail`} alt="" /> : <span className="media-kind">{a.kind === 'video' ? '▶ Видео' : '♫ Аудио'}</span>}<span>{picked.includes(a.asset_id) ? '✓ ' : ''}{a.original_name}</span></button>)}</div>{visibleAssets.length > assetLimit && <button onClick={() => setAssetLimit(n => n + 80)}>Показать ещё ({visibleAssets.length - assetLimit})</button>}{!visibleAssets.length && <p>{assets.length ? 'Ничего не найдено. Измени поиск или тип материалов.' : 'Библиотека пуста. Загрузи материалы в разделе «Библиотека».'}</p>}</>}<div className="library-actions"><span role="status">Выбрано: {picked.length}</span><button disabled={entityBusy || !picked.length} onClick={() => setPicked([])}>Снять выбор</button><button className="accent" disabled={entityBusy || libraryLoading || (!libraryTarget.current?.nodeId && !picked.length)} onClick={applyMaterials}>{entityBusy ? 'Сохраняется…' : libraryTarget.current?.nodeId ? 'Применить выбор' : 'Добавить на доску'}</button></div></section></div>}

  </section>;
}
createRoot(document.getElementById('root')).render(<App />);
