# Mini Keyboard - SMTC oynatici hedefleme yardimcisi
#
# Windows'ta her medya oturumu (Spotify, tarayici sekmesi, tarayiciya kurulu
# bir PWA...) System Media Transport Controls'a kendi
# SourceAppUserModelId'siyle kaydolur. Bu kimlik uygulama yeniden acildiginda
# degismez, bu yuzden ayarlara "hedef oynatici" olarak kaydedilebilir.
#
#   -List                         acik oturumlari listeler (kimlik<TAB>durum<TAB>baslik)
#   -Command X -Target KIMLIK     yalnizca o oturuma komut gonderir
#   -Serve                        kalici mod: stdin'den satir satir komut okur
#                                 ("List" ya da "Cmd<TAB>Komut<TAB>Hedef"), her yanitin
#                                 sonuna "<0x1E>SON <cikis kodu>" satiri yazar
#
# -Target once tam kimlikle, bulunamazsa kimlik PARCASI olarak eslesir
# ("Chrome", "MSEdge", "_crx_<id>" gibi). -Target "*" Windows'un o an odaktaki
# oturumunu secer. Hedef oturum yoksa cikis kodu 3 olur; Python tarafi bu
# durumda komutu baska bir oynaticiya gondermez.
#
# SeekForward / SeekBack: oturumun zaman cizelgesinden su anki konumu alip
# 10 sn ileri/geri atlar (oynaticinin konum degistirmeyi desteklemesi gerekir).
#
# Not: WinRT projeksiyonlari yalnizca Windows PowerShell 5.1'de (powershell.exe)
# vardir; PowerShell 7 (pwsh) bu betigi calistiramaz.

param(
    [switch]$List,
    [switch]$Serve,
    [ValidateSet('PlayPause', 'Next', 'Previous', 'SeekForward', 'SeekBack')]
    [string]$Command,
    [string]$Target
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media.Control,ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties,Windows.Media.Control,ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionPlaybackStatus,Windows.Media.Control,ContentType=WindowsRuntime] | Out-Null

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

# Konsolda WinForms mesaj pompasi yok; Task.Wait burada kilitlenmez. Sure
# siniri, bir oturum yanit vermezse betigin asili kalmamasi icin.
function Await-Op($Operation, [Type]$ResultType) {
    $task = $asTaskGeneric.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    if (-not $task.Wait(3000)) { throw "WinRT zaman asimi" }
    return $task.Result
}

$mgr = Await-Op ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) `
    ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])

function Get-Status($s) {
    try { return [string]$s.GetPlaybackInfo().PlaybackStatus } catch { return 'Unknown' }
}

function Write-List {
    foreach ($s in @($mgr.GetSessions())) {
        $title = ''
        try {
            $props = Await-Op ($s.TryGetMediaPropertiesAsync()) `
                ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
            if ($props -and $props.Title) {
                $title = $props.Title
                if ($props.Artist) { $title = "$title - $($props.Artist)" }
            }
        } catch {}
        $title = ($title -replace "[`t`r`n]", ' ')
        [Console]::Out.WriteLine("$($s.SourceAppUserModelId)`t$(Get-Status $s)`t$title")
    }
}

# Cikis kodlari: 0 tamam, 2 eksik arguman, 3 hedef oturum yok, 4 oturum reddetti.
function Invoke-Target($Cmd, $Hedef) {
    if (-not $Cmd -or -not $Hedef) { return 2 }
    # Oturumlar her komutta tazelenir (oynatici acilip kapanmis olabilir); yonetici nesnesi kalicidir.
    $sessions = @($mgr.GetSessions())
    if ($Hedef -eq '*') {
        $cur = $mgr.GetCurrentSession()
        $hits = @(if ($cur) { $cur })
    } else {
        $hits = @($sessions | Where-Object { $_.SourceAppUserModelId -eq $Hedef })
        if ($hits.Count -eq 0) {
            $hits = @($sessions | Where-Object { $_.SourceAppUserModelId -like "*$Hedef*" })
        }
    }
    if ($hits.Count -eq 0) { return 3 }

    # Ayni uygulamanin birden fazla oturumu varsa calan tercih edilir.
    $playing = @($hits | Where-Object { (Get-Status $_) -eq 'Playing' })
    $session = if ($playing.Count -gt 0) { $playing[0] } else { $hits[0] }

    $op = switch ($Cmd) {
        'PlayPause' { $session.TryTogglePlayPauseAsync() }
        'Next'      { $session.TrySkipNextAsync() }
        'Previous'  { $session.TrySkipPreviousAsync() }
        { $_ -in 'SeekForward', 'SeekBack' } {
            $tl = $session.GetTimelineProperties()
            $pos = $tl.Position
            # Konum yalnizca LastUpdatedTime aninda gecerli; calarken o andan
            # beri gecen sure eklenir (zaman bilgisi yoksa eklenmez).
            if ((Get-Status $session) -eq 'Playing' -and $tl.LastUpdatedTime.Year -gt 2000) {
                $gecen = [DateTimeOffset]::Now - $tl.LastUpdatedTime
                if ($gecen -gt [TimeSpan]::Zero -and $gecen.TotalHours -lt 24) { $pos = $pos + $gecen }
            }
            $adim = [TimeSpan]::FromSeconds(10)
            $yeni = if ($_ -eq 'SeekForward') { $pos + $adim } else { $pos - $adim }
            if ($yeni -lt $tl.StartTime) { $yeni = $tl.StartTime }
            if ($tl.EndTime -gt $tl.StartTime -and $yeni -gt $tl.EndTime) { $yeni = $tl.EndTime }
            $session.TryChangePlaybackPositionAsync($yeni.Ticks)
        }
        default     { return 2 }
    }
    if (Await-Op $op ([bool])) { return 0 } else { return 4 }
}

if ($Serve) {
    # Kalici mod: PowerShell + WinRT yuklemesi (~250 ms) yalnizca bir kez odenir;
    # her medya tusu icin yeni surec baslatmak bunu her basista tekrarliyordu.
    [Console]::InputEncoding = [System.Text.Encoding]::UTF8
    $son = [char]0x1E + 'SON '
    while ($null -ne ($line = [Console]::In.ReadLine())) {
        $p = $line -split "`t"
        try {
            if ($p[0] -eq 'List') { Write-List; $rc = 0 }
            elseif ($p[0] -eq 'Cmd') { $rc = Invoke-Target $p[1] $p[2] }
            else { $rc = 2 }
        } catch { $rc = 1 }
        [Console]::Out.WriteLine("$son$rc")
        [Console]::Out.Flush()
    }
    exit 0
}

if ($List) { Write-List; exit 0 }
exit (Invoke-Target $Command $Target)
