# Maakt de snelkoppeling "PTZ Control" (met icoon) in het Startmenu, en met -Bureaublad ook op het bureaublad.
# Doel is cmd.exe met de starter als argument: zo'n snelkoppeling kun je aan de taakbalk vastmaken.
param([switch]$Bureaublad)
$dir = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $dir 'Start PTZ Control.bat'
$ico = Join-Path $PSScriptRoot 'ptz.ico'
$w = New-Object -ComObject WScript.Shell
$plekken = @([Environment]::GetFolderPath('Programs'))
if ($Bureaublad) { $plekken += [Environment]::GetFolderPath('Desktop') }
foreach ($p in $plekken) {
  $lnk = Join-Path $p 'PTZ Control.lnk'
  $s = $w.CreateShortcut($lnk)
  $s.TargetPath = $env:ComSpec
  $s.Arguments = '/s /c ""' + $bat + '""'
  $s.WorkingDirectory = $dir
  $s.IconLocation = "$ico,0"
  $s.Description = 'PTZ Control starten'
  $s.Save()
  Write-Host "> Snelkoppeling: $lnk"
}
