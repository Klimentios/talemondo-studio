// Curated references are packaged with Studio for offline access.
// Locations have usable ground/floor, props are isolated on white, costumes have no wearer.
const stockLocations = [
  ['CLD6T4J9VZ','Гостиная с выходом в сад','Интерьер / дом','house-home'],
  ['HIH67XC5G0','Современная гостиная','Интерьер / дом','interior-design'],
  ['NF6P1OX124','Гостиная в кирпичном лофте','Интерьер / дом','house-interior'],
  ['AXOFGKXCSI','Осенняя лесная тропа','Природа / лес','forest-path'],
  ['PZCV4YNDYW','Лесной берег с тропой','Природа / лес','forest-path'],
  ['R0BLEAKC6P','Дощатая дорожка в лесу','Природа / лес','forest-path'],
  ['I5NUBCSVEP','Ночной городской переулок','Город / улица','street-alley'],
  ['5667T882YW','Промышленный переулок','Город / улица','alley-street'],
  ['Y98XKZNQ63','Пустынный пляж с вышкой','Природа / берег','beach-sunset'],
  ['3ZX53UFU8B','Светлая кухня с деревянным полом','Интерьер / дом','kitchen-interior'],
  ['HOHJK6B7TD','Кафе с посадкой','Интерьер / кафе','cafe-restaurant'],
  ['2LWU1SQCKK','Городская кофейня','Интерьер / кафе','places-restaurant'],
  ['T0BHAFEFFR','Лесной домик с площадкой перед ним','Природа / лес','log-cabin'],
  ['NSQTXCLWQ5','Офисный коридор','Интерьер / работа','office-interior'],
].map(([id,title,category,slug])=>({id,title,type:'locations',category,audience:'all',tone:'neutral',role:'Окружение',
  image:`./assets/stock/${id}.jpg`,source:`https://stocksnap.io/photo/${slug}-${id}`,origin:'StockSnap',license:'CC0'}));

const originalLocations = [
  ['location-tokyo','Токио · мокрая улица','Город / Япония'],
  ['location-marrakech','Марракеш · внутренний двор','Город / Марокко'],
  ['location-iceland','Исландия · чёрный берег','Природа / Исландия'],
  ['location-newyork','Нью-Йорк · платформа метро','Город / США'],
  ['location-venice','Венеция · набережная канала','Город / Италия'],
  ['location-sahara','Сахара · поселение у оазиса','Природа / пустыня'],
  ['location-alps','Альпы · деревенская площадь','Город / горы'],
  ['location-kyoto','Киото · храмовая дорожка','Город / Япония'],
].map(([id,title,category])=>({id,title,type:'locations',category,audience:'all',tone:'neutral',role:'Окружение',
  image:`./assets/stock/${id}.jpg`,source:'',origin:'STUDIO',license:'Оригинал, создан для STUDIO'}));

const originalProps = [
  ['prop-flashlight','Карманный фонарь','Техника'],
  ['prop-remote','Пульт управления','Техника'],
  ['prop-keys','Связка ключей','Быт'],
  ['prop-camera','Плёночная камера','Техника'],
  ['prop-phone','Винтажный телефон','Техника'],
  ['prop-watch','Наручные часы','Аксессуары'],
  ['prop-backpack','Походный рюкзак','Аксессуары'],
  ['prop-umbrella','Красный зонт','Аксессуары'],
  ['prop-typewriter','Красная печатная машинка','Техника'],
  ['prop-guitar','Акустическая гитара','Музыка'],
  ['prop-microphone','Студийный микрофон','Музыка'],
  ['prop-book','Открытая книга без текста','Быт'],
  ['prop-pistol','Пистолет в профиль · предметный референс','Реквизит / оружие'],
  ['prop-armchair','Кожаное кресло','Мебель'],
  ['prop-lamp','Настольная лампа','Интерьер'],
  ['prop-toy-mouse','Плюшевая мышь','Игрушки'],
  ['prop-suitcase','Кожаный чемодан','Быт'],
  ['prop-coffee-mug','Белая кружка','Быт'],
  ['prop-crown','Серебряная тиара','Аксессуары'],
  ['prop-sunglasses','Жёлтые солнцезащитные очки','Аксессуары'],
  ['prop-hammer','Молоток','Инструменты'],
].map(([id,title,category])=>({id,title,type:'props',category,audience:'all',tone:'neutral',role:'Предмет',
  image:`./assets/stock/${id}.jpg`,source:'',origin:'STUDIO',license:'Оригинал, создан для STUDIO'}));

const originalCostumes = [
  ['costume-cat-dinosaur','Кот · костюм динозавра','Животные / комедия','animals','funny'],
  ['costume-cat-astronaut','Кот · костюм астронавта','Животные / приключение','animals','serious'],
  ['costume-dog-raincoat','Собака · жёлтый дождевик','Животные / повседневный','animals','neutral'],
  ['costume-animal-bumblebee','Собака · костюм шмеля','Животные / комедия','animals','funny'],
  ['costume-animal-winter','Кот · зимняя куртка','Животные / мода','animals','serious'],
  ['costume-human-clown','Человек · театральный клоун','Люди / комедия','people','funny'],
  ['costume-human-space','Человек · экспедиционный костюм','Люди / фантастика','people','serious'],
  ['costume-human-formal','Человек · строгий костюм','Люди / классика','people','serious'],
  ['costume-human-evening','Человек · вечернее платье','Люди / мода','people','serious'],
  ['costume-human-streetwear','Человек · городской образ','Люди / мода','people','neutral'],
].map(([id,title,category,audience,tone])=>({id,title,type:'clothes',category,audience,tone,role:'Костюм',
  image:`./assets/stock/${id}.jpg`,source:'',origin:'STUDIO',license:'Оригинал, создан для STUDIO'}));

const styles = [
  ['style-noir','Чёрно-белый нуар','Кино / нуар'],
  ['style-watercolor','Акварельный город','Иллюстрация / акварель'],
  ['style-3d','Стилизованное 3D-кино','Анимация / 3D'],
  ['style-film35','Цветная киноплёнка 35 мм','Кино / плёнка'],
  ['style-film16','Документальная плёнка 16 мм','Кино / плёнка'],
  ['style-epic70','Широкоформатное кино 70 мм','Кино / плёнка'],
  ['style-anime','Рисованная анимация · cel','Анимация / 2D'],
  ['style-clay','Кукольная анимация · пластилин','Анимация / stop motion'],
  ['style-gouache','Рисованная гуашь','Иллюстрация / гуашь'],
  ['style-ink','Графический роман · тушь','Иллюстрация / тушь'],
  ['style-retro2d','Ретро-анимация · 2D','Анимация / 2D'],
].map(([id,title,category])=>({id,title,type:'styles',category,audience:'all',tone:'neutral',role:'Стиль',
  image:`./assets/stock/${id}.jpg`,source:'',origin:'STUDIO',license:'Оригинал, создан для STUDIO'}));

export const STOCK_CATALOG = Object.freeze([...stockLocations,...originalLocations,...originalProps,...originalCostumes,...styles]);

export function installStockLibrary({onImport, onUse, findImported, showToast}) {
  const grid=document.querySelector('#stockGrid');
  const search=document.querySelector('#stockSearch');
  const type=document.querySelector('#stockType');
  const audience=document.querySelector('#stockAudience');
  const count=document.querySelector('#stockCount');
  const tone=document.querySelector('#stockTone');
  function render() {
    const query=search.value.trim().toLocaleLowerCase('ru');
    const visible=STOCK_CATALOG.filter(item=>(type.value==='all'||type.value===item.type)
      &&(audience.value==='all'||item.audience===audience.value)
      &&(tone.value==='all'||item.tone===tone.value)
      &&(!query||`${item.title} ${item.category} ${item.origin}`.toLocaleLowerCase('ru').includes(query)));
    count.textContent=`${visible.length} из ${STOCK_CATALOG.length}`;
    grid.replaceChildren();
    for(const item of visible){
      const card=document.createElement('article');card.className='stock-card';
      const image=document.createElement('img');image.src=item.image;image.alt=item.title;image.loading='lazy';
      const body=document.createElement('div');body.className='stock-card-body';
      const title=document.createElement('strong');title.textContent=item.title;
      const category=document.createElement('small');category.textContent=item.category;
      const origin=document.createElement(item.source?'a':'span');origin.className='stock-origin';
      origin.textContent=`${item.origin} · ${item.license}${item.source?' ↗':''}`;
      origin.title=origin.textContent;
      if(item.source){origin.href=item.source;origin.target='_blank';origin.rel='noopener noreferrer';}
      const buttons=document.createElement('div');buttons.className='stock-actions';
      const save=document.createElement('button');save.type='button';save.textContent=findImported(item.id)?'Сохранено':'Сохранить';save.disabled=Boolean(findImported(item.id));
      const use=document.createElement('button');use.type='button';use.className='stock-use';use.textContent='В сцену';
      const act=async(callback,button)=>{button.disabled=true;button.textContent='Сохраняем…';try{await callback(item);render();}catch(error){showToast(`Не удалось добавить: ${error.message}`);button.disabled=false;button.textContent=button===use?'В сцену':'Повторить';}};
      save.addEventListener('click',()=>void act(onImport,save));
      use.addEventListener('click',()=>void act(onUse,use));
      buttons.append(save,use);body.append(title,category,origin,buttons);card.append(image,body);grid.append(card);
    }
    if(!visible.length){const empty=document.createElement('p');empty.className='library-empty';empty.textContent='В подборке ничего не найдено';grid.append(empty);}
  }
  search.addEventListener('input',render);type.addEventListener('change',render);audience.addEventListener('change',render);tone.addEventListener('change',render);
  return {render};
}
