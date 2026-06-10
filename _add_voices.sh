#!/bin/bash
TOKEN=$(curl -s -X POST http://localhost:7777/api/login \
  -H 'Content-Type: application/json' \
  -d '{"password":"eran123"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')

echo "Token: ${TOKEN:0:10}..."

add_voice() {
  local name=$1
  local vid=$2
  echo "Adding $name ($vid)..."
  curl -s -X POST http://localhost:7777/api/system-voices \
    -H 'Content-Type: application/json' \
    -H "Authorization: $TOKEN" \
    -d "{\"name\":\"$name\",\"elevenlabs_voice_id\":\"$vid\"}"
  echo
}

add_voice "Alexandra (טבעית)" "kdmDKE6EkgrWrrykO9Qt"
add_voice "Rachel (ניטרלית)" "21m00Tcm4TlvDq8ikWAM"
add_voice "Bella (רכה)" "EXAVITQu4vr4xnSDxMaL"
add_voice "Jessica (חמה)" "g6xIsTj2HwM6VR4iXFCw"
add_voice "Eryn (חברותית)" "dj3G1R1ilKoFKhBnWOzG"

echo "Done."
