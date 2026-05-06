import asyncio
from bson import ObjectId
from db import get_db

async def test():
    db = get_db()
    doc = await db.runs.find_one({"_id": ObjectId("69fb6cb7f2cd9fb6d26d78be")})
    if doc:
        print("Found run, type:", doc.get("type"))
        print("input_audio_url:", doc.get("input_audio_url", "MISSING"))
    else:
        print("Run not found")

asyncio.run(test())
