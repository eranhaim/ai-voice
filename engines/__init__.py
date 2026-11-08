"""Alternative voice engines, compared side by side with ElevenLabs.

TTS engines (text -> voice):    elevenlabs, minimax, modelslab,
                                minimax+applio, minimax+seedvc
STS engines (recording -> voice): elevenlabs, applio, seedvc, modelslab
"""

TTS_ENGINES = {
    "elevenlabs": "ElevenLabs v3",
    "minimax": "MiniMax 2.8 HD",
    "modelslab": "ModelsLab",
    "minimax+applio": "MiniMax → Applio",
    "minimax+seedvc": "MiniMax → Seed-VC",
}

STS_ENGINES = {
    "elevenlabs": "ElevenLabs STS",
    "applio": "Applio (RVC)",
    "seedvc": "Seed-VC",
    "modelslab": "ModelsLab",
}

DEFAULT_TTS_ENGINE = "elevenlabs"
DEFAULT_STS_ENGINE = "elevenlabs"
