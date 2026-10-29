$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $repoRoot 'backend'
$venvPython = Join-Path $backend '.venv\Scripts\python.exe'

if (-not (Test-Path $venvPython)) {
    Write-Host 'Creating the private build environment (Python 3.11 required)...' -ForegroundColor Cyan
    py -3.11 -m venv (Join-Path $backend '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 was not found. Install it from python.org or winget, then run this script again.' }
}

Write-Host 'Installing desktop app dependencies...' -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Could not update pip.' }
& $venvPython -m pip install -r (Join-Path $backend 'requirements-desktop.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Review the output above.' }
& $venvPython -c "import numpy; print('Packaging with NumPy', numpy.__version__); assert numpy.__version__ == '1.26.4', 'NumPy must be 1.26.4 for this MoviePy/PyInstaller build.'"
if ($LASTEXITCODE -ne 0) { throw 'The build environment has an unsupported NumPy version.' }

Push-Location $backend
try {
    Write-Host 'Packaging CreatorFlow as a Windows desktop application...' -ForegroundColor Cyan
    & $venvPython -m PyInstaller --noconfirm --clean --windowed --onedir `
        --name CreatorFlow `
        --add-data "$repoRoot\frontend;frontend" `
        --collect-all moviepy `
        --collect-all numpy `
        --collect-all imageio `
        --collect-all imageio_ffmpeg `
        --collect-all comtypes `
        --collect-all proglog `
        --collect-all decorator `
        --copy-metadata moviepy `
        --collect-all webview `
        --collect-all clr_loader `
        --collect-all pythonnet `
        --collect-all llama_cpp `
        --hidden-import webview.platforms.edgechromium `
        --hidden-import webview.platforms.winforms `
        desktop.py
    if ($LASTEXITCODE -ne 0) { throw 'Packaging failed. Review the PyInstaller output above.' }
} finally {
    Pop-Location
}

Write-Host ''
Write-Host 'Build complete!' -ForegroundColor Green
Write-Host "Launch the app: $backend\dist\CreatorFlow\CreatorFlow.exe" -ForegroundColor Green
Write-Host 'Configure OpenRouter, stock-media keys, and YouTube inside CreatorFlow Settings.'
