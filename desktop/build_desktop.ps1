# Build the Flutter UI and self-contained Python backend into one Windows zip.
# Usage from the repository root:
#   powershell -ExecutionPolicy Bypass -File desktop/build_desktop.ps1

param(
    [string]$Python = "python",
    [string]$Flutter = "flutter",
    [switch]$SkipDependencyInstall,
    [switch]$SkipBackendBuild,
    [switch]$SkipFlutterBuild
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$flutterRoot = Join-Path $projectRoot "desktop_app"
$packageRoot = Join-Path $projectRoot "dist\EzTradeDesktop"
$zipPath = Join-Path $projectRoot "dist\EzTradeDesktop.zip"

Push-Location $projectRoot
try {
    if (-not $SkipBackendBuild -and -not $SkipDependencyInstall) {
        Write-Host "Installing backend build dependencies..."
        & $Python -m pip install -r desktop/requirements-desktop.txt
        if ($LASTEXITCODE -ne 0) { throw "Backend dependency installation failed." }
    }

    if (-not $SkipBackendBuild) {
        Write-Host "Building the bundled backend..."
        & $Python -m PyInstaller --noconfirm --clean desktop/desktop.spec
        if ($LASTEXITCODE -ne 0) { throw "Backend build failed." }
    }

    if (-not $SkipFlutterBuild) {
        Write-Host "Resolving Flutter packages..."
        Push-Location $flutterRoot
        try {
            & $Flutter pub get
            if ($LASTEXITCODE -ne 0) { throw "flutter pub get failed." }

            Write-Host "Building the Flutter Windows release..."
            & $Flutter build windows --release
            if ($LASTEXITCODE -ne 0) { throw "Flutter build failed." }
        }
        finally {
            Pop-Location
        }
    }

    $flutterExecutable = Get-ChildItem (Join-Path $flutterRoot "build\windows") `
        -Recurse -Filter "ez_trade_desktop.exe" |
        Where-Object { $_.FullName -match "\\Release\\" } |
        Select-Object -First 1
    if (-not $flutterExecutable) {
        throw "Could not locate the Flutter release executable."
    }

    $flutterRelease = $flutterExecutable.Directory.FullName
    $backendRelease = Join-Path $projectRoot "dist\backend"
    $backendExecutable = Join-Path $backendRelease "eztrade_backend.exe"
    if (-not (Test-Path $backendExecutable)) {
        throw "Could not locate the packaged backend executable."
    }

    Write-Host "Validating packaged MT5 worker dependencies..."
    $runtimeValidated = $false
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $validation = Start-Process `
            -FilePath $backendExecutable `
            -ArgumentList "--check-runtime" `
            -PassThru `
            -WindowStyle Hidden
        if ($validation.WaitForExit(60000) -and $validation.ExitCode -eq 0) {
            $runtimeValidated = $true
            break
        }
        if (-not $validation.HasExited) {
            Stop-Process -Id $validation.Id -Force
            $validation.WaitForExit()
        }
        if ($attempt -lt 3) { Start-Sleep -Seconds (2 * $attempt) }
    }
    if (-not $runtimeValidated) {
        throw "Packaged backend runtime validation failed."
    }

    if (Test-Path $packageRoot) {
        Remove-Item -LiteralPath $packageRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $packageRoot | Out-Null
    Copy-Item -Path (Join-Path $flutterRelease "*") -Destination $packageRoot -Recurse
    Copy-Item -LiteralPath $backendRelease -Destination (Join-Path $packageRoot "backend") -Recurse

    if (Test-Path $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }
    # Use Windows' standard ZIP writer for Explorer compatibility. Antivirus
    # can briefly scan PyInstaller files, so retry transient sharing failures.
    $archiveCreated = $false
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            Compress-Archive `
                -Path (Join-Path $packageRoot "*") `
                -DestinationPath $zipPath `
                -CompressionLevel Optimal `
                -ErrorAction Stop
            $archiveCreated = $true
            break
        }
        catch {
            if (Test-Path $zipPath) {
                Remove-Item -LiteralPath $zipPath -Force
            }
            if ($attempt -eq 5) { throw }
            Start-Sleep -Seconds (2 * $attempt)
        }
    }
    if (-not $archiveCreated) { throw "Desktop zip creation failed." }

    Write-Host ""
    Write-Host "Package ready: $zipPath"
    Write-Host "Users extract the zip and run ez_trade_desktop.exe."
}
finally {
    Pop-Location
}
