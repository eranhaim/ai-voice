"""Install the Hebrew default voices the bot offers before a creator is cloned.

These are ElevenLabs Voice Library voices whose own speaker is Hebrew-speaking
(`verified_languages` contains `he`), not English premades reading Hebrew with
an American accent. Each one was rendered through eleven_v4 with Hebrew text and
checked by Whisper round-trip before being listed here: all transcribed back
word-perfect and were detected as Hebrew.

They are marked `is_default` so /voices can list them apart from the creator
voices an operator adds by hand.

Idempotent -- run it as often as you like:

    docker compose exec bot python seed_default_voices.py
"""
import asyncio

from dotenv import load_dotenv

load_dotenv()

from db import get_db  # noqa: E402  -- needs MONGO_URI from .env first

DEFAULT_VOICES = [
    # female, conversational and social -- closest to what this product sends
    ("מאיה — צעירה, עליזה", "UZzDIQRRTW2Id7YBcbgC"),
    ("שירה — צעירה, חברמנית", "1WOvbtTEb49xcgFUMDu9"),
    ("מיכל — רגועה, שיחתית", "fuMEGqYpFlKTER3VqptM"),
    ("דנה — חמה, שיחתית", "V0stpogHp8KBgdErUZbd"),
    ("יעל — עדינה, בטוחה", "d4eLZ8u5dfBCsjp4YPyz"),
    ("תמר — עדינה, בטוחה", "p7J75VowGmxi3K0it9lN"),
    ("נועה — חמה, מספרת", "6WRlPmU1fqPwYbSxeP16"),
    # male
    ("איתי — צעיר, אנרגטי", "JIxTgeeS5w0UQyBxEnrl"),
    ("עומר — בטוח, אופטימי", "k77ZRVqAFg9Hd0ltAuON"),
    ("תומר — רגוע, מספר", "yGpVFXFoImvRvaURKypy"),
    ("עמית — רגוע, מספר", "uwHajH4FhtzVp6X17pr7"),
]


async def main() -> None:
    db = get_db()
    added = 0
    for name, elevenlabs_voice_id in DEFAULT_VOICES:
        result = await db.system_voices.update_one(
            {"elevenlabs_voice_id": elevenlabs_voice_id},
            {"$set": {"name": name, "is_default": True}},
            upsert=True,
        )
        if result.upserted_id:
            added += 1
            print(f"added   {name}")
        else:
            print(f"updated {name}")
    print(f"\n{len(DEFAULT_VOICES)} default voices seeded ({added} new).")


if __name__ == "__main__":
    asyncio.run(main())
