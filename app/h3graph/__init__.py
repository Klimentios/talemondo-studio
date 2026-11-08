"""H3 graph modules (P0 of the H3 Studio engine, journal item 45).

app/graph.py::build_prompt assembles one Ref2VA job from these parts:
    core         weights, sampler, decode, MP4
    references   image/video/audio loaders and their dense <Picture N>/<Video N>/<Audio N> slots
    camera       the 3D camera move (words, or an opt-in guide video)
    guides       continuation tail and picture keyframes on the conditioning
    prompt       the text H3 reads (official IR or legacy prompt + option sentences, LoRA triggers)
    model_chain  FirstBlockCache and style/character LoRAs
    post         colour match, grade, film look
Node ids are named in nodes.Node; the numbers themselves are a contract and do not change.
"""
