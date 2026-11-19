# YAMNet for Tariq's non-speech candidates

Downloaded 13 Nov 2026, with Josh's approval of an official install. The model is used only to flag moments for Josh to listen to. It never decides a cut.

- **Model:** YAMNet, AudioSet's 521 sound classes, float32 TFLite, as Google ships it for the MediaPipe Audio Classifier.
- **Source:** the MediaPipe Audio Classifier guide, https://developers.google.com/edge/mediapipe/solutions/audio/audio_classifier, which lists the model below as "Latest" with no numbered release.
- **Model URL:** https://storage.googleapis.com/mediapipe-models/audio_classifier/yamnet/float32/latest/yamnet.tflite
  - Google Cloud Storage generation 1682545011390892, last modified Wed, 26 Apr 2023 21:36:51 GMT, 4,126,810 bytes.
  - The MD5 matches Google's own `x-goog-hash` (0C4bg4gTEHgXt1XQnWtWsw==).
  - SHA-256: 4d8b4a53282dc83ef04e3e7dbc4fbc98082e34e44ed798e16c3a0cdd4c584faf
- **Labels URL:** https://storage.googleapis.com/mediapipe-tasks/audio_classifier/yamnet_label_list.txt
  - Generation 1677973303715674, 6,230 bytes, 521 lines.
  - The MD5 matches Google's `x-goog-hash` (wOoWvB2/1wQuaOSJss1SaQ==).
  - SHA-256: 8e1267a120c1932b7273c0d0e0c5529edbb9a35512b437b1c8982baa59047051
- **Runtime:** `mediapipe` 1.1.0, already installed in Josh OS's environment. Nothing new was installed.
- **Input:** 16 kHz mono, in 0.975 s windows (15,600 samples).
- **Download headers:** kept as received in `model.headers` and `labels.headers`.

`studio/cleanup_pass.py` (`yamnet_scores`) checks this SHA-256 before it loads the model, and refuses a file that doesn't match.
