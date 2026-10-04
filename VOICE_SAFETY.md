# Voice consent and data handling

## Required authorization

Only enroll or generate with a creator voice after the creator has explicitly authorized
voice cloning and generation for this project. Record a consent reference such as a signed
agreement, support ticket, or recorded approval ID. Do not store the consent document itself
in this application.

The bot and admin API require a consent confirmation and reference for every new profile.
Profiles created before this control have an `unknown` consent status and cannot be selected or
generated until an administrator records consent in the admin UI.

## Reference audio

Upload only clean, single-speaker material that the creator authorized. The app rejects empty,
unsupported, very short, very long, very quiet, and heavily clipped files. It sends validated
audio to ElevenLabs for IVC or PVC enrollment and does not retain new source files in S3.

Existing S3 reference objects from before this change are not automatically deleted. Review and
delete them only after confirming retention requirements with the creator and customer.

## Provider behavior

`eleven_v4` is used for text and dialogue synthesis. IVC/PVC remain the only enrollment paths.
PVC retains ElevenLabs' identity-verification step. No feature may bypass provider identity
verification, recreate a voice without authorization, or promise exact voice reproduction.
