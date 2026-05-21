#Requires -Version 5.1
# Run this once to create the desktop shortcut with custom icon.

$ErrorActionPreference = "Stop"

$ScriptDir    = $PSScriptRoot
$LauncherPath = Join-Path $ScriptDir "launch_scanner.ps1"
$RepoRoot     = Split-Path -Parent $ScriptDir
$IconPath     = Join-Path $ScriptDir "scanner_icon.ico"
$Desktop      = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "NSE Pattern Finder.lnk"

# Build custom candlestick icon using System.Drawing
try {
    Add-Type -AssemblyName System.Drawing

    $sz  = 256
    $bmp = New-Object System.Drawing.Bitmap($sz, $sz)
    $g   = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode     = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic

    # Background
    $g.Clear([System.Drawing.Color]::FromArgb(12, 10, 10))

    # Rounded border rect (use explicit ints to avoid PowerShell struct arithmetic quirks)
    $bgBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(22, 18, 14))
    $bgPen   = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(255, 72, 0), 6)
    $path    = New-Object System.Drawing.Drawing2D.GraphicsPath
    $r       = 24; $r2 = $r * 2
    $rx = 8; $ry = 8; $rw = $sz - 16; $rh = $sz - 16
    $rRight  = $rx + $rw   # 248
    $rBottom = $ry + $rh   # 248
    $path.AddArc($rx,            $ry,             $r2, $r2, 180, 90)
    $path.AddArc($rRight - $r2,  $ry,             $r2, $r2, 270, 90)
    $path.AddArc($rRight - $r2,  $rBottom - $r2,  $r2, $r2, 0,   90)
    $path.AddArc($rx,            $rBottom - $r2,  $r2, $r2, 90,  90)
    $path.CloseFigure()
    $g.FillPath($bgBrush, $path)
    $g.DrawPath($bgPen, $path)
    $bgBrush.Dispose(); $bgPen.Dispose(); $path.Dispose()

    # Candle data: xCenter, bodyHigh, bodyLow, wickHigh, wickLow, isGreen
    $green  = [System.Drawing.Color]::FromArgb(34,  197, 94)
    $red    = [System.Drawing.Color]::FromArgb(239, 68,  68)
    $orange = [System.Drawing.Color]::FromArgb(255, 72,  0)

    $candles = @(
        @(44,  168, 195, 158, 205, $false),
        @(84,  132, 162, 120, 172, $true),
        @(124, 100, 138, 88,  150, $true),
        @(164, 110, 145, 98,  158, $false),
        @(204, 62,  100, 50,  112, $true)
    )
    $cw = 26

    foreach ($c in $candles) {
        $cx    = $c[0]; $bTop = $c[1]; $bBot = $c[2]
        $wTop  = $c[3]; $wBot = $c[4]; $up   = $c[5]
        $col   = if ($up) { $green } else { $red }
        $brush = New-Object System.Drawing.SolidBrush($col)
        $pen   = New-Object System.Drawing.Pen($col, 3)
        $g.DrawLine($pen, $cx, $wTop, $cx, $bTop)
        $g.DrawLine($pen, $cx, $bBot, $cx, $wBot)
        $bx = $cx - [int]($cw / 2)
        $bh = [Math]::Abs($bBot - $bTop)
        $g.FillRectangle($brush, $bx, $bTop, $cw, $bh)
        $brush.Dispose(); $pen.Dispose()
    }

    # Orange baseline
    $basePen = New-Object System.Drawing.Pen($orange, 5)
    $g.DrawLine($basePen, 28, 218, 228, 218)
    $basePen.Dispose()

    # Dashed uptrend line
    $trendPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(140, 255, 200, 80), 2)
    $trendPen.DashStyle = [System.Drawing.Drawing2D.DashStyle]::Dash
    $g.DrawLine($trendPen, 28, 195, 228, 62)
    $trendPen.Dispose()

    $g.Dispose()

    # Save as PNG-in-ICO (Vista+ format: ICO header + raw PNG bytes)
    $pngStream = New-Object System.IO.MemoryStream
    $bmp.Save($pngStream, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    $pngBytes = $pngStream.ToArray()
    $pngStream.Dispose()

    $icoStream = New-Object System.IO.MemoryStream
    $w = New-Object System.IO.BinaryWriter($icoStream)
    $w.Write([uint16]0)                    # reserved
    $w.Write([uint16]1)                    # type: ICO
    $w.Write([uint16]1)                    # image count
    $w.Write([byte]0)                      # width  (0 = 256)
    $w.Write([byte]0)                      # height (0 = 256)
    $w.Write([byte]0)                      # color count
    $w.Write([byte]0)                      # reserved
    $w.Write([uint16]1)                    # planes
    $w.Write([uint16]32)                   # bit depth
    $w.Write([uint32]$pngBytes.Length)     # data size
    $w.Write([uint32]22)                   # data offset (6 + 16 = 22)
    $w.Write($pngBytes)
    $w.Flush()

    [System.IO.File]::WriteAllBytes($IconPath, $icoStream.ToArray())
    $icoStream.Dispose()

    Write-Host "Icon created: $IconPath" -ForegroundColor Green
} catch {
    Write-Host "Custom icon skipped: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "Using built-in system icon." -ForegroundColor DarkGray
    $IconPath = "$env:SystemRoot\System32\imageres.dll,170"
}

# Create .lnk shortcut
$shell = New-Object -ComObject WScript.Shell
$lnk   = $shell.CreateShortcut($ShortcutPath)
$lnk.TargetPath       = "powershell.exe"
$lnk.Arguments        = "-ExecutionPolicy Bypass -NoExit -File `"$LauncherPath`""
$lnk.WorkingDirectory = $RepoRoot
$lnk.IconLocation     = $IconPath
$lnk.Description      = "Run NSE Pattern Finder and open dashboard"
$lnk.Save()

Write-Host ""
Write-Host "Shortcut created on Desktop:" -ForegroundColor Green
Write-Host "  $ShortcutPath" -ForegroundColor Cyan
Write-Host ""
Write-Host "Double-click it to run the scanner." -ForegroundColor Yellow
