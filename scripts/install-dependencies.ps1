param(
    [switch]$Development
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "No se encontro winget. Instala 'App Installer' desde Microsoft Store."
}

$packages = @(
    @{ Name = "Tailscale"; Id = "Tailscale.Tailscale" },
    @{ Name = "Google Drive Desktop"; Id = "Google.GoogleDrive" }
)

foreach ($package in $packages) {
    Write-Host "Comprobando $($package.Name)..."
    & winget list --id $package.Id --exact --accept-source-agreements | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "$($package.Name) ya esta instalado."
        continue
    }

    & winget install --id $package.Id --exact --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) {
        throw "No se pudo instalar $($package.Name). Codigo: $LASTEXITCODE"
    }
}

if ($Development) {
    $python = "C:/Users/ramar/AppData/Local/Programs/Python/Python312/python.exe"
    if (-not (Test-Path $python)) {
        $python = (Get-Command python -ErrorAction Stop).Source
    }
    & $python -m pip install --upgrade pip
    & $python -m pip install --editable "$root" pyinstaller
    if ($LASTEXITCODE -ne 0) {
        throw "No se pudieron instalar las dependencias de desarrollo."
    }
}

Write-Host "Dependencias instaladas. Inicia sesion en Tailscale y Google Drive Desktop."
