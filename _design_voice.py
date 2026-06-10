"""Generate a custom Israeli female voice using ElevenLabs Voice Design API."""
import os, base64
from elevenlabs.client import ElevenLabs

client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))

DESCRIPTION = (
    "A young Israeli woman in her mid-20s with a warm, casual, slightly raspy voice. "
    "She speaks with a natural Israeli Hebrew accent — confident but relaxed, like she's "
    "talking to a close friend. Her tone is flirty and playful with a hint of breathiness. "
    "She has a medium pitch, not too high or too low, with natural vocal fry at the end of sentences. "
    "Her pacing is conversational and unhurried, with natural pauses."
)

SAMPLE_TEXT = (
    "היי מותק, מה קורה איתך? חיכיתי לשמוע ממך כל היום. "
    "אתה לא מאמין מה קרה לי היום בעבודה... בוא נדבר על זה אחר כך, "
    "אני רוצה לשמוע מה עשית. ספר לי הכל, אני פה בשבילך."
)

print("Designing voice...")
print(f"Description: {DESCRIPTION[:100]}...")
print(f"Sample text: {SAMPLE_TEXT[:80]}...")

result = client.text_to_voice.design(
    voice_description=DESCRIPTION,
    text=SAMPLE_TEXT,
)

print(f"\nGot {len(result.previews)} previews:")
for i, preview in enumerate(result.previews):
    print(f"  [{i}] id={preview.generated_voice_id} duration={preview.duration_secs:.1f}s")
    audio = base64.b64decode(preview.audio_base_64)
    path = f"/tmp/voice_preview_{i}.mp3"
    with open(path, "wb") as f:
        f.write(audio)
    print(f"      saved to {path} ({len(audio)} bytes)")

# Save the first one as a voice
best = result.previews[0]
print(f"\nCreating voice from preview {best.generated_voice_id}...")
voice = client.text_to_voice.create(
    voice_name="הישראלית (ברירת מחדל)",
    voice_description=DESCRIPTION,
    generated_voice_id=best.generated_voice_id,
)
print(f"Voice created! ID: {voice.voice_id}")
print(f"Name: הישראלית (ברירת מחדל)")
print(f"Use this voice_id to add as system voice.")
