$ErrorActionPreference = 'Stop'
$compiler = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'Installed .NET Framework compiler not found. No download attempted.' }
& $compiler /nologo /target:winexe /optimize+ /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Web.Extensions.dll /out:DaJoshiResolve.exe /win32icon:DaJoshiResolve.ico ui\Program.cs
if ($LASTEXITCODE -ne 0) { throw 'Launcher compilation failed' }
