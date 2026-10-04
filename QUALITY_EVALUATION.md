# Voice quality evaluation

Evaluate a voice profile with non-sensitive scripts. Test the same script with the previous
`eleven_v3` baseline and `eleven_v4` after enrollment is complete.
Blind the reviewer to the engine when practical.

## Per-script rubric

Score each category from 1 (poor) to 5 (excellent):

| Category | What to assess |
| --- | --- |
| Speaker similarity | Recognizable timbre, accent, and vocal character without claiming identity certainty |
| Naturalness | Smooth delivery without robotic phrasing, artifacts, or excessive effects |
| Pacing | Appropriate tempo and natural pauses for the script |
| Intelligibility | Clear Hebrew words, names, numbers, and gendered forms |
| Prosody | Natural emphasis, emotion, and sentence melody |

Use at least three scripts: a short greeting, a conversational paragraph, and a script containing
names or numbers. Record engine, voice profile ID, language, settings, seed when used, and all
five scores. Do not put audio or sensitive text in source control.

## Acceptance

Adopt V4 for a creator only when its mean score is at least the baseline mean and no individual
category is lower by more than one point. Keep the scoring sheet with the project records. This
process measures subjective quality; it does not prove exact reproduction or guarantee identical
output.
