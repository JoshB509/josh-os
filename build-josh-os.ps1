$ErrorActionPreference = 'Stop'
$compiler = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'Installed .NET Framework compiler not found.' }
$output = Join-Path $PSScriptRoot 'Josh OS.exe'
& $compiler /nologo /target:winexe /optimize+ /r:System.Windows.Forms.dll /r:System.Web.Extensions.dll "/out:$output" "/win32icon:$PSScriptRoot\JoshOS.ico" "$PSScriptRoot\ui\StudioLauncher.cs"
if ($LASTEXITCODE -ne 0) { throw 'Josh OS launcher compilation failed.' }
