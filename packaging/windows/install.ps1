$ErrorActionPreference = 'Stop'
$Source = $PSScriptRoot
$Target = Join-Path $env:LOCALAPPDATA 'DataStatsWorkbench'
$TargetFull = [System.IO.Path]::GetFullPath($Target)
$SourceFull = [System.IO.Path]::GetFullPath($Source)
$same = $SourceFull.TrimEnd('\') -ieq $TargetFull.TrimEnd('\')

# 如果工作台正在运行，文件会被锁定，先提示用户从「设置 → 退出工作台」关闭。
$running = @()
try {
    $running = Get-Process python,pythonw -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and ($_.Path.StartsWith($SourceFull, [System.StringComparison]::OrdinalIgnoreCase) -or
                      $_.Path.StartsWith($TargetFull, [System.StringComparison]::OrdinalIgnoreCase))
    }
} catch { }
if ($running.Count -gt 0) {
    Write-Host '工作台正在运行，暂时不能更新程序文件。' -ForegroundColor Yellow
    Write-Host '请先在「设置与数据安全 → 退出工作台」关闭程序，再重新运行本安装脚本。'
    Read-Host '按回车键退出'
    exit 1
}

if (-not $same) {
    Write-Host '正在安装数据统计工作台…' -ForegroundColor Cyan
    New-Item -ItemType Directory -Path $TargetFull -Force | Out-Null
    $exclude = Join-Path $SourceFull 'data'
    & robocopy $SourceFull $TargetFull /E /XD $exclude /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "复制程序文件失败（robocopy $LASTEXITCODE）。请关闭正在运行的工作台后重试。"
    }
} else {
    Write-Host '已在安装目录中，更新快捷方式。' -ForegroundColor Cyan
}

$dataDir = Join-Path $TargetFull 'data'
New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
$launcher = Join-Path $TargetFull 'Start-DataStats.vbs'
$icon = Join-Path $TargetFull 'DataStats.ico'
$wscript = Join-Path $env:WINDIR 'System32\wscript.exe'
$shortcutArgs = '"' + $launcher + '"'

$wsh = New-Object -ComObject WScript.Shell
function New-AppShortcut($Path) {
    $shortcut = $wsh.CreateShortcut($Path)
    $shortcut.TargetPath = $wscript
    $shortcut.Arguments = $shortcutArgs
    $shortcut.WorkingDirectory = $TargetFull
    if (Test-Path $icon) { $shortcut.IconLocation = $icon }
    $shortcut.Description = '数据统计工作台 · 本地离线'
    $shortcut.Save()
}

$desktop = [Environment]::GetFolderPath('Desktop')
New-AppShortcut (Join-Path $desktop '数据统计工作台.lnk')
$programs = [Environment]::GetFolderPath('Programs')
$menuFolder = Join-Path $programs '数据统计工作台'
New-Item -ItemType Directory -Path $menuFolder -Force | Out-Null
New-AppShortcut (Join-Path $menuFolder '数据统计工作台.lnk')

$readmeTarget = Join-Path $menuFolder '使用说明.txt'
$readmeSource = Join-Path $TargetFull 'README.txt'
if (Test-Path $readmeSource) { Copy-Item $readmeSource $readmeTarget -Force }

Write-Host ''
Write-Host '安装完成。' -ForegroundColor Green
Write-Host "程序位置：$TargetFull"
Write-Host "数据位置：$dataDir"
Write-Host '桌面和开始菜单已创建快捷方式。'
Write-Host ''
Start-Process -FilePath $wscript -ArgumentList $shortcutArgs
