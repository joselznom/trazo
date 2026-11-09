# Experiment: every 5 s, the machine's load and loopback socket counts, to line up with
# the UTC start of each pass and the failures in its log.
param([string]$Out)
while ($true) {
  $tcp = Get-NetTCPConnection -ErrorAction SilentlyContinue
  $cpu = (Get-Counter '\Processor(_Total)\% Processor Time' -ErrorAction SilentlyContinue).CounterSamples.CookedValue
  $disk = (Get-Counter '\PhysicalDisk(_Total)\Avg. Disk sec/Transfer' -ErrorAction SilentlyContinue).CounterSamples.CookedValue
  $defender = (Get-Process MsMpEng -ErrorAction SilentlyContinue | Measure-Object CPU -Sum).Sum
  [pscustomobject]@{
    utc = (Get-Date).ToUniversalTime().ToString("o")
    cpu = [math]::Round($cpu, 1)
    disk_ms = [math]::Round($disk * 1000, 2)
    free_mb = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1024)
    time_wait = @($tcp | Where-Object State -eq 'TimeWait').Count
    established = @($tcp | Where-Object State -eq 'Established').Count
    chrome = @(Get-Process chrome -ErrorAction SilentlyContinue).Count
    defender_cpu_s = $defender
  } | ConvertTo-Json -Compress | Add-Content -Path $Out
  Start-Sleep -Seconds 5
}
