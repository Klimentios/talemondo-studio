"""Reconcile generated voice presets with explicit scene casting, without editing dialogue."""
import re


def normalize_voice_cast(prompt: str, manifest: dict) -> str:
    aliases = {}
    for number, image in enumerate(manifest.get("images", []), 1):
        owner = str(image.get("owner", "")).strip()
        match = re.search(rf"<Picture\s+{number}>\s+defines\s+(?!(?:this|the|a|an)\b)([\w-]+)", prompt, re.I)
        if owner and match:
            aliases[owner.casefold()] = match.group(1)

    def reconcile(match):
        owner = match.group(1).strip()
        subject = aliases.get(owner.casefold(), owner)
        description = re.search(
            rf"(?:^|\n){re.escape(subject)}\s+(?:speaks|speaking)\s+([^\n]{{0,500}}\bvoice\b[^\n]{{0,500}})",
            prompt, re.I,
        )
        if not description:
            return match.group(0)
        voice = description.group(1).strip().rstrip(".")
        return (
            f"VOICE CAST FOR {subject}: Follow the voice explicitly specified for {subject} "
            f"in this scene: {voice}. This scene description has priority over saved voice presets. "
            "Keep this character's voice identity stable while varying emotion, pauses, stress "
            "and intonation according to each dialogue beat. Do not swap voices between speakers."
        )

    return re.sub(r"^VOICE CAST FOR ([^:\r\n]+):[^\r\n]*", reconcile, prompt, flags=re.M)
