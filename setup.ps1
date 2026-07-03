$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$pythonCommand = $null
$pythonArguments = @()

if (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCommand = "py"
    $pythonArguments = @("-3")
}
elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonCommand = "python"
}
else {
    throw "Python 3.11+ was not found. Install Python, then rerun this script."
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $pythonCommand @pythonArguments -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}
if (-not (Test-Path "config.json")) {
    Copy-Item "config.example.json" "config.json"
}

New-Item -ItemType Directory -Force -Path "secrets", "runtime" | Out-Null

Write-Host ""
Write-Host "Setup complete."
Write-Host "Next: edit .env and config.json, then use VS Code's Run and Debug panel."
