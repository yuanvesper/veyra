# Veyra install script (PowerShell) - install / update / uninstall the mod-issue-triage Skill.
# Safety: absolute destination only, path-escape rejection, reparse-point detection,
# ownership marker validation, exclusive lock file, exclusive temp/backup/trash creation,
# repository-overlap rejection, internal-link scanning, and transactional replace with rollback.
#
# Note: these checks mitigate TOCTOU races; they are not a cryptographic security boundary.
#
# Usage:
#   ./scripts/install.ps1 -Destination "C:\path\to\skills"
#   ./scripts/install.ps1 -Destination "C:\path\to\skills" -Update
#   ./scripts/install.ps1 -Destination "C:\path\to\skills" -Uninstall
#
# The Destination may be an existing directory or a path whose parents exist; it is
# created only after its nearest existing parent has been checked for reparse points.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Destination,

    [switch]$Update,

    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

$script:HasLock = $false
$script:LockStream = $null
$script:LockPath = $null
$script:RepoRoot = Split-Path -Parent $PSScriptRoot
$script:SourceSkill = Join-Path $script:RepoRoot 'skills\mod-issue-triage'
$script:SkillName = 'mod-issue-triage'
$script:RequiredFiles = @('SKILL.md', 'references\report-template.md', 'evals\README.md')
$script:MarkerContent = '{"owner":"veyra","skill_name":"mod-issue-triage","schema_version":1}'

function Write-Step([string]$Message) {
    Write-Host "[veyra] $Message"
}

function Write-Fail([string]$Message) {
    [Console]::Error.WriteLine("[veyra][错误] $Message")
}

# --- Path safety -----------------------------------------------------------

function Resolve-Destination([string]$Raw) {
    if ([string]::IsNullOrWhiteSpace($Raw)) {
        throw 'Destination 不能为空'
    }
    if ($Raw -match '\.\.') {
        $rawComponents = $Raw -split '[\\/]+'
        if ($rawComponents -contains '..') {
            throw '路径包含 ..，已拒绝'
        }
    }
    if ($Raw -match '^[A-Za-z]:$') {
        throw 'Destination 必须是完整绝对路径（不能只写盘符，如 C:）'
    }
    if ($Raw -match '^[A-Za-z]:[^\\/]') {
        throw 'Destination 必须是完整绝对路径（盘符后缺少分隔符，如 C:folder）'
    }
    if ($Raw -match '^[\\/](?![\\/])') {
        throw 'Destination 必须是完整绝对路径（不能是根相对路径，如 \folder）'
    }
    if ($Raw -match '^[\\/]{2}') {
        if ($Raw -notmatch '^[\\/]{2}[^\\/]+[\\/][^\\/]+') {
            throw 'Destination 的 UNC 路径不完整（需要 \\server\share 形式）'
        }
    } elseif (-not [System.IO.Path]::IsPathRooted($Raw)) {
        throw 'Destination 必须是绝对路径'
    }
    $full = [System.IO.Path]::GetFullPath($Raw)
    $root = [System.IO.Path]::GetPathRoot($full)
    if ([string]::Equals($full.TrimEnd('\', '/'), $root.TrimEnd('\', '/'), [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝敏感目录：$full"
    }

    Assert-NotOverlappingRepository $full

    if (Test-Path -LiteralPath $full) {
        if (-not (Test-Path -LiteralPath $full -PathType Container)) {
            throw "Destination 已存在但不是目录：$full"
        }
    } else {
        $probe = $full
        while (-not (Test-Path -LiteralPath $probe)) {
            $parent = Split-Path -Parent $probe
            if ([string]::IsNullOrEmpty($parent) -or $parent -eq $probe) {
                throw "无法解析 Destination 的已存在父目录：$full"
            }
            $probe = $parent
        }
        Assert-NoReparseChain $probe
        New-Item -ItemType Directory -Path $full -Force | Out-Null
    }

    Assert-NoReparseChain $full
    return $full
}

function Split-PathComponents([string]$Path) {
    $full = [System.IO.Path]::GetFullPath($Path)
    $trimmed = $full.TrimEnd('\', '/')
    if ([string]::IsNullOrEmpty($trimmed)) {
        return @()
    }
    return @($trimmed -split '[\\/]+')
}

function Test-ComponentsEqual($Left, $Right) {
    if ($Left.Count -ne $Right.Count) { return $false }
    for ($i = 0; $i -lt $Left.Count; $i++) {
        if (-not [string]::Equals($Left[$i], $Right[$i], [System.StringComparison]::OrdinalIgnoreCase)) {
            return $false
        }
    }
    return $true
}

function Test-ComponentsPrefix($Prefix, $Full) {
    if ($Prefix.Count -gt $Full.Count) { return $false }
    for ($i = 0; $i -lt $Prefix.Count; $i++) {
        if (-not [string]::Equals($Prefix[$i], $Full[$i], [System.StringComparison]::OrdinalIgnoreCase)) {
            return $false
        }
    }
    return $true
}

function Assert-NotOverlappingRepository([string]$DestFull) {
    $repo = [System.IO.Path]::GetFullPath($script:RepoRoot)
    $source = [System.IO.Path]::GetFullPath($script:SourceSkill)
    $destComponents = Split-PathComponents $DestFull
    $repoPair = [PSCustomObject]@{ Path = $repo; Label = '仓库根目录' }
    $sourcePair = [PSCustomObject]@{ Path = $source; Label = '正式 Skill 源目录' }
    $equalOrder = @($sourcePair, $repoPair)
    $ancestorOrder = @($repoPair, $sourcePair)
    foreach ($pair in $equalOrder) {
        if (Test-ComponentsEqual $destComponents (Split-PathComponents $pair.Path)) {
            throw "Destination 不能等于$($pair.Label)：$DestFull"
        }
    }
    foreach ($pair in $equalOrder) {
        if (Test-ComponentsPrefix (Split-PathComponents $pair.Path) $destComponents) {
            throw "Destination 不能位于$($pair.Label)内部：$DestFull"
        }
    }
    foreach ($pair in $ancestorOrder) {
        if (Test-ComponentsPrefix $destComponents (Split-PathComponents $pair.Path)) {
            throw "Destination 不能是$($pair.Label)的祖先目录：$DestFull"
        }
    }
}

function Test-IsReparsePoint([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) {
        return $false
    }
    return (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
}

function Assert-NoReparsePoint([string]$Path) {
    if (Test-IsReparsePoint $Path) {
        throw "发现符号链接或重解析点：$Path"
    }
}

function Assert-NoReparseChain([string]$FullPath) {
    $current = $FullPath
    while ($true) {
        Assert-NoReparsePoint $current
        $parent = Split-Path -Parent $current
        if ([string]::IsNullOrEmpty($parent) -or $parent -eq $current) {
            break
        }
        $current = $parent
    }
}

# --- Lock file -------------------------------------------------------------

function Acquire-Lock([string]$DestFull) {
    $script:LockPath = Join-Path $DestFull '.veyra-operation.lock'
    if (Test-Path -LiteralPath $script:LockPath) {
        throw "发现锁文件 $($script:LockPath)。可能另一个操作正在运行或上次异常退出；请人工检查后删除，脚本不会自动清理。"
    }
    try {
        $script:LockStream = [System.IO.File]::Open(
            $script:LockPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
        $script:HasLock = $true
    } catch {
        throw "无法获取锁文件（可能并发冲突）：$($_.Exception.Message)"
    }
}

function Release-Lock {
    if ($script:HasLock) {
        try { $script:LockStream.Dispose() } catch { }
        try { Remove-Item -LiteralPath $script:LockPath -Force -ErrorAction Stop } catch { }
        $script:HasLock = $false
    }
}

# --- Skill tree helpers ----------------------------------------------------

function Get-SkillFiles([string]$Root) {
    Assert-NoReparsePoint $Root
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        throw "路径不存在或不是目录：$Root"
    }
    $files = New-Object System.Collections.ArrayList
    $stack = New-Object System.Collections.Stack
    $stack.Push($Root)
    while ($stack.Count -gt 0) {
        $dir = $stack.Pop()
        foreach ($item in (Get-ChildItem -LiteralPath $dir -Force -ErrorAction Stop)) {
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "发现符号链接或重解析点，拒绝操作：$($item.FullName)"
            }
            if ($item.PSIsContainer) {
                $stack.Push($item.FullName)
            } else {
                [void]$files.Add($item)
            }
        }
    }
    return $files
}

function New-ExclusiveDir([string]$Path) {
    try {
        New-Item -ItemType Directory -Path $Path -ErrorAction Stop | Out-Null
    } catch {
        throw "无法独占创建目录（目标已存在或不可创建）：$Path"
    }
}

function Copy-SkillTree([string]$Source, [string]$Target) {
    $files = Get-SkillFiles $Source
    foreach ($file in $files) {
        $relative = $file.FullName.Substring($Source.Length).TrimStart('\')
        $dest = Join-Path $Target $relative
        $parent = Split-Path -Parent $dest
        if (-not (Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        Copy-Item -LiteralPath $file.FullName -Destination $dest -Force
    }
    foreach ($file in $files) {
        $relative = $file.FullName.Substring($Source.Length).TrimStart('\')
        $dest = Join-Path $Target $relative
        if (-not (Test-Path -LiteralPath $dest -PathType Leaf)) {
            throw "复制校验失败，缺少文件：$relative"
        }
        if ((Get-Item -LiteralPath $dest).Length -ne $file.Length) {
            throw "复制校验失败，大小不一致：$relative"
        }
    }
    foreach ($required in $script:RequiredFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $Target $required) -PathType Leaf)) {
            throw "完整性校验失败，缺少 $required"
        }
    }
}

function Remove-TreeNoFollow([string]$Path) {
    Assert-NoReparsePoint $Path
    foreach ($item in (Get-ChildItem -LiteralPath $Path -Force -ErrorAction Stop)) {
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "发现符号链接或重解析点，拒绝继续删除：$($item.FullName)"
        }
        if ($item.PSIsContainer) {
            Remove-TreeNoFollow $item.FullName
        } else {
            Remove-Item -LiteralPath $item.FullName -Force -ErrorAction Stop
        }
    }
    Assert-NoReparsePoint $Path
    Remove-Item -LiteralPath $Path -Force
}

function Write-Marker([string]$SkillDir) {
    $markerPath = Join-Path $SkillDir '.veyra-install.json'
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($markerPath, $script:MarkerContent, $utf8)
    $readBack = [System.IO.File]::ReadAllText($markerPath, [System.Text.Encoding]::UTF8)
    if (-not ($readBack -is [string]) -or -not [string]::Equals($readBack, $script:MarkerContent, [System.StringComparison]::Ordinal)) {
        throw "安装标记写入校验失败（严格比较不一致）：$markerPath"
    }
}

function Assert-SkillDirComplete([string]$SkillDir) {
    if (-not (Test-Path -LiteralPath $SkillDir -PathType Container)) {
        throw "完整性校验失败：目录不存在：$SkillDir"
    }
    foreach ($required in $script:RequiredFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $SkillDir $required) -PathType Leaf)) {
            throw "完整性校验失败，缺少 $required：$SkillDir"
        }
    }
}

function Test-Marker([string]$SkillDir) {
    $markerPath = Join-Path $SkillDir '.veyra-install.json'
    Assert-NoReparsePoint $markerPath
    if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) {
        throw '缺少 .veyra-install.json，拒绝操作'
    }
    $raw = [System.IO.File]::ReadAllText($markerPath, [System.Text.Encoding]::UTF8)
    if (-not ($raw -is [string]) -or -not [string]::Equals($raw, $script:MarkerContent, [System.StringComparison]::Ordinal)) {
        throw '标记文件内容与预期不一致（大小写、首尾空白或格式被修改）'
    }
    try {
        $json = Get-Content -LiteralPath $markerPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        throw '标记文件不是合法 JSON'
    }
    if (-not ($json.owner -is [string]) -or $json.owner -cne 'veyra') {
        throw '标记文件 owner 无效'
    }
    if (-not ($json.skill_name -is [string]) -or $json.skill_name -cne 'mod-issue-triage') {
        throw '标记文件 skill_name 无效'
    }
    $version = $json.schema_version
    $valid = (($version -is [int]) -or ($version -is [long])) -and (-not ($version -is [bool])) -and ($version -eq 1)
    if (-not $valid) {
        throw '标记文件 schema_version 无效'
    }
    return $true
}

function Assert-OperationTargetsSafe([string]$DestFull, [string]$SkillDir, [string[]]$ExtraPaths) {
    Assert-NoReparseChain $DestFull
    Assert-NoReparseChain $SkillDir
    foreach ($path in $ExtraPaths) {
        Assert-NoReparsePoint $path
    }
}

# --- Operations ------------------------------------------------------------

function Install-Veyra([string]$DestFull) {
    $skillDir = Join-Path $DestFull $script:SkillName
    if (Test-Path -LiteralPath $skillDir) {
        throw "目标已存在：$skillDir。如要更新请使用 -Update"
    }
    $tmpDir = Join-Path $DestFull ('.veyra-tmp-' + [guid]::NewGuid().ToString('N'))
    $installedSkill = $false
    New-ExclusiveDir $tmpDir
    try {
        Copy-SkillTree $script:SourceSkill $tmpDir
        Write-Marker $tmpDir
        Assert-OperationTargetsSafe $DestFull $skillDir @($tmpDir)
        if (Test-Path -LiteralPath $skillDir) {
            throw '目标在安装过程中出现，拒绝覆盖'
        }
        Move-Item -LiteralPath $tmpDir -Destination $skillDir
        $installedSkill = $true
        Assert-SkillDirComplete $skillDir
        Write-Step "安装成功：$skillDir"
    } catch {
        if ($installedSkill -and (Test-Path -LiteralPath $skillDir)) {
            try { Remove-TreeNoFollow $skillDir } catch { Write-Warning "半成品安装目录清理失败，保留路径：$skillDir" }
        }
        if (Test-Path -LiteralPath $tmpDir) {
            try { Remove-TreeNoFollow $tmpDir } catch { Write-Warning "临时目录清理失败：$tmpDir" }
        }
        throw
    }
}

function Restore-OriginalVersion([string]$DestFull, [string]$SkillDir, [string]$BackupDir) {
    $backedUp = Join-Path $BackupDir $script:SkillName
    $script:LastRollbackUncertain = $false

    # 回滚目标必须不存在：无论它是文件还是目录，都不得覆盖、合并或移动到其内部。
    if (Test-Path -LiteralPath $SkillDir) {
        throw "回滚目标已存在，拒绝覆盖或合并：$SkillDir。备份保留在：$BackupDir"
    }

    # 备份根节点与内容检查（不能只检查父目录）。
    Assert-NoReparseChain $BackupDir
    Assert-NoReparseChain $backedUp
    if (-not (Test-Path -LiteralPath $backedUp -PathType Container)) {
        throw "回滚前校验失败：未找到原始 Skill 备份：$backedUp"
    }
    $backedUpSkill = Join-Path $backedUp 'SKILL.md'
    Assert-NoReparsePoint $backedUpSkill
    if (-not (Test-Path -LiteralPath $backedUpSkill -PathType Leaf)) {
        throw "回滚前校验失败：备份缺少 SKILL.md：$backedUpSkill"
    }

    # 回滚前重新验证路径安全（允许 $SkillDir 本身不存在）。
    Assert-NoReparseChain $DestFull
    Assert-NoReparseChain $SkillDir
    Get-SkillFiles $backedUp | Out-Null

    $expectedHash = (Get-FileHash -LiteralPath $backedUpSkill -Algorithm SHA256).Hash
    Move-Item -LiteralPath $backedUp -Destination $SkillDir

    try {
        if (-not (Test-Path -LiteralPath $SkillDir -PathType Container)) {
            throw "回滚后校验失败：正式位置未恢复：$SkillDir"
        }
        Get-SkillFiles $SkillDir | Out-Null
        $restoredSkill = Join-Path $SkillDir 'SKILL.md'
        Assert-NoReparsePoint $restoredSkill
        if (-not (Test-Path -LiteralPath $restoredSkill -PathType Leaf)) {
            throw "回滚后校验失败：缺少 SKILL.md：$SkillDir"
        }
        $restoredHash = (Get-FileHash -LiteralPath $restoredSkill -Algorithm SHA256).Hash
        if ($restoredHash -ne $expectedHash) {
            throw "回滚后校验失败：SKILL.md 内容与备份不一致：$SkillDir"
        }
        Assert-SkillDirComplete $SkillDir
    } catch {
        $reason = $_.Exception.Message
        $recovered = $false
        $movedBack = $false
        $recoveryError = $null
        try {
            Assert-NoReparseChain $SkillDir
            Assert-NoReparseChain $BackupDir
            if ((Test-Path -LiteralPath $SkillDir -PathType Container) -and -not (Test-Path -LiteralPath (Join-Path $BackupDir $script:SkillName))) {
                Move-Item -LiteralPath $SkillDir -Destination $BackupDir
                $movedBack = $true

                # 移动回备份后再做完整性复核，移动成功不等于数据完整。
                $restoredRoot = Join-Path $BackupDir $script:SkillName
                Assert-NoReparsePoint $BackupDir
                Assert-NoReparsePoint $restoredRoot
                if (-not (Test-Path -LiteralPath $restoredRoot -PathType Container)) {
                    throw "退回备份后校验失败：备份根不存在：$restoredRoot"
                }
                Get-SkillFiles $restoredRoot | Out-Null
                $restoredSkillFile = Join-Path $restoredRoot 'SKILL.md'
                Assert-NoReparsePoint $restoredSkillFile
                if (-not (Test-Path -LiteralPath $restoredSkillFile -PathType Leaf)) {
                    throw "退回备份后校验失败：缺少 SKILL.md：$restoredSkillFile"
                }
                $movedBackHash = (Get-FileHash -LiteralPath $restoredSkillFile -Algorithm SHA256).Hash
                if ($movedBackHash -ne $expectedHash) {
                    throw "退回备份后校验失败：SHA256 不一致（期望 $expectedHash，实际 $movedBackHash）：$restoredSkillFile"
                }
                Assert-SkillDirComplete $restoredRoot
                $recovered = $true
            }
        } catch {
            $recovered = $false
            $recoveryError = $_.Exception.Message
        }
        if ($recovered) {
            throw "回滚后校验失败，原始版本已退回备份：$BackupDir（原因：$reason）"
        }
        $script:LastRollbackUncertain = $true
        if ($movedBack) {
            throw "回滚后校验失败，已退回备份位置但内容已损坏（不能确认原始版本完整恢复）：$BackupDir（原因：$recoveryError）"
        }
        throw "回滚后校验失败且无法退回备份；原始版本可能位于：$SkillDir 或 $BackupDir（原因：$reason）"
    }

    try {
        if (Test-Path -LiteralPath $BackupDir) {
            Remove-TreeNoFollow $BackupDir
        }
    } catch {
        Write-Warning "空备份目录清理失败（原始版本已回滚）：$BackupDir"
    }
}
function Update-Veyra([string]$DestFull) {
    $skillDir = Join-Path $DestFull $script:SkillName
    if (-not (Test-Path -LiteralPath $skillDir -PathType Container)) {
        throw "未找到已安装的 Skill：$skillDir"
    }
    Assert-NoReparseChain $skillDir
    Get-SkillFiles $skillDir | Out-Null
    if (-not (Test-Marker $skillDir)) { throw '缺少有效标记文件，拒绝更新' }

    $id = [guid]::NewGuid().ToString('N')
    $tmpDir = Join-Path $DestFull ".veyra-tmp-$id"
    $backupDir = Join-Path $DestFull ".veyra-bak-$id"

    New-ExclusiveDir $tmpDir
    try {
        Copy-SkillTree $script:SourceSkill $tmpDir
        Write-Marker $tmpDir

        New-ExclusiveDir $backupDir
        $movedToBackup = $false
        $newInstalled = $false
        try {
            Assert-OperationTargetsSafe $DestFull $skillDir @($tmpDir, $backupDir)
            Get-SkillFiles $skillDir | Out-Null
            Move-Item -LiteralPath $skillDir -Destination $backupDir
            $movedToBackup = $true

            # 原版本已移入备份：从这里到新版本就位之间的任何失败都必须回滚。
            try {
                Assert-OperationTargetsSafe $DestFull $skillDir @($tmpDir)
                Move-Item -LiteralPath $tmpDir -Destination $skillDir
                Assert-SkillDirComplete $skillDir
                $newInstalled = $true
            } catch {
                $failure = $_.Exception.Message
                try {
                    Restore-OriginalVersion $DestFull $skillDir $backupDir
                } catch {
                    $reason = $_.Exception.Message
                    if ($script:LastRollbackUncertain) {
                        throw "替换失败且回滚失败，原始版本位置无法确认：$reason"
                    }
                    throw "替换失败且回滚失败，备份保留在：$backupDir（原因：$reason）"
                }
                throw "替换失败，已回滚到原版本：$failure"
            }

            try {
                Remove-TreeNoFollow $backupDir
                Write-Step "更新成功：$skillDir"
            } catch {
                Write-Warning "备份清理失败（新版本已就位）：$backupDir"
                Write-Step "更新成功（存在残留备份）：$skillDir"
            }
        } catch {
            # 若原版本尚未移动，备份目录为空，可以安全清理。
            if (-not $movedToBackup -and (Test-Path -LiteralPath $backupDir)) {
                try { Remove-TreeNoFollow $backupDir } catch { Write-Warning "备份目录清理失败：$backupDir" }
            }
            throw
        }
    } catch {
        if (Test-Path -LiteralPath $tmpDir) {
            try { Remove-TreeNoFollow $tmpDir } catch { Write-Warning "临时目录清理失败：$tmpDir" }
        }
        throw
    }
}
function Uninstall-Veyra([string]$DestFull) {
    $skillDir = Join-Path $DestFull $script:SkillName
    if (-not (Test-Path -LiteralPath $skillDir -PathType Container)) {
        throw "未找到已安装的 Skill：$skillDir"
    }
    Assert-NoReparseChain $skillDir
    Get-SkillFiles $skillDir | Out-Null
    if (-not (Test-Marker $skillDir)) { throw '缺少有效标记文件，拒绝卸载' }

    $trashDir = Join-Path $DestFull ('.veyra-trash-' + [guid]::NewGuid().ToString('N'))
    New-ExclusiveDir $trashDir
    try {
        Assert-OperationTargetsSafe $DestFull $skillDir @($trashDir)
        Get-SkillFiles $skillDir | Out-Null
        Move-Item -LiteralPath $skillDir -Destination $trashDir
    } catch {
        if (Test-Path -LiteralPath $trashDir) {
            try { Remove-TreeNoFollow $trashDir } catch { Write-Warning "隔离目录清理失败：$trashDir" }
        }
        throw
    }

    try {
        Remove-TreeNoFollow $trashDir
        Write-Step "卸载成功：$skillDir"
    } catch {
        Write-Fail "隔离目录清理失败，保留路径：$trashDir（内容可能不完整）"
        throw "卸载未完全完成：请人工检查 $trashDir"
    }
}

# --- Main ------------------------------------------------------------------

$exitCode = 0
try {
    if ($Update -and $Uninstall) {
        throw '参数冲突：-Update 与 -Uninstall 不能同时使用'
    }
    $destFull = Resolve-Destination $Destination
    Assert-NoReparseChain $script:RepoRoot
    Assert-NoReparseChain $script:SourceSkill
    Acquire-Lock $destFull
    if ($Uninstall) {
        Uninstall-Veyra $destFull
    } elseif ($Update) {
        Update-Veyra $destFull
    } else {
        Install-Veyra $destFull
    }
} catch {
    Write-Fail $_.Exception.Message
    $exitCode = 1
} finally {
    Release-Lock
}
exit $exitCode
