// Render the actual bound assets, never inferred filenames or upload-order ownership.
export function referencePreviewItems(references=[]){
 return references.map(ref=>{
  const kind=ref.kind, id=String(ref.asset_id||'');
  if(!id||!['image','video','audio'].includes(kind))throw new Error('Референс не имеет файла или поддержанного типа.');
  return {id,kind,url:`/api/assets/${encodeURIComponent(id)}/file`,
   label:`${{image:'Фото',video:'Видео',audio:'Аудио'}[kind]} ${ref.number||''} · ${ref.role||'Роль не задана'}`,
   owner:ref.owner||'',performer:ref.performer||'',duration:ref.duration_seconds};
 });
}
export function referencePreview(references,{title='Референсы этого плана'}={}){
 if(!document.querySelector('link[data-reference-preview]')){
  const css=document.createElement('link');css.rel='stylesheet';css.href='./reference-preview.css?v=20261009-1';css.dataset.referencePreview='';document.head.append(css);
 }
 const section=document.createElement('section');section.className='reference-preview';section.setAttribute('aria-label',title);
 const heading=document.createElement('h4');heading.textContent=title;section.append(heading);
 const grid=document.createElement('div');grid.className='reference-preview-grid';section.append(grid);
 for(const item of referencePreviewItems(references)){
  const card=document.createElement('figure'),media=document.createElement(item.kind==='image'?'img':item.kind),caption=document.createElement('figcaption');
  if(item.kind==='image'){media.alt=item.label;media.loading='lazy';}
  else{media.controls=true;media.preload='metadata';if(item.kind==='video')media.playsInline=true;}
  media.src=item.url;
  caption.textContent=item.label+(item.owner?` · Герой: ${item.owner}`:'')+(item.performer?` · Исполнитель: ${item.performer}`:'');
  const problem=document.createElement('p');problem.hidden=true;problem.setAttribute('role','status');
  media.addEventListener('error',()=>{problem.hidden=false;problem.textContent='Файл не удалось открыть. Проверь его в библиотеке перед запуском.';});
  const open=document.createElement('a');open.href=item.url;open.target='_blank';open.rel='noopener';open.textContent='Открыть исходник';
  card.append(media,caption,open,problem);grid.append(card);
 }
 if(!references.length){const p=document.createElement('p');p.textContent='Референсы не выбраны.';section.append(p);}
 // Avoid audio/video continuing to play after removing or replacing the preview.
 section.addEventListener('play',event=>{for(const media of section.querySelectorAll('video,audio'))if(media!==event.target)media.pause();},true);
 return section;
}
