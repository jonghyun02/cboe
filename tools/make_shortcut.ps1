# Desktop shortcut "CBoE" -> .venv\Scripts\pythonw.exe -m cboe gui (no console window).
#   powershell -ExecutionPolicy Bypass -File tools\make_shortcut.ps1
$root = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = Join-Path $desktop 'CBoE.lnk'
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath = Join-Path $root '.venv\Scripts\pythonw.exe'
$s.Arguments = '-m cboe gui'
$s.WorkingDirectory = $root
$s.IconLocation = (Join-Path $root 'cboe\server\static\cboe.ico') + ',0'
$s.Description = 'CBoE SDH subtitle review assistant'
$s.Save()
Write-Output $lnk
