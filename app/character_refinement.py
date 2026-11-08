"""Strict whole-character refinement contract; reference indexes are dense and one based."""
import math


def normalize_character_refinement(value, image_count=None):
    if value is None or value == {}:
        return None
    if not isinstance(value, dict) or set(value) - {'characters', 'width', 'height'}:
        raise ValueError('Неизвестные параметры уточнения героя')
    width, height = value.get('width', 768), value.get('height', 768)
    if any(type(x) is not int or x < 512 or x > 1536 or x % 32 for x in (width, height)):
        raise ValueError('Размер уточнения: 512–1536, кратно 32')
    chars = value.get('characters')
    if not isinstance(chars, list) or not 1 <= len(chars) <= 4:
        raise ValueError('Уточнение поддерживает 1–4 отдельных героев')
    clean, names, refs = [], set(), set()
    for item in chars:
        if not isinstance(item, dict) or set(item) - {'name', 'picture', 'box'}:
            raise ValueError('Для героя нужны имя, референс и область целиком')
        name, picture, box = item.get('name'), item.get('picture'), item.get('box')
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or '\n' in name:
            raise ValueError('Некорректное имя героя')
        if type(picture) is not int or picture < 1 or (image_count is not None and picture > image_count):
            raise ValueError('Референс героя отсутствует в задании')
        if name.casefold() in names or picture in refs:
            raise ValueError('Каждому герою нужен отдельный референс и имя')
        if not isinstance(box, list) or len(box) != 4 or any(type(x) not in (int, float) or not math.isfinite(x) for x in box):
            raise ValueError('Область героя: x, y, ширина, высота от 0 до 1')
        x, y, w, h = box
        if not (0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0 and x+w <= 1 and y+h <= 1):
            raise ValueError('Область героя выходит за кадр')
        names.add(name.casefold()); refs.add(picture)
        clean.append({'name': name.strip(), 'picture': picture, 'box': list(box)})
    return {'characters': clean, 'width': width, 'height': height}


def build_character_refinement(request, references, source_video, config):
    from .graph import build_prompt, _node
    config = normalize_character_refinement(config, len(references))
    if request.profile != 'bf16':
        raise ValueError('Уточнение героя требует полную BF16 модель')
    # Reuse native loaders only; each pass has its own image, crop, latent and seed.
    base = build_prompt(request, references, cache_mode='off')
    graph = {k: base[k] for k in ('92', '119', '120', '127', '128', '130')}
    graph['500'] = _node('LoadVideo', 'Исходный готовый ролик', file=source_video)
    graph['501'] = _node('GetVideoComponents', 'Исходные кадры и звук', video=['500', 0])
    graph['502'] = _node('H3CharacterTrack', 'Отдельные треки героев целиком',
                         images=['501', 0], characters_json=__import__('json').dumps(config['characters']))
    output = ['501', 0]
    for i, character in enumerate(config['characters']):
        ids = [str(510 + i*10 + j) for j in range(8)]
        load, crop, cond, noise, sched, guider, sample, decode = ids
        graph[load] = _node('LoadImage', f"Референс {character['name']}", image=references[character['picture']-1])
        graph[crop] = _node('H3CharacterCrop', f"Область {character['name']}", images=['501', 0], tracks=['502', 0],
                           character=i, width=config['width'], height=config['height'])
        prompt = (f"<Picture 1> defines the sole character {character['name']}, including exact face, fur or skin, "
                  "body proportions, complete costume, accessories and photographic or illustrated rendering style. "
                  "<Video 1> supplies only this character's motion, pose, expressions and scene lighting. "
                  "Restore the complete character from <Picture 1>; preserve the action and timing of <Video 1>. Keep the same letterboxed composition and padding as <Video 1>. "
                  "Do not introduce another character, alter the camera, or remove clothing. No new dialogue.")
        graph[cond] = _node('MiniMaxH3ReferenceToVideo', f"H3 уточнение {character['name']}",
                           prompt=prompt, width=config['width'], height=config['height'], length=[crop, 1],
                           ref_image_size='max', clip=['128', 0], vae=['119', 0], audio_vae=['120', 0],
                           **{'ref_images.ref_image_0':[load, 0], 'ref_videos.ref_video_0':[crop, 0]})
        graph[noise] = _node('RandomNoise', 'Seed уточнения', noise_seed=((request.seed or 0)+i) % 2**64)
        graph[sched] = _node('BasicScheduler', 'Полные шаги уточнения', model=['127', 0], scheduler='simple', steps=request.steps, denoise=1.0)
        graph[guider] = _node('BasicGuider', 'Гид уточнения', model=['127', 0], conditioning=[cond, 0])
        graph[sample] = _node('SamplerCustomAdvanced', 'Отдельный проход H3', noise=[noise, 0], guider=[guider, 0],
                              sampler=['503', 0], sigmas=[sched, 0], latent_image=[cond, 1])
        graph[decode] = _node('VAEDecode', 'Уточнённые кадры героя', samples=[sample, 0], vae=['119', 0])
        composite = str(560+i)
        graph[composite] = _node('H3CharacterComposite', 'Вернуть героя в исходный кадр',
                                images=output, refined=[decode, 0], tracks=['502', 0], character=i, feather=0.04)
        output = [composite, 0]
    graph['503'] = _node('KSamplerSelect', 'Сэмплер уточнения', sampler_name='res_multistep')
    graph['130']['inputs'].update(images=output, audio=['501', 1], fps=24.0)
    return graph


def validate_refinement_video(item, expected_frames):
    """Keep original video/audio bytes: reject incompatible timing instead of transcoding."""
    import json
    import subprocess
    import tempfile
    from fractions import Fraction
    from pathlib import Path
    with tempfile.TemporaryDirectory(prefix='h3-refine-source-') as directory:
        path = Path(directory)/'source.mp4'
        path.write_bytes(item[1])
        try:
            result = subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-count_frames',
                                     '-show_entries','stream=avg_frame_rate,nb_read_frames,width,height',
                                     '-of','json',str(path)],capture_output=True,text=True,timeout=120,check=True)
            stream = json.loads(result.stdout)['streams'][0]
            if Fraction(stream['avg_frame_rate']) != 24 or int(stream['nb_read_frames']) != expected_frames:
                raise ValueError('Уточнение сохраняет все кадры: нужен H3-ролик 24 fps с длительностью выбранного задания')
        except (OSError, subprocess.SubprocessError, KeyError, IndexError, ZeroDivisionError) as exc:
            raise ValueError('Не удалось проверить длительность исходного ролика') from exc
    return item
