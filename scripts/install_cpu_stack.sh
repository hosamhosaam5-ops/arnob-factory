#!/usr/bin/env bash
set -euo pipefail

sudo apt-get update
sudo apt-get install --no-install-recommends -y ffmpeg espeak-ng
printf 'FFmpeg: %s\n' "$(ffmpeg -version | head -n 1)"
printf 'ffprobe: %s\n' "$(ffprobe -version | head -n 1)"
printf 'eSpeak NG: %s\n' "$(espeak-ng --version | head -n 1)"

python -m pip install --no-cache-dir --upgrade pip

# Official mutually compatible CPU-only wheel set: torch 2.11.0 / torchaudio 2.11.0 / torchvision 0.26.0.
python -m pip install --no-cache-dir \
  --index-url https://download.pytorch.org/whl/cpu \
  "torch==2.11.0" "torchaudio==2.11.0" "torchvision==0.26.0"

# CPU-compatible inference dependencies. Do not add TorchCodec or CUDA ONNX Runtime.
python -m pip install --no-cache-dir \
  cached_path click "ema_pytorch>=0.5.2" "hydra-core>=1.3.0" \
  librosa matplotlib "numpy<=1.26.4" pydub safetensors soundfile tomli \
  torchdiffeq "tqdm>=4.65.0" transformers transformers_stream_generator \
  unidecode vocos "x_transformers>=1.31.14" \
  "nemo_text_processing==1.1.0" "onnxruntime==1.32.0"

python -m pip install --no-cache-dir --no-deps -r scripts/requirements-silma.txt
python -m pip install --no-cache-dir --no-deps "catt_tashkeel==1.0.2"
python -m pip uninstall --yes torchcodec onnxruntime-gpu

# Required exact runtime diagnostic requested by the owner.
python -c "import torch,torchaudio;print(torch.__version__,torchaudio.__version__,torch.cuda.is_available())"

python -c 'import importlib.metadata as m; import onnxruntime, torch, torchaudio, torchvision; from importlib.metadata import PackageNotFoundError; expected={"torch":"2.11.0","torchaudio":"2.11.0","torchvision":"0.26.0","silma-tts":"1.0.5","onnxruntime":"1.32.0"}
for name, version in expected.items():
 actual=m.version(name).split("+",1)[0]
 print(f"{name}={m.version(name)}")
 assert actual == version, f"Expected {name}=={version}, found {m.version(name)}"
assert torch.cuda.is_available() is False, "CUDA must be unavailable"
assert torch.version.cuda is None, f"Unexpected CUDA runtime: {torch.version.cuda}"
providers=onnxruntime.get_available_providers()
print(f"onnxruntime-providers={providers}")
assert "CUDAExecutionProvider" not in providers, "CUDA ONNX Runtime provider is forbidden"
forbidden=("torchcodec","onnxruntime-gpu")
for name in forbidden:
 try:
  m.version(name)
 except PackageNotFoundError:
  print(f"{name}=not-installed")
 else:
  raise AssertionError(f"Forbidden package remains installed: {name}")
print("CPU runtime verification=PASS")'
