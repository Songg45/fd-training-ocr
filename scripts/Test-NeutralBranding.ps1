[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$Token = $env:FD_OCR_PROHIBITED_TOKEN
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($Token)) {
    [Console]::Error.WriteLine('Set FD_OCR_PROHIBITED_TOKEN outside the repository or pass -Token before running the audit.')
    exit 2
}

try {
    $root = [IO.Path]::GetFullPath($RepositoryRoot)
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        throw "Repository root does not exist: $root"
    }

    $insideWorkTree = & git -C $root rev-parse --is-inside-work-tree 2>$null
    if ($LASTEXITCODE -ne 0 -or $insideWorkTree -ne 'true') {
        throw "Repository root is not a Git work tree: $root"
    }

    $trackedFiles = @(& git -C $root ls-files --)
    if ($LASTEXITCODE -ne 0) {
        throw 'git ls-files failed.'
    }

    $regex = New-Object Text.RegularExpressions.Regex(
        [Text.RegularExpressions.Regex]::Escape($Token),
        ([Text.RegularExpressions.RegexOptions]::IgnoreCase -bor [Text.RegularExpressions.RegexOptions]::CultureInvariant)
    )
    $matches = New-Object System.Collections.Generic.List[string]

    foreach ($relativePath in $trackedFiles) {
        if ([string]::IsNullOrWhiteSpace($relativePath)) {
            continue
        }
        $displayPath = $relativePath.Replace('\', '/')
        if ($regex.IsMatch($displayPath)) {
            $matches.Add("PATH $displayPath")
        }

        $fullPath = Join-Path $root $relativePath
        if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
            continue
        }
        $bytes = [IO.File]::ReadAllBytes($fullPath)
        $hasUtf16Bom = ($bytes.Length -ge 2) -and (
            (($bytes[0] -eq 0xFF) -and ($bytes[1] -eq 0xFE)) -or
            (($bytes[0] -eq 0xFE) -and ($bytes[1] -eq 0xFF))
        )
        if (($bytes -contains 0) -and -not $hasUtf16Bom) {
            continue
        }
        $lineNumber = 0
        $reader = New-Object IO.StreamReader($fullPath, $true)
        try {
            while (-not $reader.EndOfStream) {
                $line = $reader.ReadLine()
                $lineNumber++
                if ($regex.IsMatch($line)) {
                    $matches.Add("CONTENT ${displayPath}:${lineNumber}: $line")
                }
            }
        }
        finally {
            $reader.Dispose()
        }
    }

    if ($matches.Count -gt 0) {
        foreach ($match in $matches) {
            Write-Output $match
        }
        Write-Output "Found $($matches.Count) prohibited token match(es) in tracked paths or content."
        exit 1
    }

    Write-Output "No prohibited token matches in $($trackedFiles.Count) tracked file(s)."
    exit 0
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 2
}
