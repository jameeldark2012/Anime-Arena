# Video + Whisper + Qwen Prototype

This prototype is separate from the production boss and AI code. It:

1. Samples evenly spaced JPEG frames from a video with ffmpeg.
2. Extracts mono 16 kHz audio with ffmpeg.
3. Transcribes speech locally with `faster-whisper`.
4. Sends the transcript and sampled frames to Ollama.
5. Saves the response as `<video-name>.qwen.json`.

## Requirements

- `ffmpeg` and `ffprobe` on PATH.
- Ollama running locally.
- The requested model installed:

```powershell
ollama pull qwen3.5:9b
```

- Python dependency:

```powershell
.\.env\Scripts\python.exe -m pip install -r prototype\video_qwen\requirements.txt
```

The first Whisper run downloads the selected model. Use `--whisper-model tiny` for the lightest CPU footprint or `base` for a better accuracy/resource balance. Qwen 3.5 9B is the larger resource cost; its memory use is controlled by Ollama and depends on quantization.

## Run

```powershell
.\.env\Scripts\python.exe prototype\video_qwen\run_video.py path\to\clip.mp4
```

Useful options:

```powershell
.\.env\Scripts\python.exe prototype\video_qwen\run_video.py path\to\clip.mp4 `
  --model qwen3.5:9b `
  --whisper-model tiny `
  --frames 8 `
  --context-size 32768 `
  --output output\clip-analysis.json
```

When using a large original battle prompt, keep `--context-size 32768` or higher if your Qwen quantization supports it. Ollama defaults to 4096, which is too small for the full battle prompt plus images.

To test only frames and Qwen without local speech-to-text:

```powershell
.\.env\Scripts\python.exe prototype\video_qwen\run_video.py path\to\clip.mp4 --no-transcription
```

The prototype sends images through Ollama's native `/api/chat` API as base64 JPEGs. It does not use the production `OllamaClient`, because that client currently declares media input unsupported.
