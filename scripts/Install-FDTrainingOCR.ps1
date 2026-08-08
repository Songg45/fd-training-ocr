#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$InstallRoot = "C:\Github\fd-training-ocr",
    [string]$DataRoot = "C:\Temp",
    [string]$RepositoryUrl = "https://github.com/Songg45/fd-training-ocr.git",
    [switch]$SkipModels,
    [switch]$SkipDesktopShortcut,
    [switch]$RunTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step { param([string]$Message) Write-Host "`n==> $Message" -ForegroundColor Cyan }
function Invoke-Native {
    param([Parameter(Mandatory)][string]$FilePath, [Parameter(Mandatory)][string[]]$Arguments)
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$FilePath failed with exit code $LASTEXITCODE" }
}
function Refresh-ProcessPath {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = @($machinePath, $userPath) -join ";"
}
function Install-WinGetPackage {
    param([Parameter(Mandatory)][string]$Id)
    & winget list --exact --id $Id --source winget --accept-source-agreements *> $null
    if ($LASTEXITCODE -eq 0) { Write-Host "$Id is already installed."; return }
    Invoke-Native "winget" @("install", "--exact", "--id", $Id, "--source", "winget", "--silent",
        "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity")
}
function Resolve-RequiredCommand {
    param([Parameter(Mandatory)][string]$Name, [string[]]$Candidates = @())
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -ne $command) { return $command.Source }
    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "Required executable not found after installation: $Name"
}
function ConvertTo-TomlPath { param([string]$Path) return $Path.Replace("\", "\\") }
function Write-Utf8NoBom {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Value)
    [IO.File]::WriteAllText($Path, $Value, (New-Object Text.UTF8Encoding($false)))
}

if ($env:OS -ne "Windows_NT") { throw "This installer supports Windows only." }
if ([Environment]::OSVersion.Version.Build -lt 19045) {
    throw "Ollama requires Windows 10 22H2 (build 19045) or newer."
}
if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
    throw "WinGet is required. Install or update Microsoft App Installer, then rerun this script."
}
$installDrive = Split-Path -Qualifier $InstallRoot
if ($installDrive) {
    $drive = Get-PSDrive -Name ($installDrive.TrimEnd(":")) -ErrorAction SilentlyContinue
    if ($null -ne $drive -and $drive.Free -lt 25GB) {
        throw "At least 25 GB of free space is required for tools, environments, and both models."
    }
}

Write-Step "Installing Windows prerequisites through WinGet"
Install-WinGetPackage "Git.Git"
Install-WinGetPackage "Python.Python.3.12"
Install-WinGetPackage "oschwartz10612.Poppler"
Install-WinGetPackage "Ollama.Ollama"
Refresh-ProcessPath

$gitExe = Resolve-RequiredCommand "git.exe" @("$env:ProgramFiles\Git\cmd\git.exe")
$pythonLauncher = Resolve-RequiredCommand "py.exe" @("$env:SystemRoot\py.exe",
    "$env:LOCALAPPDATA\Programs\Python\Launcher\py.exe")
$ollamaExe = Resolve-RequiredCommand "ollama.exe" @("$env:LOCALAPPDATA\Programs\Ollama\ollama.exe")
$ollamaVersionText = (& $ollamaExe --version 2>&1 | Out-String)
if ($ollamaVersionText -match "([0-9]+\.[0-9]+\.[0-9]+)") {
    if ([version]$Matches[1] -lt [version]"0.12.7") {
        Write-Step "Upgrading Ollama for Qwen3-VL compatibility"
        Invoke-Native "winget" @("upgrade", "--exact", "--id", "Ollama.Ollama", "--source", "winget", "--silent",
            "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity")
        Refresh-ProcessPath
        $ollamaExe = Resolve-RequiredCommand "ollama.exe" @("$env:LOCALAPPDATA\Programs\Ollama\ollama.exe")
    }
} else {
    throw "Unable to determine the installed Ollama version."
}
$pdftoppmExe = $null
try {
    $pdftoppmExe = Resolve-RequiredCommand "pdftoppm.exe" @("$env:LOCALAPPDATA\Microsoft\WinGet\Links\pdftoppm.exe")
} catch {
    $packageRoot = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages"
    if (Test-Path -LiteralPath $packageRoot -PathType Container) {
        $found = Get-ChildItem -LiteralPath $packageRoot -Filter "pdftoppm.exe" -File -Recurse |
            Where-Object { $_.FullName -like "*oschwartz10612.Poppler*" } | Select-Object -First 1
        if ($null -ne $found) { $pdftoppmExe = $found.FullName }
    }
    if (-not $pdftoppmExe) { throw }
}

Write-Step "Installing or updating fd-training-ocr"
$installParent = Split-Path -Parent $InstallRoot
if (-not (Test-Path -LiteralPath $installParent)) {
    New-Item -ItemType Directory -Path $installParent -Force | Out-Null
}
if (Test-Path -LiteralPath (Join-Path $InstallRoot ".git") -PathType Container) {
    Invoke-Native $gitExe @("-C", $InstallRoot, "pull", "--ff-only")
} elseif (Test-Path -LiteralPath $InstallRoot) {
    if ((Get-ChildItem -LiteralPath $InstallRoot -Force | Measure-Object).Count -ne 0) {
        throw "InstallRoot exists but is not an empty directory or Git checkout: $InstallRoot"
    }
    Invoke-Native $gitExe @("clone", $RepositoryUrl, $InstallRoot)
} else {
    Invoke-Native $gitExe @("clone", $RepositoryUrl, $InstallRoot)
}

$templatePath = Join-Path $InstallRoot "templates\pilot_fd_training_sign_in\v1\template.json"
$masterPath = Join-Path $InstallRoot "templates\pilot_fd_training_sign_in\v1\cleaned-master.png"
if (-not (Test-Path -LiteralPath $templatePath -PathType Leaf)) { throw "Template JSON is missing: $templatePath" }
if (-not (Test-Path -LiteralPath $masterPath -PathType Leaf)) { throw "Cleaned master is missing: $masterPath" }

Write-Step "Creating Python 3.12 environment and installing the GUI"
$venvPath = Join-Path $InstallRoot ".venv"
if (-not (Test-Path -LiteralPath (Join-Path $venvPath "Scripts\python.exe") -PathType Leaf)) {
    Invoke-Native $pythonLauncher @("-3.12", "-m", "venv", $venvPath)
}
$venvPython = Join-Path $venvPath "Scripts\python.exe"
Invoke-Native $venvPython @("-m", "pip", "install", "--upgrade", "pip", "setuptools")
Push-Location $InstallRoot
try {
    Invoke-Native $venvPython @("-m", "pip", "install", "-e", ".[gui]")
    if ($RunTests) { Invoke-Native $venvPython @("-m", "unittest", "discover", "-s", "tests", "-v") }
} finally { Pop-Location }

Write-Step "Preparing external configuration and roster"
New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null
$outputRoot = Join-Path $DataRoot "fd-training-ocr-output"
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$configPath = Join-Path $DataRoot "fd-training-ocr-config.toml"
$rosterPath = Join-Path $DataRoot "fd-training-ocr-roster.json"
if (-not (Test-Path -LiteralPath $rosterPath -PathType Leaf)) {
    $rosterText = @{ schema_version = 1; members = @(@{ name = "Example Member"; unit_ids = @("4554"); aliases = @("E. Member") }) } |
        ConvertTo-Json -Depth 6
    Write-Utf8NoBom $rosterPath $rosterText
    Write-Warning "Created an example roster at $rosterPath. Replace it before production use."
} else { Write-Host "Preserving existing roster: $rosterPath" }
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    $configText = @"
[app]
output_dir = "$(ConvertTo-TomlPath $outputRoot)"
template_dir = "$(ConvertTo-TomlPath (Join-Path $InstallRoot 'templates'))"
log_level = "INFO"
offline = false
ollama_endpoint = "http://127.0.0.1:11434"
ollama_model = "qwen2.5vl:7b"
ollama_stage3_model = "qwen3-vl:8b-instruct"
ollama_timeout_seconds = 180
roster_path = "$(ConvertTo-TomlPath $rosterPath)"
valid_apparatus = ["Engine 54", "Tanker 54", "Brush 54", "Engine 254", "Tanker 854", "Brush 254"]
valid_locations = ["District", "Pilot Fire Department"]
location_aliases = { PFD = "Pilot Fire Department", "Pilot FD" = "Pilot Fire Department", "Pilot Fire Department" = "Pilot Fire Department" }
"@
    Write-Utf8NoBom $configPath $configText
} else { Write-Host "Preserving existing configuration: $configPath" }

Write-Step "Starting Ollama and installing local OCR models"
$ollamaReady = $false
try { Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3 | Out-Null; $ollamaReady = $true }
catch {
    Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Hidden
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Seconds 1
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2 | Out-Null
            $ollamaReady = $true; break
        } catch { }
    }
}
if (-not $ollamaReady) { throw "Ollama did not become ready at http://127.0.0.1:11434" }
if (-not $SkipModels) {
    Invoke-Native $ollamaExe @("pull", "qwen2.5vl:7b")
    Invoke-Native $ollamaExe @("pull", "qwen3-vl:8b-instruct")
} else { Write-Warning "Model downloads skipped; both configured models are required before processing." }

Write-Step "Verifying configuration"
Invoke-Native $venvPython @("-m", "fd_training_ocr.cli", "--config", $configPath, "inspect-config")
$guiExe = Join-Path $venvPath "Scripts\fd-training-ocr-gui.exe"
if (-not (Test-Path -LiteralPath $guiExe -PathType Leaf)) { throw "GUI entry point was not installed: $guiExe" }

if (-not $SkipDesktopShortcut) {
    Write-Step "Creating desktop shortcut"
    $shortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "FD Training OCR.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $guiExe
    $shortcut.Arguments = "--config `"$configPath`" --master `"$masterPath`" --template `"$templatePath`" --pdftoppm `"$pdftoppmExe`""
    $shortcut.WorkingDirectory = $InstallRoot
    $shortcut.Description = "Local Pilot Fire Department training-form OCR"
    $shortcut.Save()
    Write-Host "Desktop shortcut created: $shortcutPath"
}
if (-not (Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue)) {
    Write-Warning "nvidia-smi was not found. Install the current NVIDIA driver before production OCR."
}

Write-Host "`nFD Training OCR installation complete." -ForegroundColor Green
Write-Host "Application: $InstallRoot"
Write-Host "Configuration: $configPath"
Write-Host "Roster: $rosterPath"
Write-Host "Output: $outputRoot"
Write-Host "Master: $masterPath"
Write-Host "Poppler: $pdftoppmExe"
Write-Host "Models remain local in Ollama and are not sent to an external service."
