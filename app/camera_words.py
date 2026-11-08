"""Camera movement in the official MiniMax H3 words (base guide §4.3: motion type + amplitude + speed).

Source: MiniMax-H3 "Video Prompt Writing Guide" (T2VA / I2VA / FL2VA / L2VA), §4.3 Camera Motion. The
trained vocabulary is: Zoom In/Out, Push In/Pull Out, Pan Left/Right, Truck Left/Right, Tilt Up/Down,
Pedestal Up/Down, Arc Shot, Tracking Shot, Static Shot, Shake Slightly/Strongly, POV,
Roll Clockwise/Counterclockwise; amplitude "with small amplitude" / "with large amplitude"; speed
"at slow speed" / "at fast speed". Medium amplitude and normal speed are omitted, as the guide says.
Movement is written as a natural English action: "The camera pushes in with small amplitude at slow speed."

Used by the scenarist (CAMERA line for Qwen) and by the A/B variant ``camera_words`` that replaces the
numeric camera text (compile_projected) of legacy prompts.
"""
from __future__ import annotations

# movement -> {direction: verb phrase}; "" is the direction-less form.
_MOVES: dict[str, dict[str, str]] = {
    "push": {"in": "pushes in", "": "pushes in"},
    "pull": {"out": "pulls out", "": "pulls out"},
    "zoom": {"in": "zooms in", "out": "zooms out"},
    "pan": {"left": "pans left", "right": "pans right"},
    "truck": {"left": "trucks left", "right": "trucks right"},
    "tilt": {"up": "tilts up", "down": "tilts down"},
    "pedestal": {"up": "pedestals up", "down": "pedestals down"},
    "arc": {"": "moves in an arc shot around the subject", "left": "arcs left around the subject",
            "right": "arcs right around the subject"},
    "tracking": {"": "follows the moving subject in a tracking shot"},
    "static": {"": "holds a static shot"},
    "shake": {"slight": "shakes slightly", "strong": "shakes strongly", "": "shakes slightly"},
    "roll": {"clockwise": "rolls clockwise", "counterclockwise": "rolls counterclockwise"},
    "pov": {"": "shows the subject's point of view in a POV shot"},
}
# Aliases Studio or a user may send; normalised before the lookup.
_ALIASES = {
    "push_in": ("push", "in"), "pull_out": ("pull", "out"), "dolly_in": ("push", "in"),
    "dolly_out": ("pull", "out"), "zoom_in": ("zoom", "in"), "zoom_out": ("zoom", "out"),
    "orbit": ("arc", ""), "crane": ("pedestal", ""), "follow": ("tracking", ""), "track": ("tracking", ""),
    "hold": ("static", ""), "locked": ("static", ""), "handheld": ("shake", "slight"),
}
# Amplitude and speed do not apply to a held frame, a shake (its strength is the direction) or a POV.
_NO_MODIFIERS = {"static", "shake", "pov"}
AMPLITUDES = {"small": "with small amplitude", "medium": "", "large": "with large amplitude", "": ""}
SPEEDS = {"slow": "at slow speed", "normal": "", "medium": "", "fast": "at fast speed", "": ""}
MOVEMENTS = tuple(_MOVES) + tuple(_ALIASES)


def vocabulary() -> dict:
    """The accepted camera words for clients (GET /api/capabilities ``camera_words_v1``)."""
    return {
        "movements": {move: list(directions) for move, directions in _MOVES.items()},
        "aliases": {alias: list(target) for alias, target in _ALIASES.items()},
        "speeds": ["slow", "normal", "fast"],
        "amplitudes": ["small", "medium", "large"],
        "no_modifiers": sorted(_NO_MODIFIERS),
    }


def camera_phrase(movement: str, direction: str = "", speed: str = "", amplitude: str = "") -> str:
    """One official-style sentence, e.g. ``The camera pans right with large amplitude at fast speed.``"""
    move = str(movement or "").strip().lower().replace(" ", "_").replace("-", "_")
    direction = str(direction or "").strip().lower().replace("-", "").replace(" ", "")
    if move in _ALIASES:
        move, implied = _ALIASES[move]
        direction = direction or implied
    if move not in _MOVES:
        raise ValueError(f"camera.movement: неизвестное движение «{movement}»; доступны: {', '.join(_MOVES)}")
    verbs = _MOVES[move]
    if direction not in verbs:
        options = ", ".join(sorted(key for key in verbs if key)) or "без направления"
        raise ValueError(f"camera.direction для «{move}»: {options}")
    speed = str(speed or "").strip().lower()
    amplitude = str(amplitude or "").strip().lower()
    if speed not in SPEEDS:
        raise ValueError("camera.speed: slow, normal или fast")
    if amplitude not in AMPLITUDES:
        raise ValueError("camera.amplitude: small, medium или large")
    parts = [f"The camera {verbs[direction]}"]
    if move not in _NO_MODIFIERS:
        parts += [text for text in (AMPLITUDES[amplitude], SPEEDS[speed]) if text]
    return " ".join(parts) + "."


def _amplitude(value: float, small: float, large: float) -> str:
    return "small" if value < small else "large" if value > large else "medium"


def camera_phrase_from_state(camera_state: dict | None, frames: int, fps: int = 24) -> str:
    """Studio trajectory (camera_state keys) → official camera words, first key to last key.

    distance → push in / pull out, orbit → arc, elevation → pedestal, fov at a fixed distance → zoom.
    Several components become several sentences. Speed: change per second relative to the clip.
    """
    keys = (camera_state or {}).get("keys") or []
    if len(keys) < 2:
        return camera_phrase("static")
    first, last = keys[0], keys[-1]
    seconds = max(1e-3, float(last.get("t", (frames - 1) / fps)) - float(first.get("t", 0.0)))

    def speed(rate: float, slow: float, fast: float) -> str:
        return "slow" if rate < slow else "fast" if rate > fast else "normal"

    sentences = []
    d0, d1 = float(first.get("distance", 4.5)), float(last.get("distance", 4.5))
    ratio = d1 / d0 if d0 > 0 else 1.0
    if abs(ratio - 1.0) > 0.05:
        change = abs(1.0 - ratio)
        sentences.append(camera_phrase("push" if ratio < 1 else "pull", "in" if ratio < 1 else "out",
                                       speed(change / seconds, 0.06, 0.2), _amplitude(change, 0.25, 0.5)))
    orbit = float(last.get("orbit", 0.0)) - float(first.get("orbit", 0.0))
    if abs(orbit) > 3:
        sentences.append(camera_phrase("arc", "right" if orbit > 0 else "left",
                                       speed(abs(orbit) / seconds, 4, 15), _amplitude(abs(orbit), 15, 45)))
    elevation = float(last.get("elevation", 0.0)) - float(first.get("elevation", 0.0))
    if abs(elevation) > 3:
        sentences.append(camera_phrase("pedestal", "up" if elevation > 0 else "down",
                                       speed(abs(elevation) / seconds, 3, 10), _amplitude(abs(elevation), 10, 25)))
    f0, f1 = float(first.get("fov", 45.0)), float(last.get("fov", 45.0))
    if abs(ratio - 1.0) <= 0.05 and f0 > 0 and abs(f1 / f0 - 1.0) > 0.08:
        change = abs(1.0 - f1 / f0)
        sentences.append(camera_phrase("zoom", "in" if f1 < f0 else "out",
                                       speed(change / seconds, 0.06, 0.2), _amplitude(change, 0.25, 0.5)))
    return " ".join(sentences) or camera_phrase("static")
