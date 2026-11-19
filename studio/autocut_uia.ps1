<#
AutoCut UI engine for Tariq (Windows UI Automation, built into .NET, nothing installed).

It acts only on AutoCut's own window (process AutoCut.exe), finds controls by type and exact name, and uses each control's
own pattern (Invoke, Toggle, SelectionItem). It never clicks by position. If a match isn't unique, isn't enabled, or has no
pattern, it refuses and says why. Output is one JSON object on stdout.

  autocut_uia.ps1 snapshot
  autocut_uia.ps1 press  -Type Button   -Name "AutoCut Silences" [-Index 0]
  autocut_uia.ps1 toggle -Type CheckBox -Name "A2" -State off
  autocut_uia.ps1 select -Type RadioButton -Name "Remove silences"
#>
param(
    [Parameter(Position = 0, Mandatory = $true)][ValidateSet('snapshot', 'press', 'toggle', 'select', 'track', 'open', 'inspect_open')][string]$Command,
    [string]$Type = '',
    [string]$Name = '',
    [int]$Index = -1,
    [ValidateSet('', 'on', 'off')][string]$State = ''
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$A = [System.Windows.Automation.AutomationElement]

function Out-Json($o) { $o | ConvertTo-Json -Depth 6 -Compress; exit 0 }
function Fail($msg) { Out-Json @{ ok = $false; error = $msg } }

if ($Command -in @('open', 'inspect_open')) {
    # Premiere's Win32 menu bar is not consistently exposed in its UIA descendants.
    # Resolve the installed extension by exact menu labels and its current command ID.
    Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class PremiereNativeMenu {
 [DllImport("user32.dll")] public static extern IntPtr GetMenu(IntPtr hwnd);
 [DllImport("user32.dll")] public static extern IntPtr GetSubMenu(IntPtr menu, int index);
 [DllImport("user32.dll")] public static extern int GetMenuItemCount(IntPtr menu);
 [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetMenuString(IntPtr menu, uint item, StringBuilder text, int size, uint flags);
 [DllImport("user32.dll")] public static extern uint GetMenuItemID(IntPtr menu, int index);
 [DllImport("user32.dll")] public static extern uint GetMenuState(IntPtr menu, uint item, uint flags);
 [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hwnd, uint msg, IntPtr wp, IntPtr lp);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hwnd, uint msg, IntPtr wp, IntPtr lp);
}
"@
    $premiere = @(Get-Process 'Adobe Premiere Pro' -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 })
    if ($premiere.Count -ne 1) { Fail 'One Premiere window must be open before AutoCut can start.' }
    $hwnd = $premiere[0].MainWindowHandle
    $menu = [PremiereNativeMenu]::GetMenu($hwnd)
    if ($menu -eq [IntPtr]::Zero) { Fail 'Premiere has no native menu bar available.' }
    $route = @()
    foreach ($name in @('Window', 'Extensions', 'AutoCut')) {
        $matches = @()
        for ($i = 0; $i -lt [PremiereNativeMenu]::GetMenuItemCount($menu); $i++) {
            $text = New-Object System.Text.StringBuilder 512
            [void][PremiereNativeMenu]::GetMenuString($menu, $i, $text, 512, 0x400)
            $label = ($text.ToString().Replace('&', '') -split "`t")[0].Trim()
            if ($label -eq $name) { $matches += $i }
        }
        if ($matches.Count -ne 1) { Fail "Premiere menu '$name' was not uniquely available." }
        $index = $matches[0]
        $route += $name
        if ($name -ne 'AutoCut') {
            $menu = [PremiereNativeMenu]::GetSubMenu($menu, $index)
            if ($menu -eq [IntPtr]::Zero) { Fail "Premiere menu '$name' has no submenu." }
            if ($Command -eq 'open') { [void][PremiereNativeMenu]::SendMessage($hwnd, 0x117, $menu, [IntPtr]$index) }
        } else {
            $id = [PremiereNativeMenu]::GetMenuItemID($menu, $index)
            $menuState = [PremiereNativeMenu]::GetMenuState($menu, $index, 0x400)
            if ($id -eq [uint32]::MaxValue -or $id -eq 0 -or ($Command -eq 'open' -and ($menuState -band 3))) { Fail 'The installed AutoCut menu command is unavailable.' }
            if ($Command -eq 'open' -and -not [PremiereNativeMenu]::PostMessage($hwnd, 0x111, [IntPtr]([long]$id), [IntPtr]::Zero)) { Fail 'Premiere refused its AutoCut menu command.' }
            Out-Json @{ ok = $true; did = $Command; extension = 'AutoCut'; command_id = $id; route = $route; enabled = -not ($menuState -band 3) }
        }
    }
}

$procs = @(Get-Process AutoCut -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 })
if ($procs.Count -eq 0) { Fail 'AutoCut is not open. Open it from Premiere: Window > Extensions > AutoCut.' }
if ($procs.Count -gt 1) { Fail 'More than one AutoCut window is open.' }
$p = $procs[0]
$root = $A::FromHandle($p.MainWindowHandle)
$exe = $p.MainModule.FileName
$ver = (Get-Item $exe).VersionInfo.ProductVersion

function Kind($el) { $el.Current.ControlType.ProgrammaticName.Replace('ControlType.', '') }

function Keep($el) { $el.Current.Name -or (Kind $el) -match 'CheckBox|RadioButton|Edit|Spinner|Slider|ComboBox' }

function Describe($el) {
    $d = [ordered]@{ type = (Kind $el); name = $el.Current.Name; enabled = $el.Current.IsEnabled; offscreen = $el.Current.IsOffscreen }
    $o = $null
    if ($el.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$o)) { $d.toggle = $o.Current.ToggleState.ToString() }
    if ($el.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$o)) { $d.selected = $o.Current.IsSelected }
    if ($el.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$o)) { $d.value = $o.Current.Value }
    $d
}

$all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)

if ($Command -eq 'snapshot') {
    # an element can vanish while the page redraws; it is skipped rather than failing the whole read
    $items = foreach ($el in $all) { try { if (Keep $el) { Describe $el } } catch { } }
    $r = $root.Current.BoundingRectangle
    Out-Json @{ ok = $true; pid = $p.Id; exe = $exe; version = $ver; window = @{ x = $r.X; y = $r.Y; w = $r.Width; h = $r.Height; minimized = ($r.Width -le 0) }; items = @($items) }
}

if ($Command -eq 'track') {
    # AutoCut's track checkboxes have no names. Each one comes straight before its label (A1, A2...) in the tree.
    if ($Name -notmatch '^A\d+$' -or -not $State) { Fail 'Say -Name A1 (or A2...) and -State on or off.' }
    # in the same order as a snapshot: unnamed wrappers (groups, spans) sit between a box and its label in the full tree
    $list = @($all | Where-Object { try { Keep $_ } catch { $false } })
    $boxes = @()
    for ($i = 0; $i -lt $list.Count - 1; $i++) {
        if ((Kind $list[$i]) -eq 'CheckBox' -and (Kind $list[$i + 1]) -eq 'Text' -and $list[$i + 1].Current.Name -eq $Name) { $boxes += $list[$i] }
    }
    if ($boxes.Count -ne 1) { Fail "Found $($boxes.Count) checkboxes labelled $Name, so none was changed." }
    $o = $null
    if (-not $boxes[0].TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$o)) { Fail "$Name has no on/off state." }
    $want = if ($State -eq 'on') { 'On' } else { 'Off' }
    if ($o.Current.ToggleState.ToString() -ne $want) { $o.Toggle(); Start-Sleep -Milliseconds 300 }
    if ($o.Current.ToggleState.ToString() -ne $want) { Fail "$Name is still $($o.Current.ToggleState) after toggling." }
    Out-Json @{ ok = $true; did = 'track'; track = $Name; toggle = $want }
}

if (-not $Type -or -not $Name) { Fail 'Say which control: -Type and -Name.' }
$hits = @($all | Where-Object { try { (Kind $_) -eq $Type -and $_.Current.Name -eq $Name } catch { $false } })
if ($hits.Count -eq 0) { Fail "No $Type called '$Name' on AutoCut's screen now." }
if ($Index -ge 0) {
    if ($Index -ge $hits.Count) { Fail "Only $($hits.Count) $Type called '$Name'." }
    $el = $hits[$Index]
} else {
    if ($hits.Count -gt 1) { Fail "$($hits.Count) controls are called '$Name', so it isn't clear which one is meant. Pass -Index." }
    $el = $hits[0]
}
if (-not $el.Current.IsEnabled) { Fail "'$Name' is greyed out." }
$o = $null
switch ($Command) {
    'press' {
        if (-not $el.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$o)) { Fail "'$Name' can't be pressed through UI Automation, so it is left alone." }
        $o.Invoke()
        Out-Json @{ ok = $true; did = 'press'; target = @{ type = $Type; name = $Name } }
    }
    'toggle' {
        if (-not $State) { Fail 'Say -State on or off.' }
        if (-not $el.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$o)) { Fail "'$Name' has no on/off state through UI Automation." }
        $want = if ($State -eq 'on') { 'On' } else { 'Off' }
        if ($o.Current.ToggleState.ToString() -ne $want) { $o.Toggle(); Start-Sleep -Milliseconds 300 }
        $now = $o.Current.ToggleState.ToString()
        if ($now -ne $want) { Fail "'$Name' is still $now after toggling." }
        Out-Json @{ ok = $true; did = 'toggle'; target = @{ type = $Type; name = $Name } }
    }
    'select' {
        if ($el.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$o)) { $o.Select() }
        elseif ($el.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$o)) { $o.Invoke() }
        else { Fail "'$Name' can't be selected through UI Automation." }
        Out-Json @{ ok = $true; did = 'select'; target = @{ type = $Type; name = $Name } }
    }
}
