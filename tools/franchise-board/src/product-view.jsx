import React, { useEffect, useState } from 'react';
import './product-view.css';

const LABELS = { character: 'Персонаж', location: 'Локация', rule: 'Правило', idea: 'Идея', scene: 'Сцена' };
const assetUrl = id => `/api/assets/${encodeURIComponent(id)}/file`;
const assetIdFromNode = node => node.url?.match(/\/api\/assets\/([^/]+)\/file/)?.[1];

function summary(body = '') {
  const lines = body.split('\n').map(line => line.trim()).filter(line => line.length > 42 && !/^ЧАСТЬ\s|^\d+(?:\.\d+)?\./.test(line));
  const first = lines.find(line => /^[А-ЯЁA-Z][\p{L}-]+\s+[—–]/u.test(line)) || lines.find(line => /[.!?…]$/.test(line)) || lines[0] || body.trim();
  return first.length > 210 ? `${first.slice(0, 207).trimEnd()}…` : first;
}

export default function ProductView({ content, catalog, franchise, boardName, onEdit }) {
  const [open, setOpen] = useState(null);
  const records = [...new Map((content?.children || []).filter(node => node.studio?.entity_id)
    .map(node => [node.studio.entity_id, node.studio])).values()];
  const byId = new Map(catalog.map(asset => [asset.asset_id, asset]));
  const characters = records.filter(record => record.type === 'character');
  const otherRecords = records.filter(record => record.type !== 'character');
  const imageIds = [...new Set((content?.children || []).filter(node => node.type === 'image')
    .map(assetIdFromNode).filter(Boolean))];
  const visualCount = new Set([...imageIds, ...records.flatMap(record => record.asset_ids || [])
    .filter(id => byId.get(id)?.kind === 'image')]).size;
  const attached = new Set(characters.flatMap(record => record.asset_ids || []));
  const gallery = imageIds.filter(id => !attached.has(id)).map(id => byId.get(id)).filter(Boolean);
  const heroAssets = characters.map(record => byId.get(record.asset_ids?.[0])).filter(Boolean).slice(0, 3);
  const rich = characters.length || gallery.length || otherRecords.length;

  useEffect(() => {
    if (!open) return;
    const onKey = event => { if (event.key === 'Escape') setOpen(null); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  return <div className="product-view">
    <section className="product-hero" aria-label="Обзор франшизы">
      <div className="product-hero-copy">
        <span className="product-kicker">TALEMONDO · ФРАНШИЗА</span>
        <h2>{franchise}</h2>
        <p>{boardName}</p>
        <div className="product-hero-meta"><span>{characters.length ? `${characters.length} героя` : `${otherRecords.length} разделов`}</span><span>{visualCount} визуальных материалов</span></div>
      </div>
      {heroAssets.length > 0 && <div className="product-hero-cast" aria-hidden="true">{heroAssets.map(asset =>
        <img key={asset.asset_id} src={assetUrl(asset.asset_id)} alt="" loading="eager" />)}</div>}
      {!heroAssets.length && gallery[0] && <img className="product-hero-scene" src={assetUrl(gallery[0].asset_id)} alt="" />}
      {!heroAssets.length && !gallery.length && <div className="product-hero-mark" aria-hidden="true">{String(otherRecords.length).padStart(2, '0')}</div>}
    </section>

    {characters.length > 0 && <section className="product-section" aria-labelledby="cast-heading">
      <header className="product-section-heading"><div><span className="product-kicker">ВНЕШНОСТЬ И ХАРАКТЕР</span><h3 id="cast-heading">Герои</h3></div><p>Референсы привязаны к персонажам и остаются в библиотеке Studio.</p></header>
      <div className="product-cast-grid">{characters.map(record => {
        const media = (record.asset_ids || []).map(id => byId.get(id)).filter(asset => asset?.kind === 'image');
        return <article className="product-character" key={record.entity_id}>
          <div className="product-character-media">{media.length ? media.slice(0, 3).map((asset, index) =>
            <img key={asset.asset_id} className={index ? 'product-supporting-image' : ''} src={assetUrl(asset.asset_id)} alt={`${record.title}: референс ${index + 1}`} loading="lazy" />) : <span>Нет изображения</span>}</div>
          <div className="product-character-copy"><span className="product-type">{LABELS[record.type]} · {record.state === 'approved' ? 'утверждено' : 'рабочая версия'}</span><h4>{record.title}</h4><p>{summary(record.body)}</p><button onClick={() => setOpen(record)}>Открыть персонажа <span aria-hidden="true">↗</span></button></div>
        </article>;
      })}</div>
    </section>}

    {gallery.length > 0 && <section className="product-section" aria-labelledby="visuals-heading">
      <header className="product-section-heading"><div><span className="product-kicker">КАДРЫ И АТМОСФЕРА</span><h3 id="visuals-heading">Визуальные ориентиры</h3></div><p>Сцены задают настроение и действие. Точную внешность сверяйте с карточками героев.</p></header>
      <div className="product-gallery">{gallery.map((asset, index) => <a key={asset.asset_id} href={assetUrl(asset.asset_id)} target="_blank" rel="noreferrer" className={index === 0 ? 'product-gallery-feature' : ''}><img src={assetUrl(asset.asset_id)} alt={asset.original_name} loading="lazy" /><span><b>{String(index + 1).padStart(2, '0')}</b>{asset.original_name}<span aria-hidden="true">↗</span></span></a>)}</div>
    </section>}

    {otherRecords.length > 0 && <section className="product-section" aria-labelledby="notes-heading">
      <header className="product-section-heading"><div><span className="product-kicker">СМЫСЛ И ПРАВИЛА</span><h3 id="notes-heading">{characters.length || gallery.length ? 'Материалы доски' : 'Разделы библии'}</h3></div><p>Выберите раздел, чтобы прочитать полный текст.</p></header>
      <div className="product-record-grid">{otherRecords.map((record, index) => <button className="product-record" key={record.entity_id} onClick={() => setOpen(record)}><span className="product-record-top"><span>{LABELS[record.type]}</span><b>{String(index + 1).padStart(2, '0')}</b></span><strong>{record.title}</strong><span className="product-record-summary">{summary(record.body)}</span><span className="product-record-open">Читать полностью ↗</span></button>)}</div>
    </section>}

    {!rich && <section className="product-empty"><h3>Пока здесь пусто</h3><p>Добавьте персонажа, правило или изображение на холсте — здесь появится удобный обзор.</p><button onClick={onEdit}>Открыть холст</button></section>}

    {open && <div className="product-reader-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) setOpen(null); }}><section className="product-reader" role="dialog" aria-modal="true" aria-labelledby="product-reader-title"><header><div><span className="product-kicker">{LABELS[open.type]} · {open.state === 'approved' ? 'утверждено' : 'рабочая версия'}</span><h2 id="product-reader-title">{open.title}</h2></div><button aria-label="Закрыть" onClick={() => setOpen(null)}>×</button></header><div className="product-reader-scroll"><p>{open.body}</p>{open.asset_ids?.length > 0 && <div className="product-reader-assets">{open.asset_ids.map(id => { const asset = byId.get(id); return asset && <a key={id} href={assetUrl(id)} target="_blank" rel="noreferrer">{asset.kind === 'image' && <img src={`/api/assets/${encodeURIComponent(id)}/thumbnail`} alt="" loading="lazy" />}<span>{asset.original_name} ↗</span></a>; })}</div>}</div><footer><button onClick={() => setOpen(null)}>Закрыть</button><button className="accent" onClick={() => { setOpen(null); onEdit(); }}>Открыть холст для правки</button></footer></section></div>}
  </div>;
}
