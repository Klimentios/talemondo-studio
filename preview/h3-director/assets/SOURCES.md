# Director library assets

The five camera photographs under `cameras/` come from Wikimedia Commons. Each
image's author, license, and source link are also displayed on its preset card.
The files here are downscaled thumbnails; redistribution must preserve the
attribution and CC BY-SA 4.0 terms.

| Local file | Author | Commons source | Technical source |
| --- | --- | --- | --- |
| `cameras/alexa-35.jpg` | Rémi Simonnin | [Alexa 35 (3)](https://commons.wikimedia.org/wiki/File:Alexa_35_(3).jpg) | [ARRI ALEXA 35](https://www.arri.com/en/cine-systems/cine-cameras/legacy-cine-cameras/alexa-35) |
| `cameras/sony-fx6.jpg` | Hidalgo944 | [Sony Cinema line FX6](https://commons.wikimedia.org/wiki/File:Sony_Cinema_line_FX6.jpg) | [Sony FX6 specifications](https://www.sony.com/electronics/support/camcorders-interchangeable-lens-camcorders/ilme-fx6v/specifications) |
| `cameras/ursa-cine-12k-lf.jpg` | TurnOnTheNight | [Blackmagic URSA Cine 12K LF](https://commons.wikimedia.org/wiki/File:Blackmagic_URSA_Cine_12K_LF_20250726122053.jpg) | [Blackmagic technical specifications](https://www.blackmagicdesign.com/products/blackmagicursacine/techspecs) |
| `cameras/sony-burano.jpg` | TurnOnTheNight | [Sony CineAlta BURANO](https://commons.wikimedia.org/wiki/File:Sony_CineAlta_BURANO_with_Sony_FE_24-70mm_F2.8_GM_II_20250726120012.jpg) | [Sony BURANO specifications](https://pro.sony/ue_US/products/digital-cinema-cameras/burano) |
| `cameras/canon-c400.jpg` | TaurusEmerald | [Canon EOS C400](https://commons.wikimedia.org/wiki/File:Canon_EOS_C400_Camera.jpg) | [Canon EOS C400 specifications](https://www.usa.canon.com/shop/p/eos-c400) |

`director/cinematic-atlas.png` is an original illustrative visual study for
the Studio. It is not a camera test, measured lens comparison, LUT, motion
capture, or a representation of a guaranteed H3 result.

`shot-focus/*.jpg` are cropped from an original AI-generated contact sheet made
for this Studio on 2026-10-03. The first sheet illustrates eight framing
choices and six focus choices with different subjects and locations. They are visual examples,
not measurements of focal length, depth of field, camera optics, or expected H3
output. No third-party film stills or stock photos were used for these cards.

The nine additional `shot-two.jpg`, `shot-pov.jpg`, `shot-insert.jpg`,
`shot-overhead.jpg`, `shot-dutch.jpg`, `focus-foreground.jpg`,
`focus-background.jpg`, `focus-foreground-to-subject.jpg`, and
`focus-background-to-subject.jpg` were cropped from a second original
AI-generated 3×3 cinematography sheet on 2026-10-03. Shot/focus terminology was
cross-checked against the [ASC Grammar of the Shot](https://store.ascmag.com/products/grammar-of-the-shot-fifth-edition)
overview, ASC's [point-of-view interview](https://staging.ascmag.com/articles/creative-perspectives-for-shot-1),
and its [Dutch-angle case study](https://staging.ascmag.com/articles/twelve-monkeys-dystopian-trip-through-time).
The stills illustrate editorial intent. A still cannot demonstrate the timing
of a rack-focus transition; the structured `from`/`to` choices and Director
instruction carry that intent to H3.

`lenses/lens-*.jpg` are original AI-generated illustrative product images made
for this Studio on 2026-10-03. They depict generic unbranded lenses, not the
physical dimensions or optical behavior of a particular manufacturer's lens.

The color swatches in the code are original design choices rather than sampled
frames or measured color profiles. The ten extended style recipes are original
art-direction descriptions; they do not reproduce a named filmmaker's work.

`lighting/*.jpg` are twelve distinct original AI-generated illustrative stills
made for this Studio on 2026-10-03. They illustrate lighting direction and
contrast rather than reproducing a film frame or promising an exact H3 render.
The lighting recipes synthesize general production techniques from the
[ARRI Lighting Handbook](https://www.arri.com/en/learn-help/lighting/lighting-handbook)
(key, soft fill, and backlight), the [ARRI SkyPanel X application guide](https://www.arri.com/en/lighting/led-panel-lights/skypanel-x/applications)
(soft versus hard light), and ARRI's account of [window keys, negative fill and
practicals](https://www.arri.com/news-en/shooting-and-grading-for-hdr-with-arri-alexa-35/314098-314098).
The Rembrandt and frontal portrait entries are original descriptions of
conventional portrait lighting geometry, not claims that H3 uses a physical
light rig. Their structured fields are validated Director inputs; currently
H3 receives the lighting direction as prompt conditioning.
