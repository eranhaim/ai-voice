# Voice data handling

## Reference audio

The app rejects empty, unsupported, very short, very long, very quiet, and heavily clipped
reference audio. It sends validated audio to ElevenLabs for IVC or PVC enrollment and does not
retain new source files in S3.

Existing S3 reference objects from before this change are not automatically deleted. Review and
delete them only after confirming their retention requirements.

## Provider behavior

`eleven_v4` is used for text and dialogue synthesis. IVC/PVC remain the only enrollment paths.
PVC retains ElevenLabs' identity-verification step. No feature may bypass provider identity
verification or promise exact voice reproduction.
