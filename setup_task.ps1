# setup_task.ps1
# Create a Windows Scheduled Task to run the investment commentary fetcher daily.
# Run this script as Administrator for best results.

$TaskName = "FeishuInvestmentPusher"
$ScriptDir = "C:\Users\zhiyutong\feishu-pusher"
$PythonExe = "C:\Users\zhiyutong\AppData\Local\Programs\Python\Python314\python.exe"
$FetcherScript = "$ScriptDir\fetcher.py"

# Verify files exist
if (-not (Test-Path $PythonExe)) {
    Write-Host "[ERROR] Python not found at: $PythonExe" -ForegroundColor Red
    Write-Host "Please update `$PythonExe to match your Python installation path."
    Write-Host "Run 'where python' to find the correct path."
    exit 1
}

if (-not (Test-Path $FetcherScript)) {
    Write-Host "[ERROR] fetcher.py not found at: $FetcherScript" -ForegroundColor Red
    exit 1
}

# Remove existing task if present
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing task '$TaskName'..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Create the scheduled task action
$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$FetcherScript`"" `
    -WorkingDirectory $ScriptDir

# Daily trigger at 10:00 AM
$Trigger = New-ScheduledTaskTrigger `
    -Daily `
    -At "10:00AM"

# Settings: stop if running longer than 5 minutes, don't restart too quickly
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -RestartInterval (New-TimeSpan -Minutes 10) `
    -RestartCount 3

# Run as current user
$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

# Register the task
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Daily fetch of BlackRock and HSBC investment commentary to Feishu"

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "  Task '$TaskName' created successfully!" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Task details:"
Write-Host "  Schedule:  Daily at 10:00 AM"
Write-Host "  Python:    $PythonExe"
Write-Host "  Script:    $FetcherScript"
Write-Host "  Run as:    $env:USERNAME"
Write-Host ""
Write-Host "To test manually, run:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host ""
Write-Host "To view task status:"
Write-Host "  Get-ScheduledTaskInfo -TaskName '$TaskName'"
Write-Host ""
Write-Host "To remove the task:"
Write-Host "  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
Write-Host ""
Write-Host "IMPORTANT: Make sure to fill in your Feishu webhook URL in:"
Write-Host "  $ScriptDir\config.json"
Write-Host ""
