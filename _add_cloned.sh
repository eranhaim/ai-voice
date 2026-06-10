#!/bin/bash
TOKEN=$(curl -s -X POST http://localhost:7777/api/login \
  -H 'Content-Type: application/json' \
  -d '{"password":"eran123"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')

echo "Adding cloned Israeli voice..."
curl -s -X POST http://localhost:7777/api/system-voices \
  -H 'Content-Type: application/json' \
  -H "Authorization: $TOKEN" \
  -d '{"name":"קול מעולה של אישה","elevenlabs_voice_id":"L6MkgvVoFggyQz4tmakR"}'
echo
echo "Done."
