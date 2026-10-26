param([switch]$CpuOnly)
$ErrorActionPreference = 'Stop'

if (-not $env:APPDATA) { throw 'APPDATA is not set. Run this setup from a normal Windows user session.' }
$runtime = Join-Path $env:APPDATA 'CreatorFlow\chatterbox-venv'
$python = Join-Path $runtime 'Scripts\python.exe'

if (-not (Test-Path $python)) {
    Write-Host "Creating an isolated Chatterbox environment. This will not change CreatorFlow's pinned AI packages." -ForegroundColor Cyan
    py -3.11 -m venv $runtime
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 was not found. Install Python 3.11, then run this script again.' }
}

& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Could not update pip in the Chatterbox environment.' }

if ($CpuOnly) {
    Write-Host 'Installing CPU PyTorch 2.6.0...' -ForegroundColor Cyan
    & $python -m pip install torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cpu
} else {
    Write-Host 'Installing CUDA 12.4 PyTorch 2.6.0 for NVIDIA GPUs...' -ForegroundColor Cyan
    & $python -m pip install torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
}
if ($LASTEXITCODE -ne 0) { throw 'PyTorch installation failed. Check the Python version, internet connection, and NVIDIA driver.' }

Write-Host 'Installing Chatterbox TTS and its isolated dependencies...' -ForegroundColor Cyan
& $python -m pip install chatterbox-tts==0.1.7
if ($LASTEXITCODE -ne 0) { throw 'Chatterbox installation failed. Review the pip output above.' }

& $python -c "from chatterbox.tts import ChatterboxTTS; import torch; print('Chatterbox import OK; CUDA available:', torch.cuda.is_available())"
if ($LASTEXITCODE -ne 0) { throw 'Chatterbox was installed but could not be imported.' }
Write-Host ''
Write-Host 'Chatterbox runtime is ready. CreatorFlow will detect it automatically.' -ForegroundColor Green
Write-Host "Python: $python"
Write-Host 'The Chatterbox model weights download automatically on first voice generation.'
