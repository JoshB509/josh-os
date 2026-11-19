# Add one script to Elgato Camera Hub the way Josh does it by hand: Prompter tab, Content Text, Scripts +, name, Add,
# then paste the text into the new script's editor. Camera Hub saves it to its own Texts folder by itself shortly after.
# Used by studio/filming.py, which then checks the saved words and selects the script. Controls are found by their
# Windows UI Automation ids, never by guessed screen positions. Nothing else in Camera Hub is touched, and nothing is
# deleted. Prints one JSON line: {"ok":true} or {"ok":false,"error":"..."}.
param([Parameter(Mandatory=$true)][string]$Name, [Parameter(Mandatory=$true)][string]$TextFile)
$ErrorActionPreference = 'Stop'
$script:raised = @()
function Lower { foreach ($h in $script:raised) { [void][CH]::SetWindowPos($h, [IntPtr](-2), 0, 0, 0, 0, 0x13) } }
function Out($o) { try { Lower } catch {}; $o | ConvertTo-Json -Compress; exit 0 }
try {
  Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Windows.Forms
  Add-Type @"
using System;using System.Text;using System.Runtime.InteropServices;using System.Collections.Generic;
public class CH {
  public delegate bool P(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(P p, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, IntPtr e);
  [DllImport("user32.dll")] public static extern void keybd_event(byte k, byte s, uint f, IntPtr e);
  [StructLayout(LayoutKind.Sequential)] public struct PT { public int X, Y; }
  [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(PT p);
  [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr h, uint f);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool f);
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint f);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
  public static uint Owner(IntPtr h) { uint p; GetWindowThreadProcessId(h, out p); return p; }
  public static uint AtPoint(int x, int y) { var pt = new PT { X = x, Y = y }; var h = GetAncestor(WindowFromPoint(pt), 2); return Owner(h); }
  public static List<IntPtr> Windows(uint pid) { var r = new List<IntPtr>(); EnumWindows((h, l) => { uint p; GetWindowThreadProcessId(h, out p); if (p == pid) r.Add(h); return true; }, IntPtr.Zero); return r; }
  public static string Title(IntPtr h) { var s = new StringBuilder(256); GetWindowText(h, s, 256); return s.ToString(); }
}
"@
  [void][CH]::SetProcessDPIAware()
  $A = [System.Windows.Automation.AutomationElement]
  $Tree = [System.Windows.Automation.TreeScope]
  $proc = Get-Process 'Camera Hub' -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $proc) { Out @{ok=$false; error='Camera Hub is not open.'} }

  function Find($root, $aidEnd, $name = $null) {
    foreach ($e in $root.FindAll($Tree::Descendants, [System.Windows.Automation.Condition]::TrueCondition)) {
      $aid = $e.Current.AutomationId
      if ($aid -and $aid.EndsWith($aidEnd) -and ($name -eq $null -or $e.Current.Name -eq $name)) { return $e }
    }
    return $null
  }
  function FindAll($root, $aidEnd) {
    $out = @()
    foreach ($e in $root.FindAll($Tree::Descendants, [System.Windows.Automation.Condition]::TrueCondition)) {
      if ($e.Current.AutomationId -and $e.Current.AutomationId.EndsWith($aidEnd)) { $out += $e }
    }
    return ,$out
  }
  # Bring a Camera Hub window to the front. Returns whether it really is in front.
  function Front($h) {
    $fg = [CH]::GetForegroundWindow(); $fgThread = 0; $null = [CH]::GetWindowThreadProcessId($fg, [ref]$fgThread)
    $me = [CH]::GetCurrentThreadId()
    if ($fgThread -and $fgThread -ne $me) { [void][CH]::AttachThreadInput($me, $fgThread, $true) }
    [CH]::keybd_event(0x12, 0, 0, [IntPtr]::Zero); [CH]::keybd_event(0x12, 0, 2, [IntPtr]::Zero)
    # Kept on top only while this runs (put back in the finally below), so no other window can take the clicks.
    [void][CH]::SetWindowPos($h, [IntPtr](-1), 0, 0, 0, 0, 0x13); $script:raised += $h
    [void][CH]::BringWindowToTop($h); [void][CH]::SetForegroundWindow($h)
    if ($fgThread -and $fgThread -ne $me) { [void][CH]::AttachThreadInput($me, $fgThread, $false) }
    Start-Sleep -Milliseconds 250
    return ([CH]::Owner([CH]::GetForegroundWindow()) -eq [uint32]$proc.Id)
  }
  function Click($e) {
    $r = $e.Current.BoundingRectangle
    if ($r.Width -le 0) { throw "A Camera Hub control isn't on screen." }
    $x = [int]($r.X + $r.Width / 2); $y = [int]($r.Y + $r.Height / 2)
    # Never click anything that isn't Camera Hub: the window under that exact point must belong to it.
    if ([CH]::AtPoint($x, $y) -ne [uint32]$proc.Id) { throw 'Another window is covering Camera Hub, so nothing was clicked.' }
    [void][CH]::SetCursorPos($x, $y); Start-Sleep -Milliseconds 120
    if ([CH]::AtPoint($x, $y) -ne [uint32]$proc.Id) { throw 'Another window is covering Camera Hub, so nothing was clicked.' }
    [CH]::mouse_event(2, 0, 0, 0, [IntPtr]::Zero); [CH]::mouse_event(4, 0, 0, 0, [IntPtr]::Zero); Start-Sleep -Milliseconds 400
  }
  function Toggled($e) { try { return $e.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Current.ToggleState -eq 'On' } catch { return $false } }

  # The main window, shown if Camera Hub is sitting in the tray.
  $main = $null; $mainH = $null
  foreach ($h in [CH]::Windows([uint32]$proc.Id)) {
    if ([CH]::Title($h) -ne 'Camera Hub') { continue }
    $w = $A::FromHandle($h)
    if (Find $w 'topBar.pb_prompter') { $main = $w; $mainH = $h; break }
  }
  if (-not $main) {
    foreach ($h in [CH]::Windows([uint32]$proc.Id)) {
      if ([CH]::Title($h) -ne 'Camera Hub') { continue }
      [void][CH]::ShowWindow($h, 5); Start-Sleep -Milliseconds 600
      $w = $A::FromHandle($h)
      if (Find $w 'topBar.pb_prompter') { $main = $w; $mainH = $h; break }
      [void][CH]::ShowWindow($h, 0)
    }
  }
  if (-not $main) { Out @{ok=$false; error="Camera Hub's main window couldn't be found."} }
  [void][CH]::ShowWindow($mainH, 9)
  if (-not (Front $mainH)) { Out @{ok=$false; error="Camera Hub couldn't be brought to the front, so nothing was clicked."} }

  $tab = Find $main 'topBar.pb_prompter'
  if (-not (Toggled $tab)) { Click $tab }
  $text = Find $main 'property_kPRPContent.EVHButtonGroup.EVHSegmentedImageButton' 'Text'
  if (-not $text) { Out @{ok=$false; error="Camera Hub's Content Text switch wasn't found."} }
  if (-not (Toggled $text)) { Click $text; Start-Sleep -Milliseconds 600 }

  $buttons = FindAll $main 'property_kPRPTextSources.QWidget.EVHImageButton'
  if ($buttons.Count -ne 2) { Out @{ok=$false; error="Camera Hub's Scripts + and - buttons weren't found."} }
  $plus = ($buttons | Sort-Object { $_.Current.BoundingRectangle.X })[0]
  Click $plus

  # The Add Script dialog.
  $dlg = $null
  for ($i = 0; $i -lt 20 -and -not $dlg; $i++) {
    foreach ($h in [CH]::Windows([uint32]$proc.Id)) { if ([CH]::Title($h) -like 'Add Script*') { $dlg = $A::FromHandle($h); $dlgH = $h } }
    if (-not $dlg) { Start-Sleep -Milliseconds 250 }
  }
  if (-not $dlg) { Out @{ok=$false; error="Camera Hub's Add Script box didn't open."} }
  if (-not (Front $dlgH)) { Out @{ok=$false; error="Camera Hub's Add Script box couldn't be brought to the front."} }
  $edit = $dlg.FindFirst($Tree::Descendants, (New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, [System.Windows.Automation.ControlType]::Edit)))
  if (-not $edit) { Out @{ok=$false; error="The script name box wasn't found."} }
  Click $edit
  try { $edit.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue($Name) } catch { [System.Windows.Forms.SendKeys]::SendWait(($Name -replace '([+^%~(){}\[\]])', '{$1}')) }
  Start-Sleep -Milliseconds 300
  $add = $dlg.FindFirst($Tree::Descendants, (New-Object System.Windows.Automation.PropertyCondition($A::NameProperty, 'Add')))
  if (-not $add) { Out @{ok=$false; error="The Add button wasn't found."} }
  Click $add
  Start-Sleep -Milliseconds 1200

  # The new script is selected and empty: wait until the list shows it selected and its editor has one empty part.
  $selected = $false
  for ($i = 0; $i -lt 20 -and -not $selected; $i++) {
    foreach ($e in (FindAll $main 'property_kPRPTextSources.QWidget.EVHTextSourcesTable')) {
      if ($e.Current.Name.TrimEnd([char]0) -eq $Name) {
        try { $selected = $e.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Current.IsSelected } catch { $selected = $true }
      }
    }
    if (-not $selected) { Start-Sleep -Milliseconds 300 }
  }
  if (-not $selected) { Out @{ok=$false; error="The new script didn't show as selected in Camera Hub's list."} }
  Start-Sleep -Milliseconds 600
  $parts = FindAll $main 'EVHPrompterEditorContainer.EVHChapterTextEdit'
  if ($parts.Count -ne 1) { Out @{ok=$false; error="The new script's editor has $($parts.Count) parts instead of one empty one, so nothing was pasted."} }
  $edit = $parts[0]

  # Paste into it the same way Josh would, only once the editor itself has the keyboard focus.
  $content = [System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8)
  Set-Clipboard -Value $content
  $focused = $false
  for ($i = 0; $i -lt 4 -and -not $focused; $i++) {
    if (-not (Front $mainH)) { continue }
    try { $edit.SetFocus() } catch {}
    Click $edit
    $f = [System.Windows.Automation.AutomationElement]::FocusedElement
    $focused = $f -and $f.Current.AutomationId -and $f.Current.AutomationId.Contains('EVHChapterTextEdit')
  }
  if (-not $focused) { Out @{ok=$false; error="Camera Hub's script editor wouldn't take the keyboard focus, so nothing was pasted."} }
  [CH]::keybd_event(0x11, 0, 0, [IntPtr]::Zero); [CH]::keybd_event(0x56, 0, 0, [IntPtr]::Zero)
  [CH]::keybd_event(0x56, 0, 2, [IntPtr]::Zero); [CH]::keybd_event(0x11, 0, 2, [IntPtr]::Zero)
  Start-Sleep -Milliseconds 1200
  $after = FindAll $main 'EVHPrompterEditorContainer.EVHChapterTextEdit'
  Out @{ok=$true; parts=$after.Count}
} catch {
  Out @{ok=$false; error=("Camera Hub control failed: " + $_.Exception.Message)}
}
