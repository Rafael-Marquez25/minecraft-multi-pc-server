$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = "C:/Users/ramar/AppData/Local/Programs/Python/Python312/python.exe"

if (-not (Test-Path $python)) {
    $python = "python"
}

Push-Location $root
try {
    & $python -c "import PyInstaller" 2>$null
    if ($LASTEXITCODE -ne 0) {
        & $python -m pip install pyinstaller
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to install PyInstaller"
        }
    }

    $launcherEntry = Join-Path $root "minecraft_multi_pc_server/gui_entry.py"
    $installerEntry = Join-Path $root "scripts/install_dependencies.py"

    & $python -m PyInstaller --noconfirm --clean --onefile --windowed --name MinecraftServerLauncher --collect-submodules tkinter $launcherEntry
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed"
    }

    & $python -m PyInstaller --noconfirm --clean --onefile --windowed --name InstalarDependencias --collect-submodules tkinter $installerEntry
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installer build failed"
    }

    $launcherExe = Join-Path $root "dist/MinecraftServerLauncher.exe"
    $installerExe = Join-Path $root "dist/InstalarDependencias.exe"
    $launcherSmoke = Start-Process -FilePath $launcherExe -ArgumentList "--smoke-test" -Wait -PassThru -WindowStyle Hidden
    if ($launcherSmoke.ExitCode -ne 0) {
        throw "Launcher EXE smoke test failed with exit code $($launcherSmoke.ExitCode)"
    }
    $installerSmoke = Start-Process -FilePath $installerExe -ArgumentList "--check" -Wait -PassThru -WindowStyle Hidden
    if ($installerSmoke.ExitCode -ne 0) {
        throw "Dependency installer EXE smoke test failed with exit code $($installerSmoke.ExitCode)"
    }

    Write-Host "Created dist/MinecraftServerLauncher.exe"
    Write-Host "Created dist/InstalarDependencias.exe"
    Write-Host "Smoke tests passed"
}
finally {
    Pop-Location
}
