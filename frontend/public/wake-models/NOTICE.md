# Vendored model files -- provenance and license

These three ONNX files implement the "hey jarvis" wake-word detection chain
used by `frontend/src/wake/openWakeWordDetector.ts` (R11: on-device, passive
wake-word listening with no audio published upstream until woken).

| File                     | Source release                                                          | Size (bytes) |
|--------------------------|---------------------------------------------------------------------------|-------------:|
| `melspectrogram.onnx`    | dscripka/openWakeWord v0.5.1                                            |    1,087,958 |
| `embedding_model.onnx`   | dscripka/openWakeWord v0.5.1                                            |    1,326,578 |
| `hey_jarvis_v0.1.onnx`   | dscripka/openWakeWord v0.5.1                                            |    1,271,370 |

Upstream repository: https://github.com/dscripka/openWakeWord
Release used: https://github.com/dscripka/openWakeWord/releases/tag/v0.5.1

## License -- read this before redistributing or using commercially

Per the upstream repository's README ("License" section, verbatim):

> All of the code in this repository is licensed under the Apache 2.0
> license. All of the included pre-trained models are licensed under the
> Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International
> license due to the inclusion of datasets with unknown or restrictive
> licensing as part of the training data.

So: **the openWakeWord framework code is Apache-2.0, but these three model
WEIGHT files are CC BY-NC-SA 4.0** (https://creativecommons.org/licenses/by-nc-sa/4.0/),
**not** Apache-2.0. That means, for these specific files:

- Attribution is required (this notice serves that purpose).
- Non-commercial use only.
- Any redistribution of these files, or of derivatives built from them,
  must carry the same CC BY-NC-SA 4.0 license.

This is a materially different license than the MIT-licensed
`onnxruntime-web` runtime these models are executed with (see
`frontend/public/ort/`), and different from what was originally assumed
when this replacement for Porcupine was scoped. It was still judged
acceptable for this project (an educational/cohort submission, not a
commercial product), but it is a real constraint a future commercial
deployment of this app would have to resolve -- either by clearing rights
for these specific files or by swapping in a permissively-licensed wake
model.
