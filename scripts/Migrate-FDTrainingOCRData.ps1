[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RenameMap,

    [Parameter(Mandatory = $true)]
    [string]$OperationalRoot,

    [Parameter(Mandatory = $true)]
    [string]$BackupRoot,

    [string]$ReportPath,

    [switch]$WhatIf
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$startedAt = [DateTime]::UtcNow

function Get-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [IO.Path]::GetFullPath($Path)
}

function Test-PathWithin {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$Root
    )
    $candidateFull = Get-FullPath $Candidate
    $rootFull = (Get-FullPath $Root).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    if ([string]::Equals($candidateFull, $rootFull, [StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    $prefix = $rootFull + [IO.Path]::DirectorySeparatorChar
    return $candidateFull.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
}

function Test-ExcludedPath {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][object[]]$ExcludedRoots
    )
    foreach ($excludedRoot in $ExcludedRoots) {
        if (Test-PathWithin -Candidate $Candidate -Root ([string]$excludedRoot)) {
            return $true
        }
    }
    return $false
}

function Get-CanonicalJson {
    param($Value)
    return ($Value | ConvertTo-Json -Depth 100 -Compress)
}

function Convert-StructuredValue {
    param(
        $Value,
        [Parameter(Mandatory = $true)][hashtable]$Renames,
        [Parameter(Mandatory = $true)][ref]$RenameCount
    )

    if ($null -eq $Value) {
        return $null
    }

    if (($Value -is [System.Collections.IEnumerable]) -and
        -not ($Value -is [string]) -and
        -not ($Value -is [System.Collections.IDictionary]) -and
        -not ($Value -is [pscustomobject])) {
        $items = @()
        foreach ($item in $Value) {
            $items += ,(Convert-StructuredValue -Value $item -Renames $Renames -RenameCount $RenameCount)
        }
        return ,$items
    }

    $properties = @()
    if ($Value -is [System.Collections.IDictionary]) {
        foreach ($key in $Value.Keys) {
            $properties += [pscustomobject]@{ Name = [string]$key; Value = $Value[$key] }
        }
    }
    elseif ($Value -is [pscustomobject]) {
        $properties = @($Value.PSObject.Properties | ForEach-Object {
            [pscustomobject]@{ Name = $_.Name; Value = $_.Value }
        })
    }
    else {
        return $Value
    }

    $byName = @{}
    foreach ($property in $properties) {
        $byName[$property.Name] = $property.Value
    }

    foreach ($property in $properties) {
        if ($Renames.ContainsKey($property.Name)) {
            $destinationName = [string]$Renames[$property.Name]
            if ($byName.ContainsKey($destinationName)) {
                $oldJson = Get-CanonicalJson $property.Value
                $newJson = Get-CanonicalJson $byName[$destinationName]
                if (-not [string]::Equals($oldJson, $newJson, [StringComparison]::Ordinal)) {
                    throw "PROPERTY_CONFLICT: '$($property.Name)' and '$destinationName' have different values."
                }
            }
        }
    }

    $converted = [ordered]@{}
    foreach ($property in $properties) {
        $sourceName = $property.Name
        $destinationName = $sourceName
        if ($Renames.ContainsKey($sourceName)) {
            $destinationName = [string]$Renames[$sourceName]
            if ($byName.ContainsKey($destinationName)) {
                $RenameCount.Value++
                continue
            }
            $RenameCount.Value++
        }
        $converted[$destinationName] = Convert-StructuredValue -Value $property.Value -Renames $Renames -RenameCount $RenameCount
    }
    return $converted
}

function Convert-JsonFilePlan {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][ValidateSet('json', 'jsonl')][string]$Format,
        [Parameter(Mandatory = $true)][hashtable]$Renames
    )

    $raw = [IO.File]::ReadAllText($Path)
    $count = 0
    $countRef = [ref]$count
    if ($Format -eq 'json') {
        if ([string]::IsNullOrWhiteSpace($raw)) {
            throw "JSON target is empty: $Path"
        }
        $parsed = $raw | ConvertFrom-Json
        $converted = Convert-StructuredValue -Value $parsed -Renames $Renames -RenameCount $countRef
        $output = ($converted | ConvertTo-Json -Depth 100) + [Environment]::NewLine
    }
    else {
        $outputLines = New-Object System.Collections.Generic.List[string]
        $lineNumber = 0
        foreach ($line in [IO.File]::ReadAllLines($Path)) {
            $lineNumber++
            if ([string]::IsNullOrWhiteSpace($line)) {
                continue
            }
            try {
                $parsed = $line | ConvertFrom-Json
            }
            catch {
                throw "Invalid JSONL at ${Path}:$lineNumber - $($_.Exception.Message)"
            }
            $converted = Convert-StructuredValue -Value $parsed -Renames $Renames -RenameCount $countRef
            $outputLines.Add(($converted | ConvertTo-Json -Depth 100 -Compress))
        }
        $output = if ($outputLines.Count -gt 0) {
            ([string]::Join([Environment]::NewLine, $outputLines)) + [Environment]::NewLine
        }
        else {
            ''
        }
    }

    return [pscustomobject]@{
        type = 'json_write'
        path = $Path
        format = $Format
        changed = ($count -gt 0)
        property_count = $count
        output = $output
    }
}

function Test-FilesEqual {
    param(
        [Parameter(Mandatory = $true)][string]$Left,
        [Parameter(Mandatory = $true)][string]$Right
    )
    if ((Get-Item -LiteralPath $Left).Length -ne (Get-Item -LiteralPath $Right).Length) {
        return $false
    }
    $leftHash = Get-FileSha256 $Left
    $rightHash = Get-FileSha256 $Right
    return [string]::Equals($leftHash, $rightHash, [StringComparison]::OrdinalIgnoreCase)
}

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $stream = [IO.File]::OpenRead($Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-', '')
    }
    finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Get-DirectoryManifest {
    param([Parameter(Mandatory = $true)][string]$Path)
    $root = (Get-FullPath $Path).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $manifest = @()
    foreach ($file in Get-ChildItem -LiteralPath $root -File -Recurse | Sort-Object FullName) {
        $relative = $file.FullName.Substring($root.Length).TrimStart([IO.Path]::DirectorySeparatorChar)
        $manifest += "$relative|$(Get-FileSha256 $file.FullName)"
    }
    return [string]::Join("`n", $manifest)
}

function Save-Report {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Report,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $parent = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($parent)) {
        [IO.Directory]::CreateDirectory($parent) | Out-Null
    }
    $Report.completed_at = [DateTime]::UtcNow.ToString('o')
    [IO.File]::WriteAllText($Path, (($Report | ConvertTo-Json -Depth 100) + [Environment]::NewLine), $utf8NoBom)
}

function Copy-RecoveryItem {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$DestinationRoot
    )
    $rootFull = (Get-FullPath $Root).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $sourceFull = Get-FullPath $Source
    $relative = $sourceFull.Substring($rootFull.Length).TrimStart([IO.Path]::DirectorySeparatorChar)
    $destination = Join-Path $DestinationRoot $relative
    if (Test-Path -LiteralPath $Source -PathType Container) {
        $destinationParent = Split-Path -Parent $destination
        [IO.Directory]::CreateDirectory($destinationParent) | Out-Null
        Copy-Item -LiteralPath $Source -Destination $destinationParent -Recurse -Force -ErrorAction Stop
    }
    else {
        [IO.Directory]::CreateDirectory((Split-Path -Parent $destination)) | Out-Null
        Copy-Item -LiteralPath $Source -Destination $destination -Force -ErrorAction Stop
    }
}

$operationalRootFull = Get-FullPath $OperationalRoot
$backupRootFull = Get-FullPath $BackupRoot
if ([string]::IsNullOrWhiteSpace($ReportPath)) {
    $ReportPath = Join-Path $operationalRootFull ('FDTrainingOCR-Migration-Reports\migration-{0}.json' -f $startedAt.ToString('yyyyMMdd-HHmmssfff'))
}
$reportPathFull = Get-FullPath $ReportPath

$report = [ordered]@{
    schema_version = 1
    started_at = $startedAt.ToString('o')
    completed_at = $null
    mode = if ($WhatIf) { 'what_if' } else { 'commit' }
    backup_path = $null
    files_examined = 0
    files_changed = 0
    files_moved = 0
    properties_renamed = 0
    conflicts = @()
    errors = @()
    skipped = @()
    status = 'created'
}

try {
    if (-not (Test-Path -LiteralPath $RenameMap -PathType Leaf)) {
        throw "Rename map does not exist: $RenameMap"
    }
    if (-not (Test-Path -LiteralPath $operationalRootFull -PathType Container)) {
        throw "Operational root does not exist: $operationalRootFull"
    }
    if (-not (Test-PathWithin -Candidate $backupRootFull -Root $operationalRootFull)) {
        throw "Backup root is outside the operational root: $backupRootFull"
    }
    if ([string]::Equals($backupRootFull, $operationalRootFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Backup root cannot be the operational root.'
    }
    if (-not (Test-PathWithin -Candidate $reportPathFull -Root $operationalRootFull)) {
        throw "Report path is outside the operational root: $reportPathFull"
    }

    try {
        $map = [IO.File]::ReadAllText((Get-FullPath $RenameMap)) | ConvertFrom-Json
    }
    catch {
        throw "Rename map is not valid JSON: $($_.Exception.Message)"
    }
    if ($null -eq $map.schema_version -or [int]$map.schema_version -ne 1) {
        throw "Unsupported rename-map schema_version; expected 1."
    }
    foreach ($requiredProperty in @('property_renames', 'json_targets', 'file_moves', 'directory_moves', 'excluded_roots')) {
        if ($null -eq $map.PSObject.Properties[$requiredProperty]) {
            throw "Rename map is missing '$requiredProperty'."
        }
    }

    $renames = New-Object System.Collections.Hashtable ([StringComparer]::Ordinal)
    foreach ($property in $map.property_renames.PSObject.Properties) {
        $oldName = [string]$property.Name
        $newName = [string]$property.Value
        if ([string]::IsNullOrWhiteSpace($oldName) -or [string]::IsNullOrWhiteSpace($newName) -or $oldName -eq $newName) {
            throw "Property rename entries require different non-empty names."
        }
        if ($renames.ContainsValue($newName)) {
            throw "Multiple property renames target '$newName'."
        }
        $renames[$oldName] = $newName
    }

    $excludedRoots = @($map.excluded_roots | ForEach-Object { Get-FullPath ([string]$_) })
    if (-not ($excludedRoots | Where-Object { [string]::Equals($_, $backupRootFull, [StringComparison]::OrdinalIgnoreCase) })) {
        throw "Backup root must appear in excluded_roots."
    }
    foreach ($excludedRoot in $excludedRoots) {
        if (-not (Test-PathWithin -Candidate $excludedRoot -Root $operationalRootFull)) {
            throw "Excluded root is outside the operational root: $excludedRoot"
        }
        if ([string]::Equals($excludedRoot, $operationalRootFull, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'An excluded root cannot be the operational root.'
        }
    }

    $report.status = 'preflight'
    $plan = New-Object System.Collections.Generic.List[object]
    $seenJsonFiles = @{}

    foreach ($target in @($map.json_targets)) {
        $targetPath = Get-FullPath ([string]$target.path)
        if (-not (Test-PathWithin -Candidate $targetPath -Root $operationalRootFull)) {
            throw "JSON target is outside the operational root: $targetPath"
        }
        if (Test-ExcludedPath -Candidate $targetPath -ExcludedRoots $excludedRoots) {
            throw "JSON target is inside an excluded root: $targetPath"
        }
        $kind = [string]$target.kind
        $required = [bool]$target.required
        $format = [string]$target.format
        if ($format -notin @('json', 'jsonl')) {
            throw "Unsupported target format '$format' for $targetPath"
        }

        $files = @()
        if ($kind -eq 'file') {
            if (Test-Path -LiteralPath $targetPath -PathType Leaf) {
                $files = @($targetPath)
            }
            elseif ($required) {
                throw "Required JSON target does not exist: $targetPath"
            }
            else {
                $report.skipped += "Missing optional JSON target: $targetPath"
            }
        }
        elseif ($kind -eq 'directory') {
            if ([string]::Equals($targetPath, $operationalRootFull, [StringComparison]::OrdinalIgnoreCase)) {
                throw "A directory target cannot scan the bare operational root: $targetPath"
            }
            $pattern = [string]$target.pattern
            if ($pattern -notin @('*.json', '*.jsonl')) {
                throw "Directory target pattern must be '*.json' or '*.jsonl': $targetPath"
            }
            if (Test-Path -LiteralPath $targetPath -PathType Container) {
                $recurse = $false
                if ($null -ne $target.PSObject.Properties['recursive']) {
                    $recurse = [bool]$target.recursive
                }
                if ($recurse) {
                    $files = @(Get-ChildItem -LiteralPath $targetPath -File -Filter $pattern -Recurse | ForEach-Object FullName)
                }
                else {
                    $files = @(Get-ChildItem -LiteralPath $targetPath -File -Filter $pattern | ForEach-Object FullName)
                }
            }
            elseif ($required) {
                throw "Required JSON target directory does not exist: $targetPath"
            }
            else {
                $report.skipped += "Missing optional JSON target directory: $targetPath"
            }
        }
        else {
            throw "JSON target kind must be 'file' or 'directory': $targetPath"
        }

        foreach ($file in $files) {
            $fileFull = Get-FullPath $file
            if (Test-ExcludedPath -Candidate $fileFull -ExcludedRoots $excludedRoots) {
                throw "Enumerated JSON file is inside an excluded root: $fileFull"
            }
            if ($seenJsonFiles.ContainsKey($fileFull)) {
                continue
            }
            $seenJsonFiles[$fileFull] = $true
            $entry = Convert-JsonFilePlan -Path $fileFull -Format $format -Renames $renames
            $plan.Add($entry)
            $report.files_examined++
            if ($entry.changed) {
                $report.files_changed++
                $report.properties_renamed += $entry.property_count
            }
        }
    }

    $moveDestinations = @{}
    foreach ($moveKind in @('file_moves', 'directory_moves')) {
        foreach ($move in @($map.$moveKind)) {
            $source = Get-FullPath ([string]$move.source)
            $destination = Get-FullPath ([string]$move.destination)
            $required = [bool]$move.required
            foreach ($candidate in @($source, $destination)) {
                if (-not (Test-PathWithin -Candidate $candidate -Root $operationalRootFull)) {
                    throw "Move path is outside the operational root: $candidate"
                }
                if ([string]::Equals($candidate, $operationalRootFull, [StringComparison]::OrdinalIgnoreCase)) {
                    throw "A move path cannot be the bare operational root: $candidate"
                }
                if (Test-ExcludedPath -Candidate $candidate -ExcludedRoots $excludedRoots) {
                    throw "Move path is inside an excluded root: $candidate"
                }
            }
            if ([string]::Equals($source, $destination, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Move source and destination are equal: $source"
            }
            if ($moveDestinations.ContainsKey($destination)) {
                throw "Multiple move sources target the same destination: $destination"
            }
            $moveDestinations[$destination] = $true

            $isDirectory = $moveKind -eq 'directory_moves'
            $sourceExists = Test-Path -LiteralPath $source -PathType $(if ($isDirectory) { 'Container' } else { 'Leaf' })
            $destinationExists = Test-Path -LiteralPath $destination -PathType $(if ($isDirectory) { 'Container' } else { 'Leaf' })

            if ($sourceExists -and $destinationExists) {
                $equal = if ($isDirectory) {
                    (Get-DirectoryManifest $source) -eq (Get-DirectoryManifest $destination)
                }
                else {
                    Test-FilesEqual -Left $source -Right $destination
                }
                if (-not $equal) {
                    $report.conflicts += [ordered]@{
                        type = 'destination_exists'
                        source = $source
                        destination = $destination
                    }
                    continue
                }
                $plan.Add([pscustomobject]@{
                    type = if ($isDirectory) { 'directory_cleanup' } else { 'file_cleanup' }
                    source = $source
                    destination = $destination
                })
                $report.files_moved++
            }
            elseif ($sourceExists) {
                $plan.Add([pscustomobject]@{
                    type = if ($isDirectory) { 'directory_move' } else { 'file_move' }
                    source = $source
                    destination = $destination
                })
                $report.files_moved++
            }
            elseif ($destinationExists) {
                $report.skipped += "Already moved: $destination"
            }
            elseif ($required) {
                throw "Required move source and destination are both missing: $source"
            }
            else {
                $report.skipped += "Missing optional move source: $source"
            }
        }
    }

    if ($report.conflicts.Count -gt 0) {
        $report.status = 'conflict'
        Save-Report -Report $report -Path $reportPathFull
        Write-Output "Migration preflight found $($report.conflicts.Count) conflict(s)."
        exit 3
    }

    $mutationPlan = @($plan | Where-Object {
        $_.type -ne 'json_write' -or $_.changed
    })
    if ($mutationPlan.Count -eq 0) {
        $report.status = 'already_migrated'
        Save-Report -Report $report -Path $reportPathFull
        Write-Output 'Migration is already complete; no changes are required.'
        exit 0
    }

    if ($WhatIf) {
        $report.status = 'ready'
        Save-Report -Report $report -Path $reportPathFull
        Write-Output "Migration dry run is ready: $($mutationPlan.Count) planned operation(s)."
        exit 0
    }

    $backupPath = Join-Path $backupRootFull ('{0}-neutral-integration-migration' -f [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmssfff'))
    try {
        [IO.Directory]::CreateDirectory($backupPath) | Out-Null
        $backedUp = @{}
        foreach ($operation in $mutationPlan) {
            $sourcePath = if ($operation.type -eq 'json_write') { $operation.path } else { $operation.source }
            if ((Test-Path -LiteralPath $sourcePath) -and -not $backedUp.ContainsKey($sourcePath)) {
                Copy-RecoveryItem -Source $sourcePath -Root $operationalRootFull -DestinationRoot $backupPath
                $backedUp[$sourcePath] = $true
            }
        }
        $report.backup_path = $backupPath
        $report.status = 'backed_up'
        Save-Report -Report $report -Path $reportPathFull
    }
    catch {
        $report.errors += "Backup creation failed: $($_.Exception.Message)"
        $report.status = 'failed'
        Save-Report -Report $report -Path $reportPathFull
        [Console]::Error.WriteLine($report.errors[-1])
        exit 4
    }

    try {
        foreach ($operation in $mutationPlan) {
            switch ($operation.type) {
                'json_write' {
                    $temporary = "$($operation.path).migration-$([Guid]::NewGuid().ToString('N')).tmp"
                    $replaceBackup = "$($operation.path).migration-$([Guid]::NewGuid().ToString('N')).bak"
                    [IO.File]::WriteAllText($temporary, [string]$operation.output, $utf8NoBom)
                    try {
                        [IO.File]::Replace($temporary, $operation.path, $replaceBackup)
                        Remove-Item -LiteralPath $replaceBackup -Force
                    }
                    catch [PlatformNotSupportedException] {
                        Move-Item -LiteralPath $temporary -Destination $operation.path -Force
                    }
                    finally {
                        if (Test-Path -LiteralPath $temporary) {
                            Remove-Item -LiteralPath $temporary -Force
                        }
                        if (Test-Path -LiteralPath $replaceBackup) {
                            Remove-Item -LiteralPath $replaceBackup -Force
                        }
                    }
                }
                'file_move' {
                    [IO.Directory]::CreateDirectory((Split-Path -Parent $operation.destination)) | Out-Null
                    Move-Item -LiteralPath $operation.source -Destination $operation.destination
                }
                'directory_move' {
                    [IO.Directory]::CreateDirectory((Split-Path -Parent $operation.destination)) | Out-Null
                    Move-Item -LiteralPath $operation.source -Destination $operation.destination
                }
                'file_cleanup' {
                    Remove-Item -LiteralPath $operation.source -Force
                }
                'directory_cleanup' {
                    Remove-Item -LiteralPath $operation.source -Recurse -Force
                }
            }
        }
        $report.status = 'completed'
        Save-Report -Report $report -Path $reportPathFull
        Write-Output "Migration completed with recovery snapshot: $backupPath"
        exit 0
    }
    catch {
        $report.errors += "Migration failed after backup: $($_.Exception.Message)"
        $report.errors += "Restore active files from: $backupPath"
        $report.status = 'failed'
        Save-Report -Report $report -Path $reportPathFull
        [Console]::Error.WriteLine($report.errors[-2])
        [Console]::Error.WriteLine($report.errors[-1])
        exit 5
    }
}
catch {
    $message = $_.Exception.Message
    if ($message.StartsWith('PROPERTY_CONFLICT:', [StringComparison]::Ordinal)) {
        $report.conflicts += [ordered]@{ type = 'property_conflict'; message = $message }
        $report.status = 'conflict'
        Save-Report -Report $report -Path $reportPathFull
        [Console]::Error.WriteLine($message)
        exit 3
    }
    $report.errors += $message
    $report.status = 'failed'
    Save-Report -Report $report -Path $reportPathFull
    [Console]::Error.WriteLine($message)
    exit 2
}
